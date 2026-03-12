"""
SubdomainDeployer — deploy a website as a subdomain of the demo domain.

Each website gets its own subdomain: ``{website_name}.digitaldatatest.com``.
Uses the same shared demo load balancer but with **host-based** routing
instead of path-based routing.

Pre-requisites (one-time setup, NOT created by this deployer):
  - Wildcard SSL certificate (``*.digitaldatatest.com``) attached to the
    demo HTTPS proxy.
  - Wildcard DNS A record (``*.digitaldatatest.com`` → demo LB IP).

All operations are **idempotent**: resources are checked for existence before
creation, and host rules are only added if they do not already exist.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

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
    """Deploy a website as a subdomain on the shared demo load balancer.

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
        """Send a progress message through the log callback."""
        try:
            await self._log(message)
        except Exception:
            logger.warning("log_callback failed for message: %s", message)

    def _run_sync(self, func: Callable[..., Any], *args: Any) -> Any:
        """Run a blocking function in the default executor."""
        loop = asyncio.get_event_loop()
        return loop.run_in_executor(None, func, *args)

    # ─── public entry point ────────────────────────────────────────────

    async def deploy(self, website_name: str, main_html_file: str = "index.html") -> DeploymentResult:
        """Provision subdomain infrastructure for *website_name*.

        Returns a ``DeploymentResult`` with the public URL on success,
        or an error description on failure.
        """
        sname = safe_name(website_name)
        bucket_name = get_bucket_name(website_name, "subdomain")
        backend_bucket_name = get_backend_bucket_name(website_name, "subdomain")
        subdomain = f"{website_name}.{self._config.DEMO_DOMAIN}"

        await self._emit(
            f"[INFRA] Starting subdomain deployment for '{website_name}' "
            f"on {subdomain} (safe: {sname})"
        )

        try:
            # Step 1 — Storage bucket
            await self._ensure_storage_bucket(bucket_name, subdomain, main_html_file)

            # Step 2 — Backend bucket (CDN)
            await self._ensure_backend_bucket(backend_bucket_name, bucket_name)

            # Step 3 — Host rule on shared URL map
            await self._ensure_host_rule(
                website_name, sname, subdomain, backend_bucket_name,
            )

            # Step 4 — Ensure wildcard SSL cert is on the proxy
            await self._ensure_wildcard_ssl_on_proxy()

            url = f"https://{subdomain}/"
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

    async def delete(self, website_name: str) -> None:
        """Remove all subdomain infrastructure for *website_name*.

        Deletion order matters due to dependencies:
        1. Remove host rule from shared URL map
        2. Delete backend bucket (CDN)
        3. Delete storage bucket + all objects
        """
        sname = safe_name(website_name)
        bucket_name = get_bucket_name(website_name, "subdomain")
        backend_bucket_name = get_backend_bucket_name(website_name, "subdomain")
        subdomain = f"{website_name}.{self._config.DEMO_DOMAIN}"

        await self._emit(f"[DELETE] Starting subdomain cleanup for '{website_name}'")

        # 1. Remove host rule from shared URL map
        await self._remove_host_rule(website_name, sname, subdomain)

        # 2. Delete backend bucket
        await self._delete_backend_bucket(backend_bucket_name)

        # 3. Delete storage bucket + all objects
        await self._delete_storage_bucket(bucket_name)

        await self._emit(f"[DELETE] Subdomain cleanup complete for '{website_name}'")

    # =================================================================
    #  Step 1 — Storage Bucket
    # =================================================================

    async def _ensure_storage_bucket(
        self, bucket_name: str, subdomain: str, main_html_file: str = "index.html",
    ) -> None:
        """Create the Cloud Storage bucket if it does not already exist."""
        await self._emit(f"[INFRA] Checking storage bucket: {bucket_name}")

        def _create() -> None:
            try:
                self._storage_client.get_bucket(bucket_name)
                logger.info("Bucket %s already exists — skipping creation.", bucket_name)
                return
            except Exception:
                pass

            logger.info("Creating bucket %s ...", bucket_name)
            bucket = self._storage_client.bucket(bucket_name)
            bucket.iam_configuration.uniform_bucket_level_access_enabled = True
            bucket.versioning_enabled = False
            bucket.cors = [
                {
                    "origin": [
                        f"https://{subdomain}",
                        f"https://{self._config.DEMO_DOMAIN}",
                    ],
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

            # Website configuration — files at root (not in subdirectory)
            bucket.configure_website(
                main_page_suffix=main_html_file,
                not_found_page=main_html_file,
            )
            bucket.patch()

            # Public read access
            policy = bucket.get_iam_policy(requested_policy_version=3)
            policy.bindings.append(
                {
                    "role": "roles/storage.objectViewer",
                    "members": {"allUsers"},
                }
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
        """Create a Compute Engine backend bucket linked to the storage bucket."""
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
                "customResponseHeaders": [
                    "X-Content-Type-Options:nosniff",
                ],
            }

            operation = (
                self._compute.backendBuckets()
                .insert(project=self._project_id, body=body)
                .execute()
            )
            wait_for_global_operation(
                self._compute, self._project_id, operation["name"],
            )
            logger.info("Backend bucket %s created.", backend_bucket_name)

        await self._run_sync(_create)
        await self._emit(f"[INFRA] Backend bucket ready: {backend_bucket_name}")

    # =================================================================
    #  Step 3 — Host Rule on Shared URL Map
    # =================================================================

    async def _ensure_host_rule(
        self, website_name: str, sname: str, subdomain: str, backend_bucket_name: str,
    ) -> None:
        """Add a host rule for the subdomain to the shared demo URL map.

        Each subdomain gets its own hostRule + pathMatcher pointing to its
        backend bucket (similar to prod mode but on the demo LB).
        """
        url_map_name = self._config.DEMO_URL_MAP_NAME
        await self._emit(
            f"[INFRA] Adding host rule for '{subdomain}' to URL map '{url_map_name}'"
        )

        def _update() -> None:
            max_retries = 5
            base_delay = 5

            for attempt in range(1, max_retries + 1):
                try:
                    self._patch_url_map_host_rule(
                        url_map_name, sname, subdomain, backend_bucket_name,
                    )
                    return
                except api_errors.HttpError as err:
                    if err.resp.status == 400 and "resourceNotReady" in str(err):
                        if attempt < max_retries:
                            delay = base_delay * (2 ** (attempt - 1))
                            logger.warning(
                                "Backend bucket not ready (attempt %d/%d) — retrying in %ds...",
                                attempt, max_retries, delay,
                            )
                            time.sleep(delay)
                            continue
                    raise

        await self._run_sync(_update)
        await self._emit(f"[INFRA] Host rule added for '{subdomain}'")

    def _patch_url_map_host_rule(
        self, url_map_name: str, sname: str, subdomain: str, backend_bucket_name: str,
    ) -> None:
        """Fetch the URL map, add a host rule for the subdomain, and patch."""
        url_map = (
            self._compute.urlMaps()
            .get(project=self._project_id, urlMap=url_map_name)
            .execute()
        )

        # Check if the subdomain already has a host rule
        host_rules: list[dict] = url_map.get("hostRules", [])
        for hr in host_rules:
            if subdomain in hr.get("hosts", []):
                logger.info(
                    "Host rule for '%s' already exists — skipping.", subdomain,
                )
                return

        # Resolve the backend bucket self-link
        bb_resource = (
            self._compute.backendBuckets()
            .get(project=self._project_id, backendBucket=backend_bucket_name)
            .execute()
        )
        bb_self_link = bb_resource["selfLink"]

        # Create a unique path matcher name
        matcher_name = f"pm-sub-{sname}"

        # Add the new path matcher (defaultService = all paths go to this bucket)
        path_matchers: list[dict] = url_map.get("pathMatchers", [])
        # Remove existing matcher with same name if present (idempotent)
        path_matchers = [pm for pm in path_matchers if pm.get("name") != matcher_name]
        path_matchers.append({
            "name": matcher_name,
            "defaultService": bb_self_link,
        })
        url_map["pathMatchers"] = path_matchers

        # Add the new host rule
        host_rules.append({
            "hosts": [subdomain],
            "pathMatcher": matcher_name,
        })
        url_map["hostRules"] = host_rules

        # Patch the URL map
        operation = (
            self._compute.urlMaps()
            .patch(
                project=self._project_id,
                urlMap=url_map_name,
                body=url_map,
            )
            .execute()
        )
        wait_for_global_operation(
            self._compute, self._project_id, operation["name"],
        )
        logger.info(
            "URL map '%s' updated: host rule for '%s' -> %s",
            url_map_name, subdomain, backend_bucket_name,
        )

    # =================================================================
    #  Step 4 — Ensure Wildcard SSL on Proxy
    # =================================================================

    async def _ensure_wildcard_ssl_on_proxy(self) -> None:
        """Ensure the wildcard SSL certificate is attached to the demo HTTPS proxy.

        The wildcard cert (``*.digitaldatatest.com``) must already exist in GCP.
        This step only verifies it is attached to the proxy; it does NOT create
        the certificate (that is a one-time manual setup).
        """
        proxy_name = self._config.DEMO_HTTPS_PROXY_NAME
        cert_name = self._config.DEMO_WILDCARD_SSL_CERT_NAME

        await self._emit(
            f"[INFRA] Verifying wildcard SSL cert '{cert_name}' on proxy '{proxy_name}'"
        )

        def _check_and_attach() -> None:
            # Verify the certificate exists
            try:
                self._compute.sslCertificates().get(
                    project=self._project_id, sslCertificate=cert_name,
                ).execute()
            except api_errors.HttpError as err:
                if err.resp.status == 404:
                    logger.warning(
                        "Wildcard SSL cert '%s' not found — subdomain will work "
                        "only after the cert is created manually.", cert_name,
                    )
                    return
                raise

            # Check if already attached to the proxy
            proxy = (
                self._compute.targetHttpsProxies()
                .get(project=self._project_id, targetHttpsProxy=proxy_name)
                .execute()
            )
            current_certs: list[str] = proxy.get("sslCertificates", [])

            for cert_link in current_certs:
                if cert_link.endswith(f"/{cert_name}"):
                    logger.info("Wildcard cert already on proxy — ok.")
                    return

            # Attach it
            cert_link = (
                f"https://www.googleapis.com/compute/v1/projects/"
                f"{self._project_id}/global/sslCertificates/{cert_name}"
            )
            updated_certs = current_certs + [cert_link]

            operation = (
                self._compute.targetHttpsProxies()
                .setSslCertificates(
                    project=self._project_id,
                    targetHttpsProxy=proxy_name,
                    body={"sslCertificates": updated_certs},
                )
                .execute()
            )
            wait_for_global_operation(
                self._compute, self._project_id, operation["name"],
            )
            logger.info("Wildcard cert '%s' attached to proxy '%s'.", cert_name, proxy_name)

        await self._run_sync(_check_and_attach)
        await self._emit(f"[INFRA] Wildcard SSL verified on proxy")

    # =================================================================
    #  Delete helpers
    # =================================================================

    async def _remove_host_rule(
        self, website_name: str, sname: str, subdomain: str,
    ) -> None:
        """Remove the host rule and path matcher for the subdomain from the URL map."""
        url_map_name = self._config.DEMO_URL_MAP_NAME
        await self._emit(f"[DELETE] Removing host rule for {subdomain}")

        def _update() -> None:
            url_map = (
                self._compute.urlMaps()
                .get(project=self._project_id, urlMap=url_map_name)
                .execute()
            )

            matcher_name = f"pm-sub-{sname}"

            # Remove host rule
            host_rules = url_map.get("hostRules", [])
            url_map["hostRules"] = [
                hr for hr in host_rules
                if subdomain not in hr.get("hosts", [])
            ]

            # Remove path matcher
            path_matchers = url_map.get("pathMatchers", [])
            url_map["pathMatchers"] = [
                pm for pm in path_matchers
                if pm.get("name") != matcher_name
            ]

            operation = (
                self._compute.urlMaps()
                .patch(project=self._project_id, urlMap=url_map_name, body=url_map)
                .execute()
            )
            wait_for_global_operation(self._compute, self._project_id, operation["name"])
            logger.info("Removed host rule for %s from URL map.", subdomain)

        await self._run_sync(_update)
        await self._emit(f"[DELETE] Host rule removed for {subdomain}")

    async def _delete_backend_bucket(self, backend_bucket_name: str) -> None:
        """Delete the Compute Engine backend bucket."""
        await self._emit(f"[DELETE] Deleting backend bucket: {backend_bucket_name}")

        def _delete() -> None:
            try:
                operation = (
                    self._compute.backendBuckets()
                    .delete(project=self._project_id, backendBucket=backend_bucket_name)
                    .execute()
                )
                wait_for_global_operation(self._compute, self._project_id, operation["name"])
                logger.info("Backend bucket %s deleted.", backend_bucket_name)
            except api_errors.HttpError as err:
                if err.resp.status == 404:
                    logger.info("Backend bucket %s not found — already deleted.", backend_bucket_name)
                else:
                    raise

        await self._run_sync(_delete)

    async def _delete_storage_bucket(self, bucket_name: str) -> None:
        """Delete the storage bucket and all its objects."""
        await self._emit(f"[DELETE] Deleting storage bucket: {bucket_name}")

        def _delete() -> None:
            try:
                bucket = self._storage_client.get_bucket(bucket_name)
                blobs = list(bucket.list_blobs())
                if blobs:
                    bucket.delete_blobs(blobs)
                    logger.info("Deleted %d objects from bucket %s.", len(blobs), bucket_name)
                bucket.delete()
                logger.info("Storage bucket %s deleted.", bucket_name)
            except Exception as exc:
                if "404" in str(exc) or "NotFound" in str(exc):
                    logger.info("Bucket %s not found — already deleted.", bucket_name)
                else:
                    raise

        await self._run_sync(_delete)
