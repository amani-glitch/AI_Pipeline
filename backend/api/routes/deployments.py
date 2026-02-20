"""
Deployment API routes — create, list, inspect, and stream logs for deployments.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
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
from models.enums import DeploymentMode
from services.pipeline_orchestrator import PipelineOrchestrator

logger = logging.getLogger("webdeploy.api.deployments")

router = APIRouter(prefix="/api", tags=["deployments"])

# Pre-compiled slug validation pattern
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")


# ═══════════════════════════════════════════════════════════════════════
#  POST /api/deploy — create a new deployment
# ═══════════════════════════════════════════════════════════════════════

@router.post("/deploy", response_model=DeploymentCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_deployment(
    zip_file: UploadFile = File(...),
    mode: str = Form(...),
    website_name: str = Form(...),
    domain: Optional[str] = Form(None),
    notification_emails: Optional[str] = Form(None),
    db = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DeploymentCreateResponse:
    """
    Accept a ZIP upload and kick off the deployment pipeline.

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

    # ── Validate ZIP file ─────────────────────────────────────────────
    if not zip_file.filename or not zip_file.filename.lower().endswith(".zip"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded file must be a .zip archive.",
        )

    # ── Generate deployment ID and persist the ZIP ────────────────────
    deployment_id = str(uuid.uuid4())
    zip_dest = settings.upload_path / f"{deployment_id}.zip"

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

    logger.info(
        "Saved ZIP for deployment %s (%s) -> %s",
        deployment_id, zip_file.filename, zip_dest,
    )

    # ── Parse notification emails ─────────────────────────────────────
    email_list: list[str] = []
    if notification_emails:
        email_list = [e.strip() for e in notification_emails.split(",") if e.strip()]

    # ── Create DB record ──────────────────────────────────────────────
    crud.create_deployment(
        db,
        deployment_id=deployment_id,
        website_name=website_name_lower,
        mode=mode,
        domain=domain,
        notification_emails=notification_emails or "",
        zip_filename=zip_file.filename,
    )

    # ── Build pipeline config ─────────────────────────────────────────
    deploy_config = DeploymentConfig(
        mode=DeploymentMode(mode),
        website_name=website_name_lower,
        domain=domain,
        notification_emails=email_list,
    )

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
    db = Depends(get_db),
) -> list[DeploymentResponse]:
    """Return all deployments, most recent first."""
    records = crud.list_deployments(db, limit=limit, offset=offset)
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
