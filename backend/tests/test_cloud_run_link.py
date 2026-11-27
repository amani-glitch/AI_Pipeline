"""
Linking an existing Cloud Run service to a domain.

The two things that would hurt if they were wrong are covered here: resolving
whatever address the user pasted to the right service, and never planning the
deletion of a container the platform did not create.
"""

from __future__ import annotations

import pytest

from config import Settings
from infra import layout, teardown
from infra.layout import Placement, WebsiteRef
from models.deployment import DeploymentConfig
from models.enums import PIPELINE_STEPS, SOURCE_STEPS, DeploymentMode, PipelineStep
from services.cloud_run_catalog import (
    CloudRunCatalog,
    CloudRunLookupError,
    RunService,
    parse_run_host,
)


@pytest.fixture
def s():
    return Settings(PROJECT_ID="proj", _env_file=None)


def linked_ref(name="scripta-intelektual", domain="", service="scripta-intelektual",
               region="europe-west1"):
    return WebsiteRef(
        key=f"cloudrun:{name}", mode="cloudrun", website_name=name, domain=domain,
        placement=Placement.SITES, run_service=service, run_region=region,
        external_service=True,
    )


class FakeCatalog(CloudRunCatalog):
    """A catalog with a fixed inventory — no GCP, no credentials."""

    def __init__(self, services):
        self.s = Settings(PROJECT_ID="proj", _env_file=None)
        self.project = "proj"
        self.run = None
        self._services = list(services)

    def list_services(self):
        return list(self._services)

    def get_service(self, service, region):
        return next(
            (s for s in self._services if s.name == service and s.region == region), None,
        )


SCRIPTA = RunService(
    name="scripta-intelektual", region="europe-west1",
    url="https://scripta-intelektual-u5azdc2cvq-ew.a.run.app",
    ingress="INGRESS_TRAFFIC_ALL",
)
MODERN = RunService(
    name="webdeploy-frontend", region="europe-west1",
    url="https://webdeploy-frontend-215323664878.europe-west1.run.app",
)


# ── reading a .run.app address ───────────────────────────────────────────

def test_parse_run_host_reads_both_url_generations():
    # older services: {service}-{hash}-{region code}.a.run.app — the code is
    # not a region name, so only the service comes back
    assert parse_run_host("https://scripta-intelektual-u5azdc2cvq-ew.a.run.app") == \
        ("scripta-intelektual", "")
    # current: {service}-{project number}.{region}.run.app
    assert parse_run_host("https://webdeploy-frontend-215323664878.europe-west1.run.app/") == \
        ("webdeploy-frontend", "europe-west1")
    assert parse_run_host("https://example.com") == ("", "")


def test_resolve_matches_the_pasted_url_against_the_project():
    catalog = FakeCatalog([SCRIPTA, MODERN])
    assert catalog.resolve("https://scripta-intelektual-u5azdc2cvq-ew.a.run.app") == SCRIPTA
    assert catalog.resolve("scripta-intelektual") == SCRIPTA


def test_resolve_falls_back_to_the_name_in_an_outdated_url():
    """A service whose own uri has moved to the new form is still found from
    the old address someone has bookmarked."""
    renamed = RunService(
        name="scripta-intelektual", region="europe-west1",
        url="https://scripta-intelektual-215323664878.europe-west1.run.app",
    )
    catalog = FakeCatalog([renamed])
    assert catalog.resolve("https://scripta-intelektual-u5azdc2cvq-ew.a.run.app") == renamed


def test_resolve_refuses_a_service_from_another_project():
    catalog = FakeCatalog([SCRIPTA])
    with pytest.raises(CloudRunLookupError):
        catalog.resolve("https://someone-else-999999.us-central1.run.app")


def test_resolve_asks_for_a_region_when_the_name_is_ambiguous():
    twin = RunService(name="scripta-intelektual", region="us-central1", url="https://x-1.us-central1.run.app")
    catalog = FakeCatalog([SCRIPTA, twin])
    with pytest.raises(CloudRunLookupError, match="plusieurs régions"):
        catalog.resolve("scripta-intelektual")
    assert catalog.resolve("scripta-intelektual", "us-central1") == twin


def test_ingress_that_blocks_the_load_balancer_is_reported():
    assert SCRIPTA.lb_reachable
    assert RunService("a", "b", "", ingress="").lb_reachable  # unset means ALL
    assert not RunService("a", "b", "", ingress="INGRESS_TRAFFIC_INTERNAL_ONLY").lb_reachable


# ── the NEG follows the service's region ────────────────────────────────

def test_a_linked_service_keeps_its_own_name_and_region(s):
    names = layout.app_names("my-site", s, "scripta-intelektual", "us-central1")
    assert names.service == "scripta-intelektual"   # not derived from the website
    assert names.region == "us-central1"            # a NEG lives where its service does
    # the front door stays the platform's, named after the website
    assert names == layout.app_names("my-site", s, "scripta-intelektual", "us-central1")
    assert names.backend_service == layout.app_names("my-site", s).backend_service


# ── teardown never destroys a borrowed service ──────────────────────────

def test_deleting_a_linked_website_leaves_the_service_and_its_images(s):
    plan = teardown.plan_teardown(linked_ref(domain="scripta-intelektual.com"), s)
    actions = [st.action for st in plan]
    assert "run.delete_service" not in actions
    assert "ar.delete_package" not in actions
    # the front door the platform *did* create is still removed in full
    assert actions == [
        "url_map.remove_host_route",   # platform host
        "url_map.remove_host_route",   # custom domain
        "certmap.delete_entry", "certmap.delete_entry", "certmanager.delete_cert",
        "backend_service.delete", "neg.delete",
        "dns.cleanup_host", "dns.cleanup_host",
    ]


def test_a_linked_negs_region_is_the_services_not_the_default(s):
    plan = teardown.plan_teardown(linked_ref(region="us-central1"), s)
    neg = next(st for st in plan if st.action == "neg.delete")
    assert neg.params["region"] == "us-central1"
    assert s.CLOUDRUN_REGION != "us-central1"


def test_a_platform_built_website_still_deletes_its_service(s):
    ref = WebsiteRef("cloudrun:api", "cloudrun", "api", "", Placement.SITES)
    actions = [st.action for st in teardown.plan_teardown(ref, s)]
    assert "run.delete_service" in actions and "ar.delete_package" in actions


# ── the pipeline skips everything that needs sources ────────────────────

def test_a_linked_deployment_runs_only_domain_infra_and_notify():
    config = DeploymentConfig(
        mode=DeploymentMode.CLOUDRUN, website_name="scripta-intelektual",
        domain="scripta-intelektual.com", run_service="scripta-intelektual",
        run_region="europe-west1",
    )
    assert config.links_existing_service
    remaining = [step for step in PIPELINE_STEPS if step not in SOURCE_STEPS]
    assert remaining == [
        PipelineStep.DOMAIN_REGISTER, PipelineStep.INFRA, PipelineStep.NOTIFY,
    ]
    # an upload-based deployment keeps all of them
    assert not DeploymentConfig(
        mode=DeploymentMode.CLOUDRUN, website_name="x",
    ).links_existing_service
