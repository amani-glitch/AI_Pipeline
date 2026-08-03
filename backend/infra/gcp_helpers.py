"""
Shared GCP utilities for the WebDeploy platform.

Provides common helpers for resource naming, authentication, operation polling,
and bucket/backend-bucket name generation used by both demo and prod deployers.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Callable

import google.auth
from google.oauth2 import service_account
from googleapiclient import errors as api_errors
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
#  URL Map Mutation (shared, concurrency-safe)
# =====================================================================

# Every deploy/delete that touches a URL map (e.g. the demo LB's "test-lb")
# does get-modify-patch against the *same* resource. One lock per URL map
# name serializes same-process callers so they queue instead of racing.
_url_map_locks: dict[str, threading.Lock] = {}
_url_map_locks_guard = threading.Lock()


def _get_url_map_lock(url_map_name: str) -> threading.Lock:
    with _url_map_locks_guard:
        lock = _url_map_locks.get(url_map_name)
        if lock is None:
            lock = _url_map_locks[url_map_name] = threading.Lock()
        return lock


def mutate_url_map(
    compute: Resource,
    project_id: str,
    url_map_name: str,
    mutate_fn: Callable[[dict[str, Any]], bool],
    max_retries: int = 5,
    base_delay: float = 5.0,
) -> None:
    """Fetch a URL map, apply ``mutate_fn`` to it, and patch it back.

    A URL map is a single shared resource that many deploys/deletes mutate
    concurrently via get-modify-patch, which races in two ways GCP surfaces
    as errors rather than serializing itself:

    - ``412 Invalid fingerprint``: another patch landed between our GET and
      PATCH, invalidating the optimistic-lock fingerprint we sent.
    - ``400 resourceNotReady``: the map is still applying a just-submitted
      change and briefly refuses new patches.

    Both are transient — re-fetching the map (for a fresh fingerprint) and
    retrying resolves them, which is why the retry loop re-runs ``mutate_fn``
    against a fresh GET each attempt rather than reusing the stale body. A
    per-process lock on ``url_map_name`` additionally serializes same-process
    callers so they queue instead of racing and retrying.

    Args:
        mutate_fn: Receives the freshly-fetched URL map body and mutates it
            in place. Returns whether a patch is actually needed — ``False``
            short-circuits as a no-op (e.g. the desired state already
            exists), skipping the PATCH call entirely.
    """
    with _get_url_map_lock(url_map_name):
        for attempt in range(1, max_retries + 1):
            url_map = (
                compute.urlMaps().get(project=project_id, urlMap=url_map_name).execute()
            )
            if not mutate_fn(url_map):
                return
            try:
                operation = (
                    compute.urlMaps()
                    .patch(project=project_id, urlMap=url_map_name, body=url_map)
                    .execute()
                )
                wait_for_global_operation(compute, project_id, operation["name"])
                return
            except api_errors.HttpError as err:
                retryable = err.resp.status == 412 or (
                    err.resp.status == 400 and "resourceNotReady" in str(err)
                )
                if retryable and attempt < max_retries:
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "URL map '%s' patch conflict (attempt %d/%d, status %s) — "
                        "retrying in %ds...",
                        url_map_name, attempt, max_retries, err.resp.status, delay,
                    )
                    time.sleep(delay)
                    continue
                raise


# =====================================================================
#  Bucket / Backend-Bucket Name Generators
# =====================================================================

def get_bucket_name(website_name: str, mode: str) -> str:
    """Generate a Cloud Storage bucket name.

    Args:
        website_name: The logical website name (e.g. ``"my-site"``).
        mode: ``"demo"``, ``"subdomain"``, or ``"prod"``.

    Returns:
        A GCP-safe bucket name.
        - Demo:      ``"demo-{safe_name}-bucket-demo"``
        - Subdomain: ``"sub-{safe_name}-bucket-demo"``
        - Prod:      ``"{safe_name}-bucket-prod"``
    """
    sname = safe_name(website_name)
    if mode == "demo":
        bucket = f"demo-{sname}-bucket-demo"
    elif mode == "subdomain":
        bucket = f"sub-{sname}-bucket-demo"
    else:
        bucket = f"{sname}-bucket-prod"

    logger.debug("get_bucket_name(%r, %r) -> %r", website_name, mode, bucket)
    return bucket


def get_backend_bucket_name(website_name: str, mode: str) -> str:
    """Generate a Compute Engine backend-bucket name.

    Args:
        website_name: The logical website name.
        mode: ``"demo"``, ``"subdomain"``, or ``"prod"``.

    Returns:
        A GCP-safe backend-bucket name.
        - Demo:      ``"demo-{safe_name}-backend-demo"``
        - Subdomain: ``"sub-{safe_name}-backend-demo"``
        - Prod:      ``"{safe_name}-backend-prod"``
    """
    sname = safe_name(website_name)
    if mode == "demo":
        backend = f"demo-{sname}-backend-demo"
    elif mode == "subdomain":
        backend = f"sub-{sname}-backend-demo"
    else:
        backend = f"{sname}-backend-prod"

    logger.debug("get_backend_bucket_name(%r, %r) -> %r", website_name, mode, backend)
    return backend
