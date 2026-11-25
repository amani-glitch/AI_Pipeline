"""
Domain service — check ownership and purchase domains via Google Cloud Domains API.

Checks domain ownership in three ways (in order):
1. Cloud Domains registrations (domains bought via Google Cloud)
2. Cloud DNS managed zones (domains managed in the GCP project, even if bought elsewhere)
3. Cloud Domains searchDomains (availability + pricing for purchase)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from google.api_core import exceptions as gcp_exceptions
from google.cloud import domains_v1
from google.type import postal_address_pb2
from googleapiclient.discovery import build as api_build

from config import Settings
from infra.gcp_helpers import get_credentials

logger = logging.getLogger("webdeploy.domain_service")

#: Stand-in nameservers used only by ``validate_only`` registrations, which
#: must send a well-formed DNS configuration without creating a zone.
_PLACEHOLDER_NS = [f"ns-cloud-a{i}.googledomains.com" for i in (1, 2, 3, 4)]


@dataclass
class DomainCheckResult:
    """Result of a domain ownership/availability check."""
    status: str  # "owned" | "available" | "unavailable"
    price_amount: Optional[float] = None
    price_currency: Optional[str] = None
    message: str = ""


@dataclass
class DomainRegisterResult:
    """Result of a domain registration attempt."""
    success: bool
    message: str = ""
    # The Cloud DNS zone the domain was pointed at, and its nameservers.
    zone_name: str = ""
    name_servers: list[str] = field(default_factory=list)
    zone_created: bool = False
    already_registered: bool = False


class DomainService:
    """Check and register domains via Google Cloud Domains + Cloud DNS."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._project = settings.PROJECT_ID
        self._location = settings.DOMAINS_LOCATION

    @property
    def _parent(self) -> str:
        return f"projects/{self._project}/locations/{self._location}"

    def _get_domains_client(self) -> domains_v1.DomainsClient:
        credentials = get_credentials(self._settings.GOOGLE_APPLICATION_CREDENTIALS)
        return domains_v1.DomainsClient(credentials=credentials)

    def _get_dns_service(self):
        credentials = get_credentials(self._settings.GOOGLE_APPLICATION_CREDENTIALS)
        return api_build("dns", "v1", credentials=credentials, cache_discovery=False)

    # ── Check domain ─────────────────────────────────────────────────

    def _check_cloud_domains(self, domain: str) -> Optional[DomainCheckResult]:
        """Check if the domain is registered via Cloud Domains."""
        try:
            client = self._get_domains_client()
            registration_name = f"{self._parent}/registrations/{domain}"
            client.get_registration(name=registration_name)
            logger.info("Domain %s found in Cloud Domains registrations", domain)
            return DomainCheckResult(
                status="owned",
                message=f"Domaine {domain} enregistré via Google Cloud Domains.",
            )
        except gcp_exceptions.NotFound:
            return None
        except Exception as exc:
            logger.warning("Error checking Cloud Domains for %s: %s", domain, exc)
            return None

    @staticmethod
    def _is_subdomain(domain: str) -> bool:
        """Return True if *domain* has 3+ dot-separated parts (e.g. blog.example.com)."""
        return len(domain.strip(".").split(".")) > 2

    @staticmethod
    def _get_parent_domain(domain: str) -> str:
        """Extract parent domain: ``blog.example.com`` -> ``example.com``."""
        parts = domain.strip(".").split(".")
        return ".".join(parts[-2:]) if len(parts) > 2 else domain

    def _check_cloud_dns(self, domain: str) -> Optional[DomainCheckResult]:
        """Check if a Cloud DNS managed zone exists for this domain or its parent.

        For subdomains (e.g. ``blog.example.com``), also checks if the parent
        domain (``example.com``) has a DNS zone — if so, the subdomain is
        considered "owned" because we can add records to it.
        """
        try:
            dns = self._get_dns_service()
            response = dns.managedZones().list(project=self._project).execute()
            zones = response.get("managedZones", [])

            # Cloud DNS stores dnsName with trailing dot: "example.com."
            target_dns_name = f"{domain}."

            # 1. Exact match — zone exists for this domain
            for zone in zones:
                if zone.get("dnsName") == target_dns_name:
                    logger.info(
                        "Domain %s found in Cloud DNS zone '%s'",
                        domain, zone.get("name"),
                    )
                    return DomainCheckResult(
                        status="owned",
                        message=f"Domaine {domain} configuré dans Cloud DNS (zone: {zone.get('name')}).",
                    )

            # 2. Subdomain check — look for parent zone
            if self._is_subdomain(domain):
                parent = self._get_parent_domain(domain)
                parent_dns_name = f"{parent}."
                for zone in zones:
                    if zone.get("dnsName") == parent_dns_name:
                        logger.info(
                            "Subdomain %s — parent zone '%s' found for %s",
                            domain, zone.get("name"), parent,
                        )
                        return DomainCheckResult(
                            status="owned",
                            message=(
                                f"Sous-domaine {domain} — la zone DNS du domaine parent "
                                f"{parent} existe (zone: {zone.get('name')}). "
                                f"Un enregistrement A sera ajouté automatiquement."
                            ),
                        )

            return None
        except Exception as exc:
            logger.warning("Error checking Cloud DNS for %s: %s", domain, exc)
            return None

    def check_domain(self, domain: str) -> DomainCheckResult:
        """
        Check whether a domain is already managed in this GCP project,
        available for purchase, or unavailable.

        For subdomains (e.g. ``blog.example.com``), checks whether the
        parent domain's DNS zone exists.  Cloud Domains availability
        search is only performed for root domains.

        Checks in order:
        1. Cloud Domains registrations (bought via Google)
        2. Cloud DNS zones (domain or parent domain managed here)
        3. Cloud Domains availability search (root domains only)
        """
        is_subdomain = self._is_subdomain(domain)
        parent_domain = self._get_parent_domain(domain) if is_subdomain else domain

        # 1. Cloud Domains registration (check parent for subdomains)
        result = self._check_cloud_domains(parent_domain)
        if result:
            if is_subdomain:
                return DomainCheckResult(
                    status="owned",
                    message=(
                        f"Sous-domaine {domain} — le domaine parent {parent_domain} "
                        f"est enregistré via Google Cloud Domains. "
                        f"Un enregistrement A sera ajouté automatiquement."
                    ),
                )
            return result

        # 2. Cloud DNS zone (also checks parent for subdomains)
        result = self._check_cloud_dns(domain)
        if result:
            return result

        # 3. For subdomains without a parent zone, explain what's needed
        if is_subdomain:
            logger.info(
                "Subdomain %s — no parent zone found for %s",
                domain, parent_domain,
            )
            return DomainCheckResult(
                status="external",
                message=(
                    f"Sous-domaine {domain} — aucune zone DNS trouvée pour {parent_domain}. "
                    f"Une nouvelle zone DNS sera créée automatiquement. "
                    f"Vous devrez configurer les nameservers chez votre registrar."
                ),
            )

        logger.info("Domain %s not found in project — checking availability", domain)

        # 4. Check availability via searchDomains (root domains only)
        try:
            client = self._get_domains_client()
            response = client.search_domains(
                query=domain,
                location=self._parent,
            )

            for reg_params in response.register_parameters:
                if reg_params.domain_name == domain:
                    if reg_params.availability == domains_v1.RegisterParameters.Availability.AVAILABLE:
                        price = reg_params.yearly_price
                        amount = price.units + price.nanos / 1e9 if price else 0
                        currency = price.currency_code if price else "USD"
                        logger.info(
                            "Domain %s is available: %.2f %s/year",
                            domain, amount, currency,
                        )
                        return DomainCheckResult(
                            status="available",
                            price_amount=amount,
                            price_currency=currency,
                            message=f"Domaine {domain} disponible — {amount:.2f} {currency}/an",
                        )
                    else:
                        logger.info("Domain %s registered externally (not in GCP)", domain)
                        return DomainCheckResult(
                            status="external",
                            message=(
                                f"Domaine {domain} enregistré chez un registrar externe. "
                                f"Après le déploiement, configurez les nameservers pour pointer vers Google Cloud DNS."
                            ),
                        )

            return DomainCheckResult(
                status="external",
                message=(
                    f"Domaine {domain} non trouvé dans Google Cloud Domains. "
                    f"S'il est enregistré ailleurs, vous pouvez déployer et configurer les nameservers après."
                ),
            )

        except Exception:
            logger.exception("Error searching domain availability for %s", domain)
            return DomainCheckResult(
                status="external",
                message="Impossible de vérifier le domaine (erreur API). Vous pouvez quand même déployer.",
            )

    # ── Register domain ──────────────────────────────────────────────

    #: WHOIS fields Cloud Domains rejects a registration without.
    _REQUIRED_CONTACT_FIELDS = (
        ("DOMAINS_CONTACT_EMAIL", "email"),
        ("DOMAINS_CONTACT_PHONE", "téléphone"),
        ("DOMAINS_CONTACT_COUNTRY", "pays"),
        ("DOMAINS_CONTACT_CITY", "ville"),
        ("DOMAINS_CONTACT_ADDRESS", "adresse"),
        ("DOMAINS_CONTACT_POSTAL_CODE", "code postal"),
    )

    def missing_contact_fields(self) -> list[str]:
        """WHOIS contact settings that are not configured.

        Checked before anything is bought: a registration that reaches the
        API with an incomplete contact is rejected *after* the order, which
        used to surface as an opaque ``InvalidArgument``.
        """
        return [
            label for key, label in self._REQUIRED_CONTACT_FIELDS
            if not str(getattr(self._settings, key, "") or "").strip()
        ]

    def _contact_settings(self) -> domains_v1.ContactSettings:
        """The registrant/admin/technical contact written into WHOIS."""
        s = self._settings
        recipient = (s.DOMAINS_CONTACT_NAME or s.DOMAINS_CONTACT_COMPANY or "").strip()
        contact = domains_v1.ContactSettings.Contact(
            postal_address=postal_address_pb2.PostalAddress(
                region_code=s.DOMAINS_CONTACT_COUNTRY,
                administrative_area=s.DOMAINS_CONTACT_STATE,
                locality=s.DOMAINS_CONTACT_CITY,
                address_lines=[s.DOMAINS_CONTACT_ADDRESS] if s.DOMAINS_CONTACT_ADDRESS else [],
                postal_code=s.DOMAINS_CONTACT_POSTAL_CODE,
                organization=s.DOMAINS_CONTACT_COMPANY,
                # WHOIS wants a person or entity name here, not an email
                # address: registries reject (or publish) a mailbox as the
                # addressee.
                recipients=[recipient] if recipient else [],
            ),
            email=s.DOMAINS_CONTACT_EMAIL,
            phone_number=s.DOMAINS_CONTACT_PHONE,
        )
        privacy = getattr(
            domains_v1.ContactPrivacy,
            (s.DOMAINS_CONTACT_PRIVACY or "REDACTED_CONTACT_DATA").strip().upper(),
            domains_v1.ContactPrivacy.REDACTED_CONTACT_DATA,
        )
        return domains_v1.ContactSettings(
            privacy=privacy,
            registrant_contact=contact,
            admin_contact=contact,
            technical_contact=contact,
        )

    def _register_parameters(self, domain: str):
        """``RegisterParameters`` for *domain* (price, availability, notices)."""
        client = self._get_domains_client()
        response = client.search_domains(query=domain, location=self._parent)
        for params in response.register_parameters:
            if params.domain_name == domain:
                return params
        return None

    def _existing_zone_nameservers(self, domain: str) -> list[str]:
        """Nameservers of the zone already serving *domain*, if any."""
        from infra import dns_ops

        try:
            dns = self._get_dns_service()
            zone = dns_ops.find_zone_for(dns, self._project, domain)
            if zone is None:
                return []
            return dns_ops.zone_nameservers(dns, self._project, zone["name"])
        except Exception as exc:
            logger.warning("Could not read nameservers for %s: %s", domain, exc)
            return []

    def ensure_dns_zone(self, domain: str) -> tuple[str, list[str], bool]:
        """Get or create the Cloud DNS zone that will serve *domain*.

        Returns ``(zone_name, nameservers, created)``. The zone is created
        **before** the domain is bought so the registration can carry this
        zone's real nameservers; Google spreads zones over four
        ``ns-cloud-{a..f}`` sets, so a hard-coded set resolves nothing.
        """
        from infra import dns_ops
        from infra.gcp_helpers import bounded_name, safe_name

        dns = self._get_dns_service()
        zone_name_for_new = bounded_name(f"{safe_name(domain)}-zone")
        zone, nameservers, created = dns_ops.ensure_zone_for_domain(
            dns, self._project, domain, zone_name_for_new,
        )
        return zone["name"], nameservers, created

    def register_domain(
        self,
        domain: str,
        *,
        name_servers: Optional[list[str]] = None,
        validate_only: bool = False,
        log=lambda msg: None,
    ) -> DomainRegisterResult:
        """Buy *domain* through Cloud Domains and point it at Cloud DNS.

        A Cloud DNS zone for the domain is created first (unless one exists,
        or ``name_servers`` is supplied) and the registration is submitted
        with that zone's nameservers, so the domain resolves through this
        project as soon as it is active.

        With ``validate_only`` nothing is created or bought: Cloud Domains
        validates the contact settings, notices and price and returns, which
        is how the flow is exercised end to end without spending money.
        """
        missing = self.missing_contact_fields()
        if missing:
            return DomainRegisterResult(
                success=False,
                message=(
                    "Coordonnées WHOIS incomplètes — impossible d'acheter un domaine. "
                    f"Champs manquants: {', '.join(missing)}."
                ),
            )

        # Already ours? Nothing to buy — but the DNS zone must still exist.
        check_result = self.check_domain(domain)
        if check_result.status == "owned":
            if validate_only:
                return DomainRegisterResult(
                    success=True,
                    message=f"Domaine {domain} déjà enregistré — aucun achat nécessaire.",
                    name_servers=self._existing_zone_nameservers(domain),
                    already_registered=True,
                )
            zone_name, nameservers, created = self.ensure_dns_zone(domain)
            return DomainRegisterResult(
                success=True,
                message=f"Domaine {domain} déjà enregistré — aucun achat nécessaire.",
                zone_name=zone_name,
                name_servers=nameservers if created else [],
                zone_created=created,
                already_registered=True,
            )
        if check_result.status != "available":
            return DomainRegisterResult(
                success=False,
                message=f"Impossible d'acheter {domain}: {check_result.message}",
            )

        try:
            params = self._register_parameters(domain)
            if params is None or params.yearly_price is None:
                return DomainRegisterResult(
                    success=False,
                    message=f"Impossible de déterminer le prix pour {domain}.",
                )

            zone_created = False
            zone_name = ""
            if name_servers:
                nameservers = list(name_servers)
            elif validate_only:
                # A dry run creates nothing, so reuse an existing zone's
                # nameservers or stand in Google's default set for the
                # validation call only.
                nameservers = self._existing_zone_nameservers(domain) or _PLACEHOLDER_NS
            else:
                zone_name, nameservers, zone_created = self.ensure_dns_zone(domain)
                log(f"Zone DNS {zone_name} {'créée' if zone_created else 'réutilisée'} pour {domain}")
            if not nameservers:
                return DomainRegisterResult(
                    success=False,
                    message=f"Zone DNS sans serveurs de noms pour {domain} — achat annulé.",
                )

            registration = domains_v1.Registration(
                name=f"{self._parent}/registrations/{domain}",
                domain_name=domain,
                dns_settings=domains_v1.DnsSettings(
                    custom_dns=domains_v1.DnsSettings.CustomDns(name_servers=nameservers),
                ),
                contact_settings=self._contact_settings(),
                labels={"managed-by": "webdeploy"},
            )

            price = params.yearly_price
            amount = price.units + price.nanos / 1e9
            log(
                f"Achat de {domain} — {amount:.2f} {price.currency_code}/an, "
                f"serveurs de noms: {', '.join(nameservers)}"
            )

            operation = self._get_domains_client().register_domain(
                request=domains_v1.RegisterDomainRequest(
                    parent=self._parent,
                    registration=registration,
                    # Notices the registry requires for this TLD (e.g. the
                    # HSTS-preload notice on .app/.dev) — omitting one is a
                    # hard rejection.
                    domain_notices=list(params.domain_notices),
                    contact_notices=(
                        [domains_v1.ContactNotice.PUBLIC_CONTACT_DATA_ACKNOWLEDGEMENT]
                        if registration.contact_settings.privacy
                        == domains_v1.ContactPrivacy.PUBLIC_CONTACT_DATA
                        else []
                    ),
                    yearly_price=price,
                    validate_only=validate_only,
                )
            )

            if validate_only:
                logger.info("Domain registration for %s validated (dry run)", domain)
                return DomainRegisterResult(
                    success=True,
                    message=(
                        f"Validation réussie pour {domain} — {amount:.2f} "
                        f"{price.currency_code}/an (aucun achat effectué)."
                    ),
                    name_servers=nameservers,
                )

            logger.info("Domain registration started for %s — waiting for completion...", domain)
            operation.result(timeout=600)  # registries can take a few minutes

            logger.info("Domain %s registered successfully!", domain)
            return DomainRegisterResult(
                success=True,
                message=(
                    f"Domaine {domain} acheté avec succès "
                    f"({amount:.2f} {price.currency_code}/an)."
                ),
                zone_name=zone_name,
                name_servers=nameservers,
                zone_created=zone_created,
            )

        except Exception as exc:
            logger.exception("Failed to register domain %s", domain)
            return DomainRegisterResult(
                success=False,
                message=f"Erreur lors de l'achat du domaine {domain}: {exc}",
            )
