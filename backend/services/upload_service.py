"""
Upload service — upload validated dist/ to a Google Cloud Storage bucket.

Handles MIME type detection, cache-control headers, parallel uploads via
concurrent.futures, and progress reporting through a log callback.
"""

from __future__ import annotations

import logging
import mimetypes
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from google.cloud import storage

from config import Settings
from models.enums import DeploymentMode

logger = logging.getLogger("webdeploy.upload_service")

# ── MIME type map — overrides / additions beyond Python's built-in map ────
_CONTENT_TYPE_MAP: dict[str, str] = {
    ".html": "text/html",
    ".htm": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".eot": "application/vnd.ms-fontobject",
    ".otf": "font/otf",
    ".xml": "application/xml",
    ".txt": "text/plain",
    ".map": "application/json",
    ".webmanifest": "application/manifest+json",
}

# HTML files get no-cache so users always see the latest deployment.
# All other assets (JS, CSS, images, fonts) get long cache with public access.
_CACHE_CONTROL_HTML = "no-cache, no-store, must-revalidate"
_CACHE_CONTROL_ASSETS = "public, max-age=3600"

# Max parallel uploads
_MAX_WORKERS = 10


class UploadService:
    """Upload a dist/ directory to Google Cloud Storage."""

    def __init__(
        self,
        log_callback: Optional[Callable] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._log = log_callback or (lambda msg, **kw: None)
        self._settings = settings or Settings()
        self._client: Optional[storage.Client] = None

    # ── Public API ────────────────────────────────────────────────────

    def upload(
        self,
        dist_path: str,
        bucket_name: str,
        website_name: str,
        mode: DeploymentMode,
    ) -> int:
        """
        Upload all files in *dist_path* to the GCS bucket.

        Parameters
        ----------
        dist_path : str
            Absolute path to the built ``dist/`` directory.
        bucket_name : str
            GCS bucket name.
        website_name : str
            Deployment name (used as prefix in demo mode).
        mode : DeploymentMode
            demo or prod — determines the upload prefix.

        Returns
        -------
        int
            Number of files uploaded.

        Raises
        ------
        RuntimeError
            If the upload encounters a critical failure.
        """
        self._log(f"Starting upload to gs://{bucket_name}/", level="INFO", step="UPLOAD")

        # Determine the object name prefix
        prefix = f"{website_name}/" if mode == DeploymentMode.DEMO else ""
        self._log(
            f"Upload prefix: '{prefix}' (mode={mode.value})",
            level="INFO",
            step="UPLOAD",
        )

        # Collect files to upload
        file_list = self._collect_files(dist_path)
        total = len(file_list)
        if total == 0:
            raise RuntimeError(f"No files found in dist directory: {dist_path}")

        self._log(f"Found {total} file(s) to upload", level="INFO", step="UPLOAD")

        # Get the bucket
        client = self._get_client()
        bucket = client.bucket(bucket_name)

        # Upload in parallel
        uploaded = 0
        failed = 0

        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
            futures = {}
            for rel_path, abs_path in file_list:
                object_name = f"{prefix}{rel_path}"
                future = executor.submit(
                    self._upload_file,
                    bucket=bucket,
                    abs_path=abs_path,
                    object_name=object_name,
                )
                futures[future] = (rel_path, object_name)

            for future in as_completed(futures):
                rel_path, object_name = futures[future]
                try:
                    future.result()
                    uploaded += 1
                    # Log progress every 10 files or on the last file
                    if uploaded % 10 == 0 or uploaded == total:
                        self._log(
                            f"Uploaded {uploaded}/{total} files",
                            level="INFO",
                            step="UPLOAD",
                        )
                except Exception as exc:
                    failed += 1
                    self._log(
                        f"Failed to upload {rel_path}: {exc}",
                        level="ERROR",
                        step="UPLOAD",
                    )

        if failed > 0:
            self._log(
                f"Upload completed with {failed} failure(s) out of {total} files",
                level="WARNING",
                step="UPLOAD",
            )
            if failed == total:
                raise RuntimeError(
                    f"All {total} uploads failed. Check GCS credentials and bucket permissions."
                )

        self._log(
            f"Upload complete: {uploaded}/{total} files to gs://{bucket_name}/{prefix}",
            level="INFO",
            step="UPLOAD",
        )
        return uploaded

    # ── Private helpers ───────────────────────────────────────────────

    def _get_client(self) -> storage.Client:
        """Lazy-init the GCS client."""
        if self._client is None:
            creds_path = self._settings.GOOGLE_APPLICATION_CREDENTIALS
            if creds_path and os.path.isfile(creds_path):
                self._client = storage.Client.from_service_account_json(creds_path)
                self._log("GCS client initialized from service account JSON", level="INFO", step="UPLOAD")
            else:
                # Fall back to Application Default Credentials
                self._client = storage.Client(project=self._settings.PROJECT_ID)
                self._log("GCS client initialized with default credentials", level="INFO", step="UPLOAD")
        return self._client

    def _collect_files(self, dist_path: str) -> list[tuple[str, str]]:
        """
        Walk the dist/ tree and return a list of (relative_path, absolute_path)
        tuples for every file.
        """
        files: list[tuple[str, str]] = []
        for dirpath, _, filenames in os.walk(dist_path):
            for filename in filenames:
                abs_path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(abs_path, dist_path)
                # Normalise to forward slashes for GCS object names
                rel_path = rel_path.replace(os.sep, "/")
                files.append((rel_path, abs_path))
        return files

    def _upload_file(
        self,
        bucket: storage.Bucket,
        abs_path: str,
        object_name: str,
    ) -> None:
        """Upload a single file to GCS with correct Content-Type and Cache-Control."""
        content_type = self._detect_content_type(abs_path)
        cache_control = self._get_cache_control(abs_path)

        blob = bucket.blob(object_name)
        blob.content_type = content_type
        blob.cache_control = cache_control

        blob.upload_from_filename(abs_path)

    @staticmethod
    def _detect_content_type(file_path: str) -> str:
        """Determine the MIME type for a file based on its extension."""
        _, ext = os.path.splitext(file_path)
        ext = ext.lower()

        # Check our explicit map first
        if ext in _CONTENT_TYPE_MAP:
            return _CONTENT_TYPE_MAP[ext]

        # Fall back to Python's mimetypes module
        guessed, _ = mimetypes.guess_type(file_path)
        return guessed or "application/octet-stream"

    @staticmethod
    def _get_cache_control(file_path: str) -> str:
        """
        Return the appropriate Cache-Control header.
        HTML files get no-cache; everything else gets public caching.
        """
        _, ext = os.path.splitext(file_path)
        if ext.lower() in (".html", ".htm"):
            return _CACHE_CONTROL_HTML
        return _CACHE_CONTROL_ASSETS
