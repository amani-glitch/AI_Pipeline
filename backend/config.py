"""
Application configuration — all settings sourced from environment variables.
Uses pydantic-settings for validation, type coercion, and .env file support.
"""

from __future__ import annotations

import os
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Top-level application settings."""

    # ── GCP ──────────────────────────────────────────────────────────
    PROJECT_ID: str = "adp-413110"
    GOOGLE_APPLICATION_CREDENTIALS: str = ""

    # Demo infrastructure (EXISTING — never created by the platform)
    DEMO_DOMAIN: str = "digitaldatatest.com"
    DEMO_URL_MAP_NAME: str = "test-lb"
    DEMO_GLOBAL_IP_NAME: str = "test-lb-ip"
    DEMO_HTTPS_PROXY_NAME: str = "test-lb-target-proxy"
    DEMO_WILDCARD_SSL_CERT_NAME: str = "wildcard-digitaldatatest-com"

    # Prod infrastructure — shared load balancer (pre-existing)
    PROD_URL_MAP_NAME: str = "websites-urlmap-prod"
    PROD_GLOBAL_IP_NAME: str = "websites-lb-ip-prod"
    PROD_HTTPS_PROXY_NAME: str = "websites-https-proxy-prod"

    # Prod infrastructure — shared load balancer (pre-existing)
    PROD_URL_MAP_NAME: str = "websites-urlmap-prod"
    PROD_GLOBAL_IP_NAME: str = "websites-lb-ip-prod"
    PROD_HTTPS_PROXY_NAME: str = "websites-https-proxy-prod"

    # Prod infrastructure toggles
    PROD_AUTO_REGISTER_DOMAINS: bool = False
    PROD_AUTO_CREATE_DNS_ZONE: bool = True
    PROD_AUTO_CREATE_SSL_CERT: bool = True

    # Domain registration (Cloud Domains WHOIS contact)
    DOMAINS_CONTACT_EMAIL: str = ""
    DOMAINS_CONTACT_PHONE: str = ""
    DOMAINS_CONTACT_COMPANY: str = "Best of Tours"
    DOMAINS_CONTACT_COUNTRY: str = "FR"
    DOMAINS_CONTACT_STATE: str = ""
    DOMAINS_CONTACT_CITY: str = ""
    DOMAINS_CONTACT_ADDRESS: str = ""
    DOMAINS_CONTACT_POSTAL_CODE: str = ""
    DOMAINS_LOCATION: str = "global"

    # Bucket
    BUCKET_LOCATION: str = "US"
    BUCKET_CORS_MAX_AGE: int = 3600

    # CDN
    CDN_DEFAULT_TTL: int = 3600
    CDN_MAX_TTL: int = 86400
    CDN_CLIENT_TTL: int = 3600
    CDN_NEGATIVE_CACHING: bool = True
    CDN_NEGATIVE_CACHING_TTL: int = 120

    # ── AI Validation ───────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""  # Free fallback when Claude is unavailable
    OPENROUTER_MODEL: str = "meta-llama/llama-3.1-8b-instruct:free"

    # ── Email (Gmail API via service account) ────────────────────────
    GMAIL_DELEGATED_USER: str = ""
    NOTIFICATION_FROM_EMAIL: str = "webdeploy@bestoftours.co.uk"
    NOTIFICATION_TO_EMAILS: str = ""
    DAILY_REPORT_EMAILS: str = "amani@bestoftours.co.uk,eline@bestoftours.co.uk,maeva@bestoftours.co.uk,yacine@bestoftours.co.uk"

    # ── Report scheduling ─────────────────────────────────────────────
    WEEKLY_REPORT_DAY: str = "friday"   # lowercase day name for weekly report
    MONTHLY_REPORT_DAY: int = 1         # day of month for monthly report

    # ── Auth ─────────────────────────────────────────────────────────
    ADMIN_APPROVAL_EMAIL: str = "amani@bestoftours.co.uk"
    APPROVAL_TOKEN_SECRET: str = ""
    FRONTEND_URL: str = "http://localhost:3500"
    GOOGLE_CLIENT_ID: str = ""  # OAuth client ID for GIS token verification

    # ── App ──────────────────────────────────────────────────────────
    DEPLOY_UPLOADS_BUCKET: str = ""  # GCS bucket for ZIP backups; defaults to "{PROJECT_ID}-deploy-uploads"
    UPLOAD_DIR: str = "./uploads"
    TEMP_DIR: str = "./tmp"
    LOG_LEVEL: str = "INFO"
    DATABASE_URL: str = "sqlite:///./data/webdeploy.db"

    # ── Build ────────────────────────────────────────────────────────
    BUILD_TIMEOUT_SECONDS: int = 600  # 10 minutes
    PREVIEW_TIMEOUT_SECONDS: int = 30
    MAX_ZIP_SIZE_MB: int = 500
    PIPELINE_MAX_TIMEOUT_SECONDS: int = 900  # 15 minutes — hard timeout for entire pipeline

    # ── Cloud Run ──────────────────────────────────────────────────
    CLOUDRUN_REGION: str = "europe-west1"
    CLOUDRUN_MEMORY: str = "512Mi"
    CLOUDRUN_CPU: str = "1"
    CLOUDRUN_MAX_INSTANCES: int = 10
    CLOUDRUN_MIN_INSTANCES: int = 0
    CLOUDRUN_ARTIFACT_REPO: str = "cloud-run-images"
    CLOUD_BUILD_TIMEOUT_SECONDS: int = 600

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # ── Derived helpers ──────────────────────────────────────────────
    @property
    def upload_path(self) -> Path:
        p = Path(self.UPLOAD_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def temp_path(self) -> Path:
        p = Path(self.TEMP_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def deploy_uploads_bucket_name(self) -> str:
        return self.DEPLOY_UPLOADS_BUCKET or f"{self.PROJECT_ID}-deploy-uploads"

    @property
    def notification_emails_list(self) -> list[str]:
        if not self.NOTIFICATION_TO_EMAILS:
            return []
        return [e.strip() for e in self.NOTIFICATION_TO_EMAILS.split(",") if e.strip()]

    @property
    def daily_report_emails_list(self) -> list[str]:
        if not self.DAILY_REPORT_EMAILS:
            return []
        return [e.strip() for e in self.DAILY_REPORT_EMAILS.split(",") if e.strip()]


def get_settings() -> Settings:
    """Singleton accessor — import this in other modules."""
    return Settings()
