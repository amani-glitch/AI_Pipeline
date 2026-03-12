"""
ProdDeployer — production deployment using a shared GCP load balancer.

All production domains share a single load balancer with host-based routing:

    Shared Static IP  ->  Shared Forwarding Rules  ->  Shared Target Proxies
        (websites-lb-ip-prod)                           (websites-https-proxy-prod)
                                                              |
                                                      Shared URL Map
                                                   (websites-urlmap-prod)
                                                         |     |
                                              hostRule: domain1.com  domain2.com
                                                         |     |
                                                  pathMatcher1  pathMatcher2
                                                         |     |
                                                  BackendBucket1  BackendBucket2
                                                         |     |
                                                  StorageBucket1  StorageBucket2

Per-domain resources created:
  - Cloud Storage bucket (website files)
  - Backend bucket (CDN)
  - SSL certificate (Google-managed)
  - Cloud DNS zone + A/CNAME records

Shared resources updated (not created):
  - URL map: add host rule + path matcher for the domain
  - HTTPS proxy: attach the new SSL certificate

All operations are **idempotent**: every resource is checked for existence
before creation.  Re-running a deployment that partially succeeded will pick
up where it left off.
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


class ProdDeployer:
    """Provision production infrastructure using the shared load balancer.

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

    async def deploy(self, website_name: str, domain: str, main_html_file: str = "index.html") -> DeploymentResult:
        """Provision production infrastructure for *domain* using the shared LB.

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
            # Step 1 — Get shared static IP
            ip_address = await self._get_shared_ip()

            # Step 2 — Storage bucket
            await self._ensure_storage_bucket(bucket_name, domain, main_html_file)

            # Step 3 — Backend bucket (CDN)
            await self._ensure_backend_bucket(backend_bucket_name, bucket_name)

            # Step 4 — Add host rule to shared URL map
            await self._add_host_rule_to_shared_url_map(
                domain, safe_domain, backend_bucket_name,
            )

            # Step 5 — SSL certificate
            ssl_cert_name: str | None = None
            if self._config.PROD_AUTO_CREATE_SSL_CERT:
                ssl_cert_name = f"{safe_domain}-ssl-cert"
                await self._ensure_ssl_certificate(ssl_cert_name, domain)

                # Step 6 — Attach SSL cert to shared HTTPS proxy
                await self._add_ssl_cert_to_shared_proxy(ssl_cert_name)

            # Step 7 — DNS zone (optional)
            dns_nameservers: list[str] = []
            if self._config.PROD_AUTO_CREATE_DNS_ZONE:
                dns_nameservers = await self._ensure_dns_zone(safe_domain, domain, ip_address)

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
                dns_nameservers=dns_nameservers,
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
    #  Step 1 — Get Shared Static IP
    # =================================================================

    async def _get_shared_ip(self) -> str:
        """Retrieve the IP address of the shared prod load balancer."""
        ip_name = self._config.PROD_GLOBAL_IP_NAME
        await self._emit(f"[INFRA] Retrieving shared prod IP: {ip_name}")

        def _get() -> str:
            result = (
                self._compute.globalAddresses()
                .get(project=self._project_id, address=ip_name)
                .execute()
            )
            return result["address"]

        ip_address: str = await self._run_sync(_get)
        await self._emit(f"[INFRA] Shared prod IP: {ip_name} -> {ip_address}")
        return ip_address

    # =================================================================
    #  Step 2 — Storage Bucket
    # =================================================================

    async def _ensure_storage_bucket(self, bucket_name: str, domain: str, main_html_file: str = "index.html") -> None:
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

            # Website config
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
    #  Step 4 — Add Host Rule to Shared URL Map
    # =================================================================

    async def _add_host_rule_to_shared_url_map(
        self, domain: str, safe_domain: str, backend_bucket_name: str,
    ) -> None:
        """Add a host rule for *domain* to the shared prod URL map.

        Each domain gets its own hostRule + pathMatcher pointing to its
        backend bucket.  Existing rules for other domains are preserved.
        If the domain is already present, this is a no-op.
        """
        url_map_name = self._config.PROD_URL_MAP_NAME
        await self._emit(
            f"[INFRA] Adding host rule for '{domain}' to shared URL map '{url_map_name}'"
        )

        def _update() -> None:
            max_retries = 5
            base_delay = 5

            for attempt in range(1, max_retries + 1):
                try:
                    self._patch_shared_url_map(url_map_name, domain, safe_domain, backend_bucket_name)
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
        await self._emit(f"[INFRA] Host rule added for '{domain}' in shared URL map")

    def _patch_shared_url_map(
        self, url_map_name: str, domain: str, safe_domain: str, backend_bucket_name: str,
    ) -> None:
        """Fetch the shared URL map, add a host rule for the domain, and patch."""
        url_map = (
            self._compute.urlMaps()
            .get(project=self._project_id, urlMap=url_map_name)
            .execute()
        )

        # Check if the domain already has a host rule
        host_rules: list[dict] = url_map.get("hostRules", [])
        for hr in host_rules:
            if domain in hr.get("hosts", []):
                logger.info(
                    "Host rule for '%s' already exists in URL map '%s' — skipping.",
                    domain, url_map_name,
                )
                return

        # Resolve the backend bucket self-link
        bb_resource = (
            self._compute.backendBuckets()
            .get(project=self._project_id, backendBucket=backend_bucket_name)
            .execute()
        )
        bb_self_link = bb_resource["selfLink"]

        # Create a unique path matcher name for this domain
        matcher_name = f"pm-{safe_domain}"

        # Add the new path matcher
        path_matchers: list[dict] = url_map.get("pathMatchers", [])
        path_matchers.append({
            "name": matcher_name,
            "defaultService": bb_self_link,
        })
        url_map["pathMatchers"] = path_matchers

        # Add the new host rule
        host_rules.append({
            "hosts": [domain],
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
            url_map_name, domain, backend_bucket_name,
        )

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
    #  Step 6 — Attach SSL Cert to Shared HTTPS Proxy
    # =================================================================

    async def _add_ssl_cert_to_shared_proxy(self, ssl_cert_name: str) -> None:
        """Add the SSL certificate to the shared HTTPS target proxy.

        GCP allows up to 15 SSL certificates per target HTTPS proxy.
        If the cert is already attached, this is a no-op.
        """
        proxy_name = self._config.PROD_HTTPS_PROXY_NAME
        await self._emit(
            f"[INFRA] Attaching SSL cert '{ssl_cert_name}' to shared proxy '{proxy_name}'"
        )

        def _update() -> None:
            # Get the current proxy config
            proxy = (
                self._compute.targetHttpsProxies()
                .get(project=self._project_id, targetHttpsProxy=proxy_name)
                .execute()
            )

            current_certs: list[str] = proxy.get("sslCertificates", [])
            new_cert_link = self._self_link("sslCertificates", ssl_cert_name)

            # Check if already attached (compare by name, not full link)
            for cert_link in current_certs:
                if cert_link.endswith(f"/{ssl_cert_name}"):
                    logger.info(
                        "SSL cert %s already attached to proxy %s — skipping.",
                        ssl_cert_name, proxy_name,
                    )
                    return

            # Append the new cert
            updated_certs = current_certs + [new_cert_link]

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
            logger.info(
                "SSL cert %s attached to proxy %s (total: %d certs).",
                ssl_cert_name, proxy_name, len(updated_certs),
            )

        await self._run_sync(_update)
        await self._emit(
            f"[INFRA] SSL cert '{ssl_cert_name}' attached to shared proxy"
        )

    # =================================================================
    #  Step 7 — DNS Zone
    # =================================================================

    async def _ensure_dns_zone(
        self, safe_domain: str, domain: str, ip_address: str,
    ) -> list[str]:
        """Create a Cloud DNS managed zone with A and CNAME records.

        Returns the list of nameservers assigned to the zone.
        """
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

        # Retrieve and display the nameservers so the user can configure
        # their external registrar (GoDaddy, OVH, etc.)
        nameservers: list[str] = []
        try:
            zone_info = self._dns.managedZones().get(
                project=self._project_id, managedZone=zone_name,
            ).execute()
            nameservers = zone_info.get("nameServers", [])
            if nameservers:
                ns_list = ", ".join(nameservers)
                await self._emit(
                    f"[INFRA] DNS zone ready: {zone_name} — "
                    f"Nameservers a configurer chez votre registrar : {ns_list}"
                )
            else:
                await self._emit(f"[INFRA] DNS zone ready: {zone_name}")
        except Exception:
            await self._emit(f"[INFRA] DNS zone ready: {zone_name}")

        return nameservers

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
