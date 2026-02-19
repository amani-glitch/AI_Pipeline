"""
ProdDeployer — full production deployment with dedicated GCP infrastructure.

Unlike the demo deployer (which shares a single load balancer), the production
deployer provisions a **complete, isolated** stack for each domain:

    Static IP  ->  Forwarding Rules  ->  Target Proxies  ->  URL Map
                                             |
                                     SSL Certificate (optional)
                                             |
                                      Backend Bucket (CDN)
                                             |
                                      Cloud Storage Bucket
                                             |
                                      Cloud DNS Zone (optional)

All operations are **idempotent**: every resource is checked for existence
before creation.  Re-running a deployment that partially succeeded will pick
up where it left off.
"""

from __future__ import annotations

import asyncio
import logging
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


class ProdDeployer:
    """Provision dedicated production infrastructure for a custom domain.

    Args:
        config: Application-wide settings (see ``config.Settings``).
        log_callback: An ``async`` callable ``(str) -> None`` used to stream
            progress messages back to the caller.
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
        self._dns = discovery.build(
            "dns", "v1", credentials=self._credentials, cache_discovery=False,
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

    def _self_link(self, resource_type: str, name: str) -> str:
        """Build the full self-link for a global compute resource."""
        return (
            f"https://www.googleapis.com/compute/v1/projects/"
            f"{self._project_id}/global/{resource_type}/{name}"
        )

    # ─── public entry point ────────────────────────────────────────────

    async def deploy(self, website_name: str, domain: str) -> DeploymentResult:
        """Provision dedicated production infrastructure for *domain*.

        Returns a ``DeploymentResult`` with the public URL on success,
        or an error description on failure.
        """
        safe_domain = safe_name(domain)
        bucket_name = get_bucket_name(domain, "prod")
        backend_bucket_name = get_backend_bucket_name(domain, "prod")

        await self._emit(
            f"[INFRA] Starting production deployment for '{website_name}' "
            f"on domain '{domain}' (safe: {safe_domain})"
        )

        try:
            # Step 1 — Reserve static IP
            ip_address = await self._ensure_static_ip(safe_domain)

            # Step 2 — Storage bucket
            await self._ensure_storage_bucket(bucket_name, domain)

            # Step 3 — Backend bucket (CDN)
            await self._ensure_backend_bucket(backend_bucket_name, bucket_name)

            # Step 4 — URL map
            url_map_name = f"{safe_domain}-url-map"
            await self._ensure_url_map(url_map_name, backend_bucket_name, domain)

            # Step 5 — SSL certificate (optional)
            ssl_cert_name: str | None = None
            if self._config.PROD_AUTO_CREATE_SSL_CERT:
                ssl_cert_name = f"{safe_domain}-ssl-cert"
                await self._ensure_ssl_certificate(ssl_cert_name, domain)

            # Step 6 — HTTPS target proxy (if SSL)
            if ssl_cert_name:
                https_proxy_name = f"{safe_domain}-https-proxy"
                await self._ensure_https_target_proxy(
                    https_proxy_name, url_map_name, ssl_cert_name,
                )

            # Step 7 — HTTP target proxy
            http_proxy_name = f"{safe_domain}-http-proxy"
            await self._ensure_http_target_proxy(http_proxy_name, url_map_name)

            # Step 8 — Forwarding rules
            ip_name = f"{safe_domain}-ip"
            if ssl_cert_name:
                await self._ensure_forwarding_rule(
                    name=f"{safe_domain}-https-rule",
                    ip_name=ip_name,
                    target_proxy_name=https_proxy_name,
                    target_proxy_type="targetHttpsProxies",
                    port="443",
                )
            await self._ensure_forwarding_rule(
                name=f"{safe_domain}-http-rule",
                ip_name=ip_name,
                target_proxy_name=http_proxy_name,
                target_proxy_type="targetHttpProxies",
                port="80",
            )

            # Step 9 — DNS zone (optional)
            if self._config.PROD_AUTO_CREATE_DNS_ZONE:
                await self._ensure_dns_zone(safe_domain, domain, ip_address)

            url = f"https://{domain}/"
            await self._emit(f"[INFRA] Production deployment complete: {url}")

            return DeploymentResult(
                mode="prod",
                website_name=website_name,
                success=True,
                url=url,
                storage_bucket=bucket_name,
                backend_bucket=backend_bucket_name,
                url_map_updated=True,
            )

        except Exception as exc:
            error_msg = f"Production deployment failed: {exc}"
            logger.exception(error_msg)
            await self._emit(f"[INFRA] ERROR: {error_msg}")
            return DeploymentResult(
                mode="prod",
                website_name=website_name,
                success=False,
                error=error_msg,
                storage_bucket=bucket_name,
                backend_bucket=backend_bucket_name,
            )

    # =================================================================
    #  Step 1 — Static IP
    # =================================================================

    async def _ensure_static_ip(self, safe_domain: str) -> str:
        """Reserve a global static IP address and return the IP string."""
        ip_name = f"{safe_domain}-ip"
        await self._emit(f"[INFRA] Checking static IP: {ip_name}")

        def _create() -> str:
            # Check existence
            try:
                existing = (
                    self._compute.globalAddresses()
                    .get(project=self._project_id, address=ip_name)
                    .execute()
                )
                ip = existing["address"]
                logger.info("Static IP %s already exists (%s) — skipping.", ip_name, ip)
                return ip
            except api_errors.HttpError as err:
                if err.resp.status != 404:
                    raise

            body: dict[str, Any] = {
                "name": ip_name,
                "ipVersion": "IPV4",
            }
            operation = (
                self._compute.globalAddresses()
                .insert(project=self._project_id, body=body)
                .execute()
            )
            wait_for_global_operation(
                self._compute, self._project_id, operation["name"],
            )

            # Retrieve the allocated IP
            result = (
                self._compute.globalAddresses()
                .get(project=self._project_id, address=ip_name)
                .execute()
            )
            ip = result["address"]
            logger.info("Static IP %s reserved: %s", ip_name, ip)
            return ip

        ip_address: str = await self._run_sync(_create)
        await self._emit(f"[INFRA] Static IP ready: {ip_name} -> {ip_address}")
        return ip_address

    # =================================================================
    #  Step 2 — Storage Bucket
    # =================================================================

    async def _ensure_storage_bucket(self, bucket_name: str, domain: str) -> None:
        """Create the Cloud Storage bucket for the production site."""
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
                    "origin": [f"https://{domain}"],
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

            # SPA website config
            bucket.configure_website(
                main_page_suffix="index.html",
                not_found_page="index.html",
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
    #  Step 3 — Backend Bucket (CDN)
    # =================================================================

    async def _ensure_backend_bucket(
        self, backend_bucket_name: str, storage_bucket_name: str,
    ) -> None:
        """Create a Compute Engine backend bucket with CDN."""
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
    #  Step 4 — URL Map
    # =================================================================

    async def _ensure_url_map(
        self, url_map_name: str, backend_bucket_name: str, domain: str,
    ) -> None:
        """Create a URL map with the backend bucket as default service."""
        await self._emit(f"[INFRA] Checking URL map: {url_map_name}")

        def _create() -> None:
            try:
                self._compute.urlMaps().get(
                    project=self._project_id, urlMap=url_map_name,
                ).execute()
                logger.info("URL map %s already exists — skipping.", url_map_name)
                return
            except api_errors.HttpError as err:
                if err.resp.status != 404:
                    raise

            bb_self_link = self._self_link("backendBuckets", backend_bucket_name)

            body: dict[str, Any] = {
                "name": url_map_name,
                "defaultService": bb_self_link,
                "hostRules": [
                    {
                        "hosts": [domain],
                        "pathMatcher": "path-matcher-1",
                    }
                ],
                "pathMatchers": [
                    {
                        "name": "path-matcher-1",
                        "defaultService": bb_self_link,
                    }
                ],
            }

            operation = (
                self._compute.urlMaps()
                .insert(project=self._project_id, body=body)
                .execute()
            )
            wait_for_global_operation(
                self._compute, self._project_id, operation["name"],
            )
            logger.info("URL map %s created.", url_map_name)

        await self._run_sync(_create)
        await self._emit(f"[INFRA] URL map ready: {url_map_name}")

    # =================================================================
    #  Step 5 — SSL Certificate
    # =================================================================

    async def _ensure_ssl_certificate(
        self, ssl_cert_name: str, domain: str,
    ) -> None:
        """Create a Google-managed SSL certificate for the domain."""
        await self._emit(f"[INFRA] Checking SSL certificate: {ssl_cert_name}")

        def _create() -> None:
            try:
                self._compute.sslCertificates().get(
                    project=self._project_id, sslCertificate=ssl_cert_name,
                ).execute()
                logger.info("SSL certificate %s already exists — skipping.", ssl_cert_name)
                return
            except api_errors.HttpError as err:
                if err.resp.status != 404:
                    raise

            body: dict[str, Any] = {
                "name": ssl_cert_name,
                "type": "MANAGED",
                "managed": {
                    "domains": [domain],
                },
            }

            operation = (
                self._compute.sslCertificates()
                .insert(project=self._project_id, body=body)
                .execute()
            )
            wait_for_global_operation(
                self._compute, self._project_id, operation["name"],
            )
            logger.info("SSL certificate %s created (provisioning may take minutes).", ssl_cert_name)

        await self._run_sync(_create)
        await self._emit(
            f"[INFRA] SSL certificate ready: {ssl_cert_name} "
            f"(note: provisioning by Google may take up to 24 hours)"
        )

    # =================================================================
    #  Step 6 — HTTPS Target Proxy
    # =================================================================

    async def _ensure_https_target_proxy(
        self, proxy_name: str, url_map_name: str, ssl_cert_name: str,
    ) -> None:
        """Create a global HTTPS target proxy."""
        await self._emit(f"[INFRA] Checking HTTPS target proxy: {proxy_name}")

        def _create() -> None:
            try:
                self._compute.targetHttpsProxies().get(
                    project=self._project_id, targetHttpsProxy=proxy_name,
                ).execute()
                logger.info("HTTPS proxy %s already exists — skipping.", proxy_name)
                return
            except api_errors.HttpError as err:
                if err.resp.status != 404:
                    raise

            body: dict[str, Any] = {
                "name": proxy_name,
                "urlMap": self._self_link("urlMaps", url_map_name),
                "sslCertificates": [
                    self._self_link("sslCertificates", ssl_cert_name),
                ],
            }

            operation = (
                self._compute.targetHttpsProxies()
                .insert(project=self._project_id, body=body)
                .execute()
            )
            wait_for_global_operation(
                self._compute, self._project_id, operation["name"],
            )
            logger.info("HTTPS target proxy %s created.", proxy_name)

        await self._run_sync(_create)
        await self._emit(f"[INFRA] HTTPS target proxy ready: {proxy_name}")

    # =================================================================
    #  Step 7 — HTTP Target Proxy
    # =================================================================

    async def _ensure_http_target_proxy(
        self, proxy_name: str, url_map_name: str,
    ) -> None:
        """Create a global HTTP target proxy."""
        await self._emit(f"[INFRA] Checking HTTP target proxy: {proxy_name}")

        def _create() -> None:
            try:
                self._compute.targetHttpProxies().get(
                    project=self._project_id, targetHttpProxy=proxy_name,
                ).execute()
                logger.info("HTTP proxy %s already exists — skipping.", proxy_name)
                return
            except api_errors.HttpError as err:
                if err.resp.status != 404:
                    raise

            body: dict[str, Any] = {
                "name": proxy_name,
                "urlMap": self._self_link("urlMaps", url_map_name),
            }

            operation = (
                self._compute.targetHttpProxies()
                .insert(project=self._project_id, body=body)
                .execute()
            )
            wait_for_global_operation(
                self._compute, self._project_id, operation["name"],
            )
            logger.info("HTTP target proxy %s created.", proxy_name)

        await self._run_sync(_create)
        await self._emit(f"[INFRA] HTTP target proxy ready: {proxy_name}")

    # =================================================================
    #  Step 8 — Forwarding Rules
    # =================================================================

    async def _ensure_forwarding_rule(
        self,
        name: str,
        ip_name: str,
        target_proxy_name: str,
        target_proxy_type: str,
        port: str,
    ) -> None:
        """Create a global forwarding rule (HTTP or HTTPS)."""
        await self._emit(f"[INFRA] Checking forwarding rule: {name} (port {port})")

        def _create() -> None:
            try:
                self._compute.globalForwardingRules().get(
                    project=self._project_id, forwardingRule=name,
                ).execute()
                logger.info("Forwarding rule %s already exists — skipping.", name)
                return
            except api_errors.HttpError as err:
                if err.resp.status != 404:
                    raise

            # Retrieve the IP address resource self-link
            ip_resource = (
                self._compute.globalAddresses()
                .get(project=self._project_id, address=ip_name)
                .execute()
            )
            ip_self_link = ip_resource["selfLink"]

            body: dict[str, Any] = {
                "name": name,
                "IPAddress": ip_self_link,
                "IPProtocol": "TCP",
                "portRange": port,
                "target": self._self_link(target_proxy_type, target_proxy_name),
                "loadBalancingScheme": "EXTERNAL",
            }

            operation = (
                self._compute.globalForwardingRules()
                .insert(project=self._project_id, body=body)
                .execute()
            )
            wait_for_global_operation(
                self._compute, self._project_id, operation["name"],
            )
            logger.info("Forwarding rule %s created on port %s.", name, port)

        await self._run_sync(_create)
        await self._emit(f"[INFRA] Forwarding rule ready: {name} (port {port})")

    # =================================================================
    #  Step 9 — DNS Zone
    # =================================================================

    async def _ensure_dns_zone(
        self, safe_domain: str, domain: str, ip_address: str,
    ) -> None:
        """Create a Cloud DNS managed zone with A and CNAME records."""
        zone_name = f"{safe_domain}-zone"
        dns_name = f"{domain}."  # DNS names are FQDN with trailing dot
        await self._emit(f"[INFRA] Checking DNS zone: {zone_name}")

        def _create() -> None:
            # --- Ensure managed zone exists ---
            try:
                self._dns.managedZones().get(
                    project=self._project_id, managedZone=zone_name,
                ).execute()
                logger.info("DNS zone %s already exists — skipping zone creation.", zone_name)
            except api_errors.HttpError as err:
                if err.resp.status != 404:
                    raise

                zone_body: dict[str, Any] = {
                    "name": zone_name,
                    "dnsName": dns_name,
                    "description": f"Managed zone for {domain} (WebDeploy)",
                }
                self._dns.managedZones().create(
                    project=self._project_id, body=zone_body,
                ).execute()
                logger.info("DNS zone %s created.", zone_name)

            # --- Ensure A record for root domain ---
            self._ensure_dns_record(
                zone_name=zone_name,
                record_name=dns_name,
                record_type="A",
                ttl=300,
                rrdatas=[ip_address],
            )

            # --- Ensure CNAME for www ---
            self._ensure_dns_record(
                zone_name=zone_name,
                record_name=f"www.{dns_name}",
                record_type="CNAME",
                ttl=300,
                rrdatas=[dns_name],
            )

        await self._run_sync(_create)
        await self._emit(f"[INFRA] DNS zone ready: {zone_name}")

    def _ensure_dns_record(
        self,
        zone_name: str,
        record_name: str,
        record_type: str,
        ttl: int,
        rrdatas: list[str],
    ) -> None:
        """Idempotently create or update a DNS record set.

        Uses the Cloud DNS ``changes.create`` API with simultaneous delete +
        add to handle both creation and update in a single call.
        """
        # Check if the record already exists with the correct data
        try:
            existing = (
                self._dns.resourceRecordSets()
                .list(
                    project=self._project_id,
                    managedZone=zone_name,
                    name=record_name,
                    type=record_type,
                )
                .execute()
            )
            rrsets = existing.get("rrsets", [])
            if rrsets:
                current = rrsets[0]
                if current.get("rrdatas") == rrdatas and current.get("ttl") == ttl:
                    logger.info(
                        "DNS record %s %s already correct — skipping.",
                        record_type, record_name,
                    )
                    return

                # Record exists but needs updating — delete old, add new
                change_body: dict[str, Any] = {
                    "deletions": [
                        {
                            "name": record_name,
                            "type": record_type,
                            "ttl": current.get("ttl", ttl),
                            "rrdatas": current.get("rrdatas", []),
                        }
                    ],
                    "additions": [
                        {
                            "name": record_name,
                            "type": record_type,
                            "ttl": ttl,
                            "rrdatas": rrdatas,
                        }
                    ],
                }
            else:
                # Record does not exist — add only
                change_body = {
                    "additions": [
                        {
                            "name": record_name,
                            "type": record_type,
                            "ttl": ttl,
                            "rrdatas": rrdatas,
                        }
                    ],
                }
        except api_errors.HttpError:
            # If listing fails, try a blind addition
            change_body = {
                "additions": [
                    {
                        "name": record_name,
                        "type": record_type,
                        "ttl": ttl,
                        "rrdatas": rrdatas,
                    }
                ],
            }

        self._dns.changes().create(
            project=self._project_id,
            managedZone=zone_name,
            body=change_body,
        ).execute()
        logger.info("DNS record %s %s -> %s created/updated.", record_type, record_name, rrdatas)
