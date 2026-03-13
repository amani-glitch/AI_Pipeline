"""
SubdomainDeployer — deploy a website on a subdomain of any domain.

Supports two scenarios:

**Case A — Internal subdomain (digitaldatatest.com)**:
    URL: ``{name}.digitaldatatest.com``
    Uses the shared demo load balancer, wildcard SSL, wildcard DNS.
    No user action needed after deployment.

**Case B — External subdomain (client-owned domain)**:
    URL: ``{name}.yourlocaleye.com``
    Uses the shared prod load balancer, individual SSL cert.
    User must add a CNAME record at their registrar:
        ``become-a-partner.yourlocaleye.com  CNAME  {prod-lb-ip}``

All operations are **idempotent**: resources are checked for existence before
creation, and host rules are only added if they do not already exist.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Optional

from google.cloud import storage as gcs
from googleapiclient import discovery, errors as api_errors

from config import Settings
from models.deployment import DeploymentResult
from infra.gcp_helpers import (
    get_backend_bucket_name,
    get_bucket_name,
    get_credentials,
    safe_name,
    wait_for_global_operation,
)

logger = logging.getLogger(__name__)


class SubdomainDeployer:
    """Deploy a website as a subdomain on a shared load balancer.

    Args:
        config: Application-wide settings (see ``config.Settings``).
        log_callback: An ``async`` callable ``(str) -> None`` used to stream
            progress messages back to the caller (e.g. WebSocket, DB log).
    """

    def __init__(self, config: Settings, log_callback: Callable) -> None:
        self._config = config
        self._log = log_callback

        # Authenticate
        self._credentials = get_credentials(config.GOOGLE_APPLICATION_CREDENTIALS)
        self._project_id = config.PROJECT_ID

        # API clients
        self._storage_client = gcs.Client(
            project=self._project_id,
            credentials=self._credentials,
        )
        self._compute = discovery.build(
            "compute", "v1", credentials=self._credentials, cache_discovery=False,
        )

    # ─── helpers ───────────────────────────────────────────────────────

    async def _emit(self, message: str) -> None:
        try:
            await self._log(message)
        except Exception:
            logger.warning("log_callback failed for message: %s", message)

    def _run_sync(self, func: Callable[..., Any], *args: Any) -> Any:
        loop = asyncio.get_event_loop()
        return loop.run_in_executor(None, func, *args)

    def _self_link(self, resource_type: str, name: str) -> str:
        return (
            f"https://www.googleapis.com/compute/v1/projects/"
            f"{self._project_id}/global/{resource_type}/{name}"
        )

    def _is_internal(self, parent_domain: Optional[str]) -> bool:
        """True if deploying on our own demo domain (wildcard SSL available)."""
        return (
            not parent_domain
            or parent_domain == self._config.DEMO_DOMAIN
        )

    # ─── public entry point ────────────────────────────────────────────

    async def deploy(
        self,
        website_name: str,
        parent_domain: Optional[str] = None,
        main_html_file: str = "index.html",
    ) -> DeploymentResult:
        """Provision subdomain infrastructure.

        Args:
            website_name: The subdomain label (e.g. ``"become-a-partner"``).
            parent_domain: The parent domain (e.g. ``"yourlocaleye.com"``).
                If None or equal to DEMO_DOMAIN, uses the demo LB (Case A).
                Otherwise uses the prod LB (Case B).
            main_html_file: The entry point HTML file.

        Returns:
            A ``DeploymentResult`` with the public URL on success.
        """
        internal = self._is_internal(parent_domain)
        effective_parent = self._config.DEMO_DOMAIN if internal else parent_domain
        fqdn = f"{website_name}.{effective_parent}"
        sname = safe_name(website_name)
        safe_fqdn = safe_name(fqdn)

        bucket_name = get_bucket_name(website_name, "subdomain")
        backend_bucket_name = get_backend_bucket_name(website_name, "subdomain")

        infra_label = "internal (demo LB)" if internal else f"external ({effective_parent}, prod LB)"
        await self._emit(
            f"[INFRA] Starting subdomain deployment for '{fqdn}' — {infra_label}"
        )

        try:
            # Step 1 — Storage bucket
            await self._ensure_storage_bucket(bucket_name, fqdn, main_html_file)

            # Step 2 — Backend bucket (CDN)
            await self._ensure_backend_bucket(backend_bucket_name, bucket_name)

            if internal:
                # Case A: demo LB + wildcard SSL
                await self._ensure_host_rule_on_demo(
                    website_name, sname, fqdn, backend_bucket_name,
                )
                await self._ensure_wildcard_ssl_on_proxy()
            else:
                # Case B: prod LB + individual SSL cert + CNAME instructions
                ip_address = await self._get_prod_ip()
                await self._ensure_host_rule_on_prod(
                    safe_fqdn, fqdn, backend_bucket_name,
                )
                ssl_cert_name = f"{safe_fqdn}-ssl-cert"
                if self._config.PROD_AUTO_CREATE_SSL_CERT:
                    await self._ensure_ssl_certificate(ssl_cert_name, fqdn)
                    await self._add_ssl_cert_to_prod_proxy(ssl_cert_name)

                await self._emit(
                    f"[INFRA] ACTION REQUISE : Chez votre registrar ({effective_parent}), "
                    f"ajoutez un enregistrement DNS :\n"
                    f"    Type: A\n"
                    f"    Nom: {website_name}\n"
                    f"    Valeur: {ip_address}\n"
                    f"    (ou CNAME vers {effective_parent} si un A record existe deja)"
                )

            url = f"https://{fqdn}/"
            await self._emit(f"[INFRA] Subdomain deployment complete: {url}")

            return DeploymentResult(
                mode="subdomain",
                website_name=website_name,
                success=True,
                url=url,
                storage_bucket=bucket_name,
                backend_bucket=backend_bucket_name,
                url_map_updated=True,
            )

        except Exception as exc:
            error_msg = f"Subdomain deployment failed: {exc}"
            logger.exception(error_msg)
            await self._emit(f"[INFRA] ERROR: {error_msg}")
            return DeploymentResult(
                mode="subdomain",
                website_name=website_name,
                success=False,
                error=error_msg,
                storage_bucket=bucket_name,
                backend_bucket=backend_bucket_name,
            )

    # ─── delete entry point ─────────────────────────────────────────────

    async def delete(
        self, website_name: str, parent_domain: Optional[str] = None,
    ) -> None:
        """Remove all subdomain infrastructure for *website_name*."""
        internal = self._is_internal(parent_domain)
        effective_parent = self._config.DEMO_DOMAIN if internal else parent_domain
        fqdn = f"{website_name}.{effective_parent}"
        sname = safe_name(website_name)
        safe_fqdn = safe_name(fqdn)

        bucket_name = get_bucket_name(website_name, "subdomain")
        backend_bucket_name = get_backend_bucket_name(website_name, "subdomain")

        await self._emit(f"[DELETE] Starting subdomain cleanup for '{fqdn}'")

        if internal:
            await self._remove_host_rule_from_demo(website_name, sname, fqdn)
        else:
            await self._remove_host_rule_from_prod(safe_fqdn, fqdn)

        await self._delete_backend_bucket(backend_bucket_name)
        await self._delete_storage_bucket(bucket_name)
        await self._emit(f"[DELETE] Subdomain cleanup complete for '{fqdn}'")

    # =================================================================
    #  Step 1 — Storage Bucket
    # =================================================================

    async def _ensure_storage_bucket(
        self, bucket_name: str, fqdn: str, main_html_file: str = "index.html",
    ) -> None:
        await self._emit(f"[INFRA] Checking storage bucket: {bucket_name}")

        def _create() -> None:
            try:
                self._storage_client.get_bucket(bucket_name)
                logger.info("Bucket %s already exists — skipping.", bucket_name)
                return
            except Exception:
                pass

            logger.info("Creating bucket %s ...", bucket_name)
            bucket = self._storage_client.bucket(bucket_name)
            bucket.iam_configuration.uniform_bucket_level_access_enabled = True
            bucket.versioning_enabled = False
            bucket.cors = [
                {
                    "origin": [f"https://{fqdn}"],
                    "method": ["GET", "HEAD", "OPTIONS"],
                    "responseHeader": [
                        "Content-Type",
                        "Access-Control-Allow-Origin",
                        "x-goog-meta-*",
                    ],
                    "maxAgeSeconds": self._config.BUCKET_CORS_MAX_AGE,
                }
            ]
            bucket.create(location=self._config.BUCKET_LOCATION)

            # Files at root — no subdirectory prefix
            bucket.configure_website(
                main_page_suffix=main_html_file,
                not_found_page=main_html_file,
            )
            bucket.patch()

            # Public read access
            policy = bucket.get_iam_policy(requested_policy_version=3)
            policy.bindings.append(
                {"role": "roles/storage.objectViewer", "members": {"allUsers"}}
            )
            bucket.set_iam_policy(policy)
            logger.info("Bucket %s created and configured.", bucket_name)

        await self._run_sync(_create)
        await self._emit(f"[INFRA] Storage bucket ready: {bucket_name}")

    # =================================================================
    #  Step 2 — Backend Bucket (CDN)
    # =================================================================

    async def _ensure_backend_bucket(
        self, backend_bucket_name: str, storage_bucket_name: str,
    ) -> None:
        await self._emit(f"[INFRA] Checking backend bucket: {backend_bucket_name}")

        def _create() -> None:
            try:
                self._compute.backendBuckets().get(
                    project=self._project_id, backendBucket=backend_bucket_name,
                ).execute()
                logger.info("Backend bucket %s already exists — skipping.", backend_bucket_name)
                return
            except api_errors.HttpError as err:
                if err.resp.status != 404:
                    raise

            body: dict[str, Any] = {
                "name": backend_bucket_name,
                "bucketName": storage_bucket_name,
                "enableCdn": True,
                "cdnPolicy": {
                    "cacheMode": "CACHE_ALL_STATIC",
                    "defaultTtl": self._config.CDN_DEFAULT_TTL,
                    "maxTtl": self._config.CDN_MAX_TTL,
                    "clientTtl": self._config.CDN_CLIENT_TTL,
                    "negativeCaching": self._config.CDN_NEGATIVE_CACHING,
                    "negativeCachingPolicy": [
                        {"code": 404, "ttl": self._config.CDN_NEGATIVE_CACHING_TTL},
                        {"code": 410, "ttl": self._config.CDN_NEGATIVE_CACHING_TTL},
                    ],
                },
                "compressionMode": "AUTOMATIC",
                "customResponseHeaders": ["X-Content-Type-Options:nosniff"],
            }

            operation = (
                self._compute.backendBuckets()
                .insert(project=self._project_id, body=body)
                .execute()
            )
            wait_for_global_operation(self._compute, self._project_id, operation["name"])
            logger.info("Backend bucket %s created.", backend_bucket_name)

        await self._run_sync(_create)
        await self._emit(f"[INFRA] Backend bucket ready: {backend_bucket_name}")

    # =================================================================
    #  Case A — Demo LB (internal subdomain)
    # =================================================================

    async def _ensure_host_rule_on_demo(
        self, website_name: str, sname: str, fqdn: str, backend_bucket_name: str,
    ) -> None:
        url_map_name = self._config.DEMO_URL_MAP_NAME
        await self._emit(f"[INFRA] Adding host rule for '{fqdn}' to demo URL map")

        def _update() -> None:
            for attempt in range(1, 6):
                try:
                    self._patch_url_map_host_rule(
                        url_map_name, f"pm-sub-{sname}", fqdn, backend_bucket_name,
                    )
                    return
                except api_errors.HttpError as err:
                    if err.resp.status == 400 and "resourceNotReady" in str(err) and attempt < 5:
                        delay = 5 * (2 ** (attempt - 1))
                        logger.warning("Backend not ready (%d/5) — retry in %ds", attempt, delay)
                        time.sleep(delay)
                        continue
                    raise

        await self._run_sync(_update)
        await self._emit(f"[INFRA] Host rule added for '{fqdn}' on demo LB")

    async def _ensure_wildcard_ssl_on_proxy(self) -> None:
        proxy_name = self._config.DEMO_HTTPS_PROXY_NAME
        cert_name = self._config.DEMO_WILDCARD_SSL_CERT_NAME
        await self._emit(f"[INFRA] Verifying wildcard SSL on demo proxy")

        def _check() -> None:
            try:
                self._compute.sslCertificates().get(
                    project=self._project_id, sslCertificate=cert_name,
                ).execute()
            except api_errors.HttpError as err:
                if err.resp.status == 404:
                    logger.warning("Wildcard cert '%s' not found — create it manually.", cert_name)
                    return
                raise

            proxy = (
                self._compute.targetHttpsProxies()
                .get(project=self._project_id, targetHttpsProxy=proxy_name)
                .execute()
            )
            current_certs = proxy.get("sslCertificates", [])
            if any(c.endswith(f"/{cert_name}") for c in current_certs):
                logger.info("Wildcard cert already on proxy.")
                return

            updated = current_certs + [self._self_link("sslCertificates", cert_name)]
            operation = (
                self._compute.targetHttpsProxies()
                .setSslCertificates(
                    project=self._project_id,
                    targetHttpsProxy=proxy_name,
                    body={"sslCertificates": updated},
                )
                .execute()
            )
            wait_for_global_operation(self._compute, self._project_id, operation["name"])
            logger.info("Wildcard cert attached to demo proxy.")

        await self._run_sync(_check)

    # =================================================================
    #  Case B — Prod LB (external subdomain)
    # =================================================================

    async def _get_prod_ip(self) -> str:
        ip_name = self._config.PROD_GLOBAL_IP_NAME
        await self._emit(f"[INFRA] Retrieving prod LB IP: {ip_name}")

        def _get() -> str:
            result = (
                self._compute.globalAddresses()
                .get(project=self._project_id, address=ip_name)
                .execute()
            )
            return result["address"]

        ip = await self._run_sync(_get)
        await self._emit(f"[INFRA] Prod LB IP: {ip}")
        return ip

    async def _ensure_host_rule_on_prod(
        self, safe_fqdn: str, fqdn: str, backend_bucket_name: str,
    ) -> None:
        url_map_name = self._config.PROD_URL_MAP_NAME
        await self._emit(f"[INFRA] Adding host rule for '{fqdn}' to prod URL map")

        def _update() -> None:
            for attempt in range(1, 6):
                try:
                    self._patch_url_map_host_rule(
                        url_map_name, f"pm-{safe_fqdn}", fqdn, backend_bucket_name,
                    )
                    return
                except api_errors.HttpError as err:
                    if err.resp.status == 400 and "resourceNotReady" in str(err) and attempt < 5:
                        delay = 5 * (2 ** (attempt - 1))
                        logger.warning("Backend not ready (%d/5) — retry in %ds", attempt, delay)
                        time.sleep(delay)
                        continue
                    raise

        await self._run_sync(_update)
        await self._emit(f"[INFRA] Host rule added for '{fqdn}' on prod LB")

    async def _ensure_ssl_certificate(self, ssl_cert_name: str, fqdn: str) -> None:
        await self._emit(f"[INFRA] Checking SSL certificate: {ssl_cert_name}")

        def _create() -> None:
            try:
                self._compute.sslCertificates().get(
                    project=self._project_id, sslCertificate=ssl_cert_name,
                ).execute()
                logger.info("SSL cert %s already exists.", ssl_cert_name)
                return
            except api_errors.HttpError as err:
                if err.resp.status != 404:
                    raise

            operation = (
                self._compute.sslCertificates()
                .insert(
                    project=self._project_id,
                    body={
                        "name": ssl_cert_name,
                        "type": "MANAGED",
                        "managed": {"domains": [fqdn]},
                    },
                )
                .execute()
            )
            wait_for_global_operation(self._compute, self._project_id, operation["name"])
            logger.info("SSL cert %s created for %s.", ssl_cert_name, fqdn)

        await self._run_sync(_create)
        await self._emit(
            f"[INFRA] SSL certificate ready: {ssl_cert_name} "
            f"(provisioning may take up to 24h — requires DNS to point to our IP)"
        )

    async def _add_ssl_cert_to_prod_proxy(self, ssl_cert_name: str) -> None:
        proxy_name = self._config.PROD_HTTPS_PROXY_NAME
        await self._emit(f"[INFRA] Attaching SSL cert to prod proxy")

        def _update() -> None:
            proxy = (
                self._compute.targetHttpsProxies()
                .get(project=self._project_id, targetHttpsProxy=proxy_name)
                .execute()
            )
            current_certs = proxy.get("sslCertificates", [])
            if any(c.endswith(f"/{ssl_cert_name}") for c in current_certs):
                logger.info("SSL cert %s already on prod proxy.", ssl_cert_name)
                return

            updated = current_certs + [self._self_link("sslCertificates", ssl_cert_name)]
            operation = (
                self._compute.targetHttpsProxies()
                .setSslCertificates(
                    project=self._project_id,
                    targetHttpsProxy=proxy_name,
                    body={"sslCertificates": updated},
                )
                .execute()
            )
            wait_for_global_operation(self._compute, self._project_id, operation["name"])
            logger.info("SSL cert %s attached to prod proxy.", ssl_cert_name)

        await self._run_sync(_update)

    # =================================================================
    #  Shared URL Map patch logic
    # =================================================================

    def _patch_url_map_host_rule(
        self, url_map_name: str, matcher_name: str, fqdn: str, backend_bucket_name: str,
    ) -> None:
        """Add a host rule + path matcher for the FQDN to a URL map."""
        url_map = (
            self._compute.urlMaps()
            .get(project=self._project_id, urlMap=url_map_name)
            .execute()
        )

        # Check if host rule already exists
        host_rules: list[dict] = url_map.get("hostRules", [])
        for hr in host_rules:
            if fqdn in hr.get("hosts", []):
                logger.info("Host rule for '%s' already exists — skipping.", fqdn)
                return

        # Resolve backend bucket self-link
        bb_resource = (
            self._compute.backendBuckets()
            .get(project=self._project_id, backendBucket=backend_bucket_name)
            .execute()
        )
        bb_self_link = bb_resource["selfLink"]

        # Add path matcher (replace if same name exists for idempotency)
        path_matchers: list[dict] = url_map.get("pathMatchers", [])
        path_matchers = [pm for pm in path_matchers if pm.get("name") != matcher_name]
        path_matchers.append({
            "name": matcher_name,
            "defaultService": bb_self_link,
        })
        url_map["pathMatchers"] = path_matchers

        # Add host rule
        host_rules.append({
            "hosts": [fqdn],
            "pathMatcher": matcher_name,
        })
        url_map["hostRules"] = host_rules

        # Patch
        operation = (
            self._compute.urlMaps()
            .patch(project=self._project_id, urlMap=url_map_name, body=url_map)
            .execute()
        )
        wait_for_global_operation(self._compute, self._project_id, operation["name"])
        logger.info("URL map '%s': host rule for '%s' -> %s", url_map_name, fqdn, backend_bucket_name)

    # =================================================================
    #  Delete helpers
    # =================================================================

    async def _remove_host_rule_from_demo(
        self, website_name: str, sname: str, fqdn: str,
    ) -> None:
        await self._remove_host_rule(
            self._config.DEMO_URL_MAP_NAME, f"pm-sub-{sname}", fqdn,
        )

    async def _remove_host_rule_from_prod(self, safe_fqdn: str, fqdn: str) -> None:
        await self._remove_host_rule(
            self._config.PROD_URL_MAP_NAME, f"pm-{safe_fqdn}", fqdn,
        )

    async def _remove_host_rule(
        self, url_map_name: str, matcher_name: str, fqdn: str,
    ) -> None:
        await self._emit(f"[DELETE] Removing host rule for {fqdn}")

        def _update() -> None:
            url_map = (
                self._compute.urlMaps()
                .get(project=self._project_id, urlMap=url_map_name)
                .execute()
            )

            url_map["hostRules"] = [
                hr for hr in url_map.get("hostRules", [])
                if fqdn not in hr.get("hosts", [])
            ]
            url_map["pathMatchers"] = [
                pm for pm in url_map.get("pathMatchers", [])
                if pm.get("name") != matcher_name
            ]

            operation = (
                self._compute.urlMaps()
                .patch(project=self._project_id, urlMap=url_map_name, body=url_map)
                .execute()
            )
            wait_for_global_operation(self._compute, self._project_id, operation["name"])
            logger.info("Removed host rule for %s from URL map %s.", fqdn, url_map_name)

        await self._run_sync(_update)

    async def _delete_backend_bucket(self, backend_bucket_name: str) -> None:
        await self._emit(f"[DELETE] Deleting backend bucket: {backend_bucket_name}")

        def _delete() -> None:
            try:
                operation = (
                    self._compute.backendBuckets()
                    .delete(project=self._project_id, backendBucket=backend_bucket_name)
                    .execute()
                )
                wait_for_global_operation(self._compute, self._project_id, operation["name"])
            except api_errors.HttpError as err:
                if err.resp.status == 404:
                    logger.info("Backend bucket %s already deleted.", backend_bucket_name)
                else:
                    raise

        await self._run_sync(_delete)

    async def _delete_storage_bucket(self, bucket_name: str) -> None:
        await self._emit(f"[DELETE] Deleting storage bucket: {bucket_name}")

        def _delete() -> None:
            try:
                bucket = self._storage_client.get_bucket(bucket_name)
                blobs = list(bucket.list_blobs())
                if blobs:
                    bucket.delete_blobs(blobs)
                bucket.delete()
            except Exception as exc:
                if "404" in str(exc) or "NotFound" in str(exc):
                    logger.info("Bucket %s already deleted.", bucket_name)
                else:
                    raise

        await self._run_sync(_delete)
