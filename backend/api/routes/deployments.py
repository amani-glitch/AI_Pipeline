"""
Deployment API routes — create, list, inspect, and stream logs for deployments.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import uuid
import zipfile
from typing import Optional

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from config import Settings, get_settings
from db.database import get_db
from db import crud
from models.deployment import (
    DeploymentConfig,
    DeploymentCreateResponse,
    DeploymentResponse,
    LogEntry,
)
from models.enums import DeploymentMode, UserRole
from services.pipeline_orchestrator import PipelineOrchestrator
from services.zip_backup import backup_zip
from api.dependencies import require_approved, require_deploy_permission

logger = logging.getLogger("webdeploy.api.deployments")

router = APIRouter(prefix="/api", tags=["deployments"])

# Pre-compiled slug validation pattern
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")


# ═══════════════════════════════════════════════════════════════════════
#  POST /api/deploy — create a new deployment
# ═══════════════════════════════════════════════════════════════════════

@router.post("/deploy", response_model=DeploymentCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_deployment(
    zip_file: Optional[UploadFile] = File(None),
    files: list[UploadFile] = File(default=[]),
    mode: str = Form(...),
    website_name: str = Form(...),
    domain: Optional[str] = Form(None),
    notification_emails: Optional[str] = Form(None),
    deployer_first_name: str = Form(""),
    deployer_last_name: str = Form(""),
    deployer_email: str = Form(""),
    ai_enabled: str = Form("false"),
    domain_purchase_confirmed: str = Form("false"),
    scheduled_at: Optional[str] = Form(None),
    user=Depends(require_approved),
    db = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DeploymentCreateResponse:
    """
    Accept a ZIP upload, raw file(s), or a folder and kick off the
    deployment pipeline.

    Upload types:
    - ``zip_file``: a single .zip archive (existing behaviour)
    - ``files``: one or more raw files (single HTML or folder via
      ``webkitdirectory``).  These are packaged into a ZIP on the fly.

    The pipeline runs asynchronously in the background; the response is
    returned immediately with a ``deployment_id`` the client can poll or
    subscribe to via WebSocket.
    """
    # ── Validate mode ─────────────────────────────────────────────────
    if mode not in (DeploymentMode.DEMO.value, DeploymentMode.PROD.value, DeploymentMode.CLOUDRUN.value):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid mode '{mode}'. Must be 'demo', 'prod', or 'cloudrun'.",
        )

    # ── Enforce deploy permissions by role ─────────────────────────────
    require_deploy_permission(mode, user)

    # ── Enforce quotas ─────────────────────────────────────────────────
    from api.routes.quotas import check_quota
    check_quota(db, user)

    # ── Auto-fill deployer info from user profile ──────────────────────
    deployer_email = deployer_email.strip() or getattr(user, "email", "")
    if not deployer_first_name.strip() and hasattr(user, "display_name") and user.display_name:
        parts = user.display_name.split(" ", 1)
        deployer_first_name = parts[0]
        deployer_last_name = parts[1] if len(parts) > 1 else deployer_last_name

    # ── Validate website_name is slug-safe ────────────────────────────
    website_name_lower = website_name.lower().strip()
    if len(website_name_lower) < 2 or not _SLUG_RE.match(website_name_lower):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid website_name '{website_name}'. "
                "Must be lowercase, alphanumeric with hyphens, and at least 2 characters "
                "(e.g. 'my-site')."
            ),
        )

    # ── Validate domain for prod mode ─────────────────────────────────
    if mode == DeploymentMode.PROD.value and not domain:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A domain is required for production deployments.",
        )

    # ── Determine upload type and validate ────────────────────────────
    has_zip = zip_file is not None and zip_file.filename
    has_files = len(files) > 0 and any(f.filename for f in files)

    if not has_zip and not has_files:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No file uploaded. Provide either a zip_file or one or more files.",
        )

    # ── Generate deployment ID ────────────────────────────────────────
    deployment_id = str(uuid.uuid4())
    zip_dest = settings.upload_path / f"{deployment_id}.zip"
    stored_zip_filename: str

    if has_zip:
        # ── ZIP upload — existing flow ────────────────────────────────
        if not zip_file.filename.lower().endswith(".zip"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Uploaded file must be a .zip archive.",
            )

        try:
            async with aiofiles.open(str(zip_dest), "wb") as f:
                while chunk := await zip_file.read(1024 * 1024):  # 1 MB chunks
                    await f.write(chunk)
        except Exception as exc:
            logger.exception("Failed to save uploaded ZIP for deployment %s", deployment_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to save uploaded file: {exc}",
            ) from exc

        stored_zip_filename = zip_file.filename
        logger.info(
            "Saved ZIP for deployment %s (%s) -> %s",
            deployment_id, zip_file.filename, zip_dest,
        )
    else:
        # ── Raw file(s) upload — package into ZIP on the fly ──────────
        try:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                # Detect common folder prefix from webkitdirectory uploads
                # (e.g. "myFolder/index.html", "myFolder/css/style.css")
                # so we can strip it and keep a clean structure in the ZIP.
                names = [upload.filename for upload in files if upload.filename]
                common_prefix = ""
                if len(names) > 1:
                    parts_list = [n.replace("\\", "/").split("/") for n in names]
                    if all(len(p) > 1 for p in parts_list):
                        first_segment = parts_list[0][0]
                        if all(p[0] == first_segment for p in parts_list):
                            common_prefix = first_segment + "/"

                for upload in files:
                    content = await upload.read()
                    arcname = upload.filename.replace("\\", "/")
                    if common_prefix and arcname.startswith(common_prefix):
                        arcname = arcname[len(common_prefix):]
                    if not arcname:
                        continue
                    zf.writestr(arcname, content)
            buf.seek(0)

            async with aiofiles.open(str(zip_dest), "wb") as f:
                await f.write(buf.getvalue())
        except Exception as exc:
            logger.exception("Failed to package files for deployment %s", deployment_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to package uploaded files: {exc}",
            ) from exc

        stored_zip_filename = f"{website_name_lower}-uploaded-files.zip"
        logger.info(
            "Packaged %d file(s) into ZIP for deployment %s -> %s",
            len(files), deployment_id, zip_dest,
        )

    # ── Backup ZIP to GCS (resilience across container restarts) ─────
    try:
        await asyncio.to_thread(backup_zip, settings, deployment_id, zip_dest)
    except Exception:
        logger.warning("GCS ZIP backup failed for %s (non-fatal)", deployment_id, exc_info=True)

    # ── Parse notification emails ─────────────────────────────────────
    email_list: list[str] = []
    if notification_emails:
        email_list = [e.strip() for e in notification_emails.split(",") if e.strip()]
    # Auto-include deployer email in notifications
    deployer_email_stripped = deployer_email.strip()
    if deployer_email_stripped and deployer_email_stripped not in email_list:
        email_list.append(deployer_email_stripped)

    # Merge deployer email into the stored notification_emails string
    all_emails_str = ", ".join(email_list) if email_list else ""

    # ── Parse AI toggle ───────────────────────────────────────────────
    ai_enabled_bool = ai_enabled.lower() == "true"
    domain_purchase_confirmed_bool = domain_purchase_confirmed.lower() == "true"

    # ── Create DB record ──────────────────────────────────────────────
    crud.create_deployment(
        db,
        deployment_id=deployment_id,
        website_name=website_name_lower,
        mode=mode,
        domain=domain,
        notification_emails=all_emails_str,
        zip_filename=stored_zip_filename,
        deployer_first_name=deployer_first_name.strip(),
        deployer_last_name=deployer_last_name.strip(),
        deployer_email=deployer_email_stripped,
        ai_enabled=ai_enabled_bool,
    )

    # ── Build pipeline config ─────────────────────────────────────────
    deploy_config = DeploymentConfig(
        mode=DeploymentMode(mode),
        website_name=website_name_lower,
        domain=domain,
        notification_emails=email_list,
        deployer_first_name=deployer_first_name.strip(),
        deployer_last_name=deployer_last_name.strip(),
        deployer_email=deployer_email_stripped,
        ai_enabled=ai_enabled_bool,
        domain_purchase_confirmed=domain_purchase_confirmed_bool,
    )

    # ── Handle scheduled deployment ──────────────────────────────────
    if scheduled_at:
        from datetime import datetime as dt
        try:
            schedule_time = dt.fromisoformat(scheduled_at.replace("Z", "+00:00"))
            if schedule_time.tzinfo is None:
                from datetime import timezone as tz
                schedule_time = schedule_time.replace(tzinfo=tz.utc)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid scheduled_at format: '{scheduled_at}'. Use ISO 8601.",
            )

        # Update the deployment record with scheduled status
        db.collection("deployments").document(deployment_id).update({
            "status": "scheduled",
            "scheduled_at": schedule_time,
        })
        logger.info("Deployment %s scheduled for %s", deployment_id, schedule_time.isoformat())
        return DeploymentCreateResponse(deployment_id=deployment_id, status="scheduled")

    # ── Launch the pipeline in the background ─────────────────────────
    orchestrator = PipelineOrchestrator(settings)
    asyncio.create_task(
        orchestrator.run(deployment_id, str(zip_dest), deploy_config),
        name=f"pipeline-{deployment_id[:8]}",
    )

    logger.info("Deployment %s queued (mode=%s, website=%s)", deployment_id, mode, website_name_lower)
    return DeploymentCreateResponse(deployment_id=deployment_id, status="queued")


# ═══════════════════════════════════════════════════════════════════════
#  DELETE /api/deployments/{deployment_id} — delete deployment + GCP resources
# ═══════════════════════════════════════════════════════════════════════

@router.delete("/deployments/{deployment_id}", status_code=status.HTTP_200_OK)
async def delete_deployment(
    deployment_id: str,
    user=Depends(require_approved),
    db=Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """
    Delete a deployment and clean up its GCP resources.

    Depending on the deployment mode:
    - **demo**: removes path rule from URL map, backend bucket, storage bucket
    - **cloudrun**: removes Cloud Run service and Artifact Registry images
    - **prod**: only deletes the DB record (prod resources require manual cleanup)
    """
    record = crud.get_deployment(db, deployment_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deployment '{deployment_id}' not found.",
        )

    # Only owner or admin can delete
    if user.role != UserRole.ADMIN.value:
        if getattr(record, "deployer_email", "") != getattr(user, "email", ""):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only delete your own deployments.",
            )

    mode = record.mode
    website_name = record.website_name
    errors_list = []

    async def _noop_log(message: str) -> None:
        logger.info("[DELETE %s] %s", deployment_id[:8], message)

    # Clean up GCP resources based on mode
    if mode == DeploymentMode.DEMO.value:
        try:
            from infra.demo_deployer import DemoDeployer
            deployer = DemoDeployer(config=settings, log_callback=_noop_log)
            await deployer.delete(website_name=website_name)
        except Exception as exc:
            logger.exception("Failed to delete demo resources for %s", deployment_id)
            errors_list.append(f"Demo cleanup error: {exc}")

    elif mode == DeploymentMode.SUBDOMAIN.value:
        try:
            from infra.subdomain_deployer import SubdomainDeployer
            deployer = SubdomainDeployer(config=settings, log_callback=_noop_log)
            await deployer.delete(website_name=website_name)
        except Exception as exc:
            logger.exception("Failed to delete subdomain resources for %s", deployment_id)
            errors_list.append(f"Subdomain cleanup error: {exc}")

    elif mode == DeploymentMode.CLOUDRUN.value:
        try:
            from infra.cloudrun_deployer import CloudRunDeployer
            deployer = CloudRunDeployer(config=settings, log_callback=_noop_log)
            await deployer.delete(website_name=website_name)
        except Exception as exc:
            logger.exception("Failed to delete Cloud Run resources for %s", deployment_id)
            errors_list.append(f"Cloud Run cleanup error: {exc}")

    elif mode == DeploymentMode.PROD.value:
        # Prod has too many interdependent resources — flag for manual cleanup
        logger.info("Prod deployment %s — skipping automatic resource cleanup", deployment_id)

    # Always delete the DB record
    crud.delete_deployment(db, deployment_id)

    result = {
        "deleted": True,
        "deployment_id": deployment_id,
        "mode": mode,
        "website_name": website_name,
    }
    if errors_list:
        result["warnings"] = errors_list

    return result


# ═══════════════════════════════════════════════════════════════════════
#  GET /api/deployments — list all deployments
# ═══════════════════════════════════════════════════════════════════════

@router.get("/deployments", response_model=list[DeploymentResponse])
def list_deployments(
    limit: int = 100,
    offset: int = 0,
    user=Depends(require_approved),
    db = Depends(get_db),
) -> list[DeploymentResponse]:
    """Return deployments. Admins see all; others see only their own."""
    records = crud.list_deployments(db, limit=limit, offset=offset)
    if user.role != UserRole.ADMIN.value:
        user_email = getattr(user, "email", "")
        records = [r for r in records if getattr(r, "deployer_email", "") == user_email]
    return [DeploymentResponse.from_record(r) for r in records]


# ═══════════════════════════════════════════════════════════════════════
#  GET /api/deployments/{deployment_id} — single deployment detail
# ═══════════════════════════════════════════════════════════════════════

@router.get("/deployments/{deployment_id}", response_model=DeploymentResponse)
def get_deployment(
    deployment_id: str,
    db = Depends(get_db),
) -> DeploymentResponse:
    """Return details for a single deployment."""
    record = crud.get_deployment(db, deployment_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deployment '{deployment_id}' not found.",
        )
    return DeploymentResponse.from_record(record)


# ═══════════════════════════════════════════════════════════════════════
#  GET /api/deployments/{deployment_id}/logs — deployment logs
# ═══════════════════════════════════════════════════════════════════════

@router.get("/deployments/{deployment_id}/logs", response_model=list[LogEntry])
def get_deployment_logs(
    deployment_id: str,
    db = Depends(get_db),
) -> list[LogEntry]:
    """Return all log entries for a deployment, ordered by timestamp."""
    # Verify deployment exists
    record = crud.get_deployment(db, deployment_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deployment '{deployment_id}' not found.",
        )

    log_records = crud.get_logs(db, deployment_id)
    return [
        LogEntry(
            timestamp=lr.timestamp,
            level=lr.level,
            step=lr.step,
            message=lr.message,
        )
        for lr in log_records
    ]
