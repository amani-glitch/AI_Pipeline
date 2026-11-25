"""WHOIS contact building for Cloud Domains registrations.

These are the fields that decide whether a purchase is accepted: a
registration that reaches the API with a mailbox as the addressee, a missing
postal code or an unknown privacy mode is rejected *after* the order, so the
mapping from settings to ``ContactSettings`` is worth pinning down.
"""

from google.cloud import domains_v1

from config import Settings
from services.domain_service import DomainService


def settings(**overrides) -> Settings:
    return Settings(PROJECT_ID="proj", _env_file=None, **overrides)


def test_company_defaults_are_complete():
    """The shipped defaults must be enough to buy a domain unattended."""
    assert DomainService(settings()).missing_contact_fields() == []


def test_missing_fields_are_named_before_anything_is_bought():
    svc = DomainService(settings(DOMAINS_CONTACT_POSTAL_CODE="", DOMAINS_CONTACT_PHONE=""))
    assert sorted(svc.missing_contact_fields()) == ["code postal", "téléphone"]

    result = svc.register_domain("example-does-not-exist-12345.com")
    assert result.success is False
    assert "code postal" in result.message


def test_contact_addressee_is_the_company_not_the_mailbox():
    contact = DomainService(settings())._contact_settings()
    address = contact.registrant_contact.postal_address
    assert address.recipients == ["Best of Tours SAS"]
    assert "@" not in address.recipients[0]
    assert address.locality == "Carpentras"
    assert address.postal_code == "84200"
    assert address.region_code == "FR"
    assert contact.registrant_contact.email == "ahmed@bestoftours.co.uk"
    assert contact.registrant_contact.phone_number.startswith("+44")
    # The same contact fills all three WHOIS roles.
    assert contact.admin_contact == contact.registrant_contact
    assert contact.technical_contact == contact.registrant_contact


def test_privacy_comes_from_settings_and_falls_back_safely():
    redacted = DomainService(settings())._contact_settings()
    assert redacted.privacy == domains_v1.ContactPrivacy.REDACTED_CONTACT_DATA

    public = DomainService(settings(DOMAINS_CONTACT_PRIVACY="public_contact_data"))._contact_settings()
    assert public.privacy == domains_v1.ContactPrivacy.PUBLIC_CONTACT_DATA

    # A typo must not publish the company's details by accident.
    nonsense = DomainService(settings(DOMAINS_CONTACT_PRIVACY="whatever"))._contact_settings()
    assert nonsense.privacy == domains_v1.ContactPrivacy.REDACTED_CONTACT_DATA
