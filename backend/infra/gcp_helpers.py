"""
Shared GCP utilities for the WebDeploy platform.

Provides common helpers for resource naming, authentication, operation polling,
and bucket/backend-bucket name generation used by both demo and prod deployers.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import google.auth
from google.oauth2 import service_account
from googleapiclient.discovery import Resource

logger = logging.getLogger(__name__)


# =====================================================================
#  Resource Naming
# =====================================================================

def safe_name(name: str) -> str:
    """Convert a domain or arbitrary name to a GCP-safe resource name.

    Rules applied:
    - Lowercase the entire string.
    - Replace dots, underscores, and consecutive non-alphanumeric chars with a
      single hyphen.
    - Strip leading/trailing hyphens.
    - Truncate to 63 characters (GCP resource name limit).
    """
    result = name.lower()
    # Replace dots and underscores with hyphens
    result = result.replace(".", "-").replace("_", "-")
    # Collapse any remaining non-alphanumeric sequences into a single hyphen
    result = re.sub(r"[^a-z0-9-]+", "-", result)
    # Collapse consecutive hyphens
    result = re.sub(r"-{2,}", "-", result)
    # Strip leading/trailing hyphens
    result = result.strip("-")
    # Truncate to 63 chars (GCP limit)
    result = result[:63]
    # Strip any trailing hyphen introduced by truncation
    result = result.rstrip("-")

    if not result:
        raise ValueError(f"Cannot derive a safe GCP name from input: {name!r}")

    logger.debug("safe_name(%r) -> %r", name, result)
    return result


# =====================================================================
#  Authentication
# =====================================================================

def get_credentials(service_account_path: str):
    """Load Google credentials — from a key file if it exists, otherwise ADC.

    On Cloud Run the attached service account provides credentials automatically
    via Application Default Credentials (ADC).  When running locally a JSON key
    file is used instead.

    Args:
        service_account_path: Path to a service-account JSON key file.
            If the file does not exist, ADC is used as a fallback.

    Returns:
        A scoped ``google.oauth2.credentials.Credentials`` instance.
    """
    import os
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]

    # Try explicit key file first
    if service_account_path and os.path.isfile(service_account_path):
        try:
            credentials = service_account.Credentials.from_service_account_file(
                service_account_path,
                scopes=scopes,
            )
            logger.info("Loaded GCP credentials from %s (project: %s)",
                         service_account_path, credentials.project_id)
            return credentials
        except Exception as exc:
            logger.warning("Failed to load key file %s: %s — falling back to ADC",
                           service_account_path, exc)

    # Fallback: Application Default Credentials (Cloud Run, GCE, local gcloud)
    credentials, project = google.auth.default(scopes=scopes)
    logger.info("Using Application Default Credentials (project: %s)", project)
    return credentials


# =====================================================================
#  Operation Polling
# =====================================================================

def wait_for_global_operation(
    compute: Resource,
    project_id: str,
    operation: str,
    timeout: int = 300,
) -> dict[str, Any]:
    """Poll until a global GCP Compute Engine operation completes.

    Args:
        compute: An authenticated ``googleapiclient`` compute resource.
        project_id: The GCP project ID.
        operation: The operation name returned by an API call.
        timeout: Maximum seconds to wait before raising ``TimeoutError``.

    Returns:
        The final operation resource dict.

    Raises:
        TimeoutError: If the operation does not complete within *timeout* seconds.
        RuntimeError: If the operation finishes with errors.
    """
    logger.info("Waiting for global operation %s (timeout=%ds)...", operation, timeout)
    deadline = time.monotonic() + timeout
    poll_interval = 2.0  # start with 2s, increase gradually

    while True:
        result = (
            compute.globalOperations()
            .get(project=project_id, operation=operation)
            .execute()
        )

        if result.get("status") == "DONE":
            if "error" in result:
                errors = result["error"].get("errors", [])
                error_messages = "; ".join(
                    e.get("message", str(e)) for e in errors
                )
                logger.error("Operation %s failed: %s", operation, error_messages)
                raise RuntimeError(
                    f"GCP operation {operation} failed: {error_messages}"
                )
            logger.info("Operation %s completed successfully.", operation)
            return result

        if time.monotonic() >= deadline:
            logger.error("Operation %s timed out after %ds.", operation, timeout)
            raise TimeoutError(
                f"GCP operation {operation} did not complete within {timeout}s"
            )

        time.sleep(poll_interval)
        # Gradual back-off up to 10s
        poll_interval = min(poll_interval * 1.3, 10.0)


# =====================================================================
#  Bucket / Backend-Bucket Name Generators
# =====================================================================

def get_bucket_name(website_name: str, mode: str) -> str:
    """Generate a Cloud Storage bucket name.

    Args:
        website_name: The logical website name (e.g. ``"my-site"``).
        mode: ``"demo"`` or ``"prod"``.

    Returns:
        A GCP-safe bucket name.
        - Demo: ``"demo-{safe_name}-bucket-demo"``
        - Prod: ``"{safe_name}-bucket-prod"``
    """
    sname = safe_name(website_name)
    if mode == "demo":
        bucket = f"demo-{sname}-bucket-demo"
    else:
        bucket = f"{sname}-bucket-prod"

    logger.debug("get_bucket_name(%r, %r) -> %r", website_name, mode, bucket)
    return bucket


def get_backend_bucket_name(website_name: str, mode: str) -> str:
    """Generate a Compute Engine backend-bucket name.

    Args:
        website_name: The logical website name.
        mode: ``"demo"`` or ``"prod"``.

    Returns:
        A GCP-safe backend-bucket name.
        - Demo: ``"demo-{safe_name}-backend-demo"``
        - Prod: ``"{safe_name}-backend-prod"``
    """
    sname = safe_name(website_name)
    if mode == "demo":
        backend = f"demo-{sname}-backend-demo"
    else:
        backend = f"{sname}-backend-prod"

    logger.debug("get_backend_bucket_name(%r, %r) -> %r", website_name, mode, backend)
    return backend
