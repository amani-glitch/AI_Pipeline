"""
Cloud Run API routes — browse the project's services and resolve a pasted
service URL, so an existing service can be linked to a domain.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from api.dependencies import require_approved
from config import Settings, get_settings
from services.cloud_run_catalog import CloudRunCatalog, CloudRunLookupError

logger = logging.getLogger("webdeploy.api.cloudrun")

router = APIRouter(prefix="/api/cloud-run", tags=["cloud-run"])


# ── Response schemas ─────────────────────────────────────────────────

class RunServiceResponse(BaseModel):
    name: str
    region: str
    url: str
    ingress: str = "INGRESS_TRAFFIC_ALL"
    lb_reachable: bool = True
    updated_at: str = ""
    last_image: str = ""
    # Filled in by /resolve only: whether anonymous callers (the load
    # balancer) may already invoke it.
    public: bool | None = None


class RunServiceListResponse(BaseModel):
    services: list[RunServiceResponse]


class ResolveRequest(BaseModel):
    reference: str
    region: str | None = None


# ═══════════════════════════════════════════════════════════════════════
#  GET /api/cloud-run/services
# ═══════════════════════════════════════════════════════════════════════

@router.get("/services", response_model=RunServiceListResponse)
async def list_cloud_run_services(
    q: str | None = Query(None, description="Filter on the service name"),
    user=Depends(require_approved),
    settings: Settings = Depends(get_settings),
) -> RunServiceListResponse:
    """Every Cloud Run service of the platform's project, all regions.

    These are the services a deployment can be pointed at: a serverless NEG
    can only reference a service in its own project.
    """
    try:
        services = await asyncio.to_thread(CloudRunCatalog(settings).list_services)
    except Exception as exc:
        logger.exception("Failed to list Cloud Run services")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Impossible de lister les services Cloud Run : {exc}",
        ) from exc

    needle = (q or "").strip().lower()
    if needle:
        services = [s for s in services if needle in s.name.lower() or needle in s.url.lower()]
    return RunServiceListResponse(
        services=[RunServiceResponse(**s.as_dict()) for s in services],
    )


# ═══════════════════════════════════════════════════════════════════════
#  POST /api/cloud-run/resolve
# ═══════════════════════════════════════════════════════════════════════

@router.post("/resolve", response_model=RunServiceResponse)
async def resolve_cloud_run_service(
    body: ResolveRequest,
    user=Depends(require_approved),
    settings: Settings = Depends(get_settings),
) -> RunServiceResponse:
    """Resolve a pasted ``.run.app`` URL (or a service name) to the service
    it belongs to, and report whether the load balancer can already reach it."""
    catalog = CloudRunCatalog(settings)

    def _resolve():
        service = catalog.resolve(body.reference, body.region)
        try:
            public = catalog.is_public(service.name, service.region)
        except Exception:
            # Reading the IAM policy needs run.services.getIamPolicy; without
            # it the deploy can still grant access, so this stays advisory.
            logger.warning("Could not read IAM policy of %s", service.resource, exc_info=True)
            public = None
        return service, public

    try:
        service, public = await asyncio.to_thread(_resolve)
    except CloudRunLookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        logger.exception("Failed to resolve Cloud Run service %r", body.reference)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Impossible de vérifier ce service Cloud Run : {exc}",
        ) from exc

    return RunServiceResponse(**service.as_dict(), public=public)
