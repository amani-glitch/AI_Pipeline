"""
Cloud Run catalog — read the project's existing Cloud Run services.

WebDeploy can put a service it did *not* build behind the platform load
balancer and serve it on a real domain ("link an existing service"). That
needs three things this module provides:

* an inventory the deploy form can offer as a picker,
* resolution of whatever the user pasted (a ``.run.app`` URL, or a service
  name) to the ``(service, region)`` pair a serverless NEG needs,
* the invoker check: an external load balancer reaches a Cloud Run service as
  an anonymous caller, so the service must grant ``allUsers`` the invoker
  role or the domain answers 403.

Resolution matches against the live inventory rather than parsing URL shapes:
Cloud Run has had several (``{svc}-{project}.{region}.run.app`` today,
``{svc}-{hash}-{code}.a.run.app`` before it), and a service's own ``uri`` is
the authoritative answer for all of them. A service missing from the listing
is a service in another project, which a serverless NEG cannot target anyway.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from googleapiclient import discovery

from config import Settings
from infra.gcp_helpers import call_with_retries, get_credentials, is_not_found

logger = logging.getLogger("webdeploy.cloudrun.catalog")

# Ingress settings that let a global external load balancer reach the service.
LB_REACHABLE_INGRESS = {
    "INGRESS_TRAFFIC_ALL",
    "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER",
    "",  # unset — the API omits the field when it is the default (ALL)
}

INVOKER_ROLE = "roles/run.invoker"
PUBLIC_MEMBER = "allUsers"


class CloudRunLookupError(Exception):
    """The reference does not name a Cloud Run service in this project."""


@dataclass(frozen=True)
class RunService:
    """One Cloud Run service of the platform's project."""
    name: str          # service id, e.g. "my-api"
    region: str        # e.g. "europe-west1"
    url: str           # its own https://…run.app address ("" while deploying)
    ingress: str = ""
    updated_at: str = ""
    last_image: str = ""

    @property
    def resource(self) -> str:
        return self.region + "/" + self.name

    @property
    def lb_reachable(self) -> bool:
        return self.ingress in LB_REACHABLE_INGRESS

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "region": self.region,
            "url": self.url,
            "ingress": self.ingress or "INGRESS_TRAFFIC_ALL",
            "lb_reachable": self.lb_reachable,
            "updated_at": self.updated_at,
            "last_image": self.last_image,
        }


def _host(value: str) -> str:
    """Host part of whatever the user pasted — URL, bare host, or neither."""
    v = (value or "").strip().lower()
    v = re.sub(r"^[a-z]+://", "", v)
    v = v.split("/", 1)[0].split("?", 1)[0].split(":", 1)[0]
    return v.strip(".")


def looks_like_url(value: str) -> bool:
    return "." in _host(value)


def parse_run_host(value: str) -> tuple[str, str]:
    """Best-effort ``(service, region)`` read out of a ``.run.app`` hostname.

    Two shapes exist in the wild and both are still served:

    ``{service}-{project number}.{region}.run.app``   (current)
    ``{service}-{hash}-{region code}.a.run.app``      (older services)

    The older shape encodes the region as a two-letter code (``-ew``), which
    is not a region name, so only the service name comes back for those. This
    is a fallback for when a service's own ``uri`` no longer matches what the
    user pasted — the inventory remains the source of truth.
    """
    host = _host(value)
    if not host.endswith(".run.app"):
        return "", ""
    base = host[: -len(".run.app")].rstrip(".")
    if base.endswith(".a"):  # older: {service}-{hash}-{code}.a.run.app
        label = base[:-2]
        parts = label.rsplit("-", 2)
        return (parts[0] if len(parts) == 3 else label), ""
    label, _, region = base.rpartition(".")
    if not label:  # no region label at all — nothing reliable to read
        return base, ""
    service, _, tail = label.rpartition("-")
    if service and tail.isdigit():  # the trailing number is the project
        return service, region
    return label, region


class CloudRunCatalog:
    """Read-only view of the project's Cloud Run services (plus the one
    write the feature needs: granting public invoker access)."""

    def __init__(self, settings: Settings, credentials=None) -> None:
        self.s = settings
        self.project = settings.PROJECT_ID
        creds = credentials or get_credentials(settings.GOOGLE_APPLICATION_CREDENTIALS)
        self.run = discovery.build("run", "v2", credentials=creds, cache_discovery=False)

    # ── reads ─────────────────────────────────────────────────────────

    def _full_name(self, service: str, region: str) -> str:
        return f"projects/{self.project}/locations/{region}/services/{service}"

    @staticmethod
    def _from_api(body: dict) -> RunService:
        # name: projects/{p}/locations/{region}/services/{service}
        parts = (body.get("name") or "").split("/")
        region = parts[3] if len(parts) > 4 else ""
        service = parts[-1]
        containers = ((body.get("template") or {}).get("containers") or [])
        return RunService(
            name=service,
            region=region,
            url=body.get("uri") or "",
            ingress=body.get("ingress") or "",
            updated_at=body.get("updateTime") or "",
            last_image=(containers[0].get("image") if containers else "") or "",
        )

    def list_services(self) -> list[RunService]:
        """Every Cloud Run service in the project, across all regions.

        ``locations/-`` is Cloud Run's own wildcard, so this is one paged
        call rather than one call per region.
        """
        parent = f"projects/{self.project}/locations/-"
        services: list[RunService] = []
        page_token: Optional[str] = None
        while True:
            response = call_with_retries(
                lambda: self.run.projects().locations().services().list(
                    parent=parent, pageSize=200, pageToken=page_token,
                ).execute(),
                description="list Cloud Run services",
            )
            services.extend(self._from_api(body) for body in response.get("services", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        services.sort(key=lambda s: (s.name, s.region))
        return services

    def get_service(self, service: str, region: str) -> Optional[RunService]:
        try:
            body = call_with_retries(
                lambda: self.run.projects().locations().services().get(
                    name=self._full_name(service, region),
                ).execute(),
                description=f"get Cloud Run {service}",
            )
        except Exception as exc:
            if is_not_found(exc):
                return None
            raise
        return self._from_api(body)

    def resolve(self, reference: str, region: Optional[str] = None) -> RunService:
        """Turn ``reference`` into a service of this project.

        ``reference`` is a ``.run.app`` URL or a service name; ``region``
        disambiguates a name deployed to several regions.
        """
        reference = (reference or "").strip()
        if not reference:
            raise CloudRunLookupError("Indiquez l'URL ou le nom du service Cloud Run.")

        region = (region or "").strip() or None
        if not looks_like_url(reference) and region:
            # Exact coordinates — one API call, no listing.
            found = self.get_service(reference, region)
            if found is None:
                raise CloudRunLookupError(
                    f"Aucun service Cloud Run '{reference}' dans la région {region} "
                    f"du projet {self.project}."
                )
            return found

        services = self.list_services()
        wanted_name, wanted_region = reference, region or ""
        if looks_like_url(reference):
            host = _host(reference)
            for service in services:
                if service.url and _host(service.url) == host:
                    return service
            # The address the user has may be an alias the API no longer
            # reports as the service's own uri — read the name out of it and
            # look that up instead of giving up.
            wanted_name, parsed_region = parse_run_host(host)
            wanted_region = wanted_region or parsed_region
            if not wanted_name:
                raise CloudRunLookupError(
                    f"'{host}' n'est pas une adresse Cloud Run."
                )

        matches = [s for s in services if s.name == wanted_name]
        if wanted_region:
            matches = [s for s in matches if s.region == wanted_region] or matches
        if not matches:
            raise CloudRunLookupError(
                f"Aucun service Cloud Run nommé '{wanted_name}' dans le projet "
                f"{self.project}. Un service d'un autre projet ne peut pas être placé "
                "derrière ce load balancer."
            )
        if len(matches) > 1:
            regions = ", ".join(sorted(s.region for s in matches))
            raise CloudRunLookupError(
                f"'{wanted_name}' existe dans plusieurs régions ({regions}) — précisez laquelle."
            )
        return matches[0]

    # ── invoker access ────────────────────────────────────────────────

    def _get_iam_policy(self, service: str, region: str) -> dict:
        return call_with_retries(
            lambda: self.run.projects().locations().services().getIamPolicy(
                resource=self._full_name(service, region),
            ).execute(),
            description=f"read IAM policy of {service}",
        )

    def is_public(self, service: str, region: str) -> bool:
        """True when anonymous callers — the load balancer included — may
        invoke the service."""
        policy = self._get_iam_policy(service, region)
        return any(
            binding.get("role") == INVOKER_ROLE
            and PUBLIC_MEMBER in (binding.get("members") or [])
            for binding in policy.get("bindings", [])
        )

    def allow_unauthenticated(self, service: str, region: str) -> bool:
        """Grant ``allUsers`` the invoker role, preserving every other binding.

        Returns True when the policy was changed. The service belongs to the
        user, not to the platform, so this merges into the existing policy
        instead of replacing it the way the platform's own deployer does.
        """
        policy = self._get_iam_policy(service, region)
        bindings = [dict(b) for b in policy.get("bindings", [])]
        for binding in bindings:
            if binding.get("role") != INVOKER_ROLE:
                continue
            members = list(binding.get("members") or [])
            if PUBLIC_MEMBER in members:
                return False
            binding["members"] = members + [PUBLIC_MEMBER]
            break
        else:
            bindings.append({"role": INVOKER_ROLE, "members": [PUBLIC_MEMBER]})

        body = {"policy": {"bindings": bindings, "etag": policy.get("etag")}}
        call_with_retries(
            lambda: self.run.projects().locations().services().setIamPolicy(
                resource=self._full_name(service, region), body=body,
            ).execute(),
            description=f"grant public invoker on {service}",
        )
        logger.info("Granted %s %s on %s/%s", PUBLIC_MEMBER, INVOKER_ROLE, region, service)
        return True
