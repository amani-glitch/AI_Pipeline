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

    # Prod infrastructure toggles
    PROD_AUTO_REGISTER_DOMAINS: bool = False
    PROD_AUTO_CREATE_DNS_ZONE: bool = True
    PROD_AUTO_CREATE_SSL_CERT: bool = False

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

    # ── App ──────────────────────────────────────────────────────────
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
    def notification_emails_list(self) -> list[str]:
        if not self.NOTIFICATION_TO_EMAILS:
            return []
        return [e.strip() for e in self.NOTIFICATION_TO_EMAILS.split(",") if e.strip()]


def get_settings() -> Settings:
    """Singleton accessor — import this in other modules."""
    return Settings()
