"""Quota management API — define and enforce per-user deployment limits."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from db.database import get_db
from db import crud
from api.dependencies import require_admin, require_approved

logger = logging.getLogger("webdeploy.api.quotas")

router = APIRouter(prefix="/api/quotas", tags=["quotas"])


# ── Models ────────────────────────────────────────────────────────────

class QuotaConfig(BaseModel):
    """Quota configuration for a user or role."""
    max_deployments_per_day: int = 10
    max_zip_size_mb: int = 500
    max_concurrent_deployments: int = 3
    max_total_deployments: int = -1  # -1 = unlimited


class QuotaUpdate(BaseModel):
    """Partial update for quotas."""
    max_deployments_per_day: Optional[int] = None
    max_zip_size_mb: Optional[int] = None
    max_concurrent_deployments: Optional[int] = None
    max_total_deployments: Optional[int] = None


class QuotaResponse(BaseModel):
    target_type: str  # "user" or "role"
    target_id: str    # uid or role name
    target_label: str  # display name or role label
    config: QuotaConfig


class QuotaUsage(BaseModel):
    """Current usage stats for a user."""
    deployments_today: int = 0
    active_deployments: int = 0
    total_deployments: int = 0
    quota: QuotaConfig
    within_limits: bool = True


# ── Default quotas per role ───────────────────────────────────────────

_DEFAULT_QUOTAS = {
    "simple_user": QuotaConfig(
        max_deployments_per_day=5,
        max_zip_size_mb=100,
        max_concurrent_deployments=1,
        max_total_deployments=50,
    ),
    "super_user": QuotaConfig(
        max_deployments_per_day=20,
        max_zip_size_mb=500,
        max_concurrent_deployments=3,
        max_total_deployments=-1,
    ),
    "admin": QuotaConfig(
        max_deployments_per_day=-1,
        max_zip_size_mb=500,
        max_concurrent_deployments=5,
        max_total_deployments=-1,
    ),
}


# ═══════════════════════════════════════════════════════════════════════
#  GET /api/quotas/defaults — get default quotas per role
# ═══════════════════════════════════════════════════════════════════════

@router.get("/defaults")
def get_defaults(user=Depends(require_admin)):
    """Return default quota configs per role (admin only)."""
    return {
        role: config.model_dump()
        for role, config in _DEFAULT_QUOTAS.items()
    }


# ═══════════════════════════════════════════════════════════════════════
#  GET /api/quotas/role/{role} — get quotas for a role
# ═══════════════════════════════════════════════════════════════════════

@router.get("/role/{role}", response_model=QuotaResponse)
def get_role_quota(role: str, user=Depends(require_admin), db=Depends(get_db)):
    """Get the quota config for a role."""
    stored = crud.get_quota(db, target_type="role", target_id=role)
    if stored:
        config = QuotaConfig(**stored)
    else:
        config = _DEFAULT_QUOTAS.get(role, QuotaConfig())

    role_labels = {"simple_user": "Utilisateur Simple", "super_user": "Super Utilisateur", "admin": "Administrateur"}
    return QuotaResponse(
        target_type="role",
        target_id=role,
        target_label=role_labels.get(role, role),
        config=config,
    )


# ═══════════════════════════════════════════════════════════════════════
#  PUT /api/quotas/role/{role} — set quotas for a role
# ═══════════════════════════════════════════════════════════════════════

@router.put("/role/{role}", response_model=QuotaResponse)
def set_role_quota(
    role: str,
    body: QuotaUpdate,
    user=Depends(require_admin),
    db=Depends(get_db),
):
    """Set quota config for a role (admin only)."""
    valid_roles = {"simple_user", "super_user", "admin"}
    if role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Invalid role: {role}")

    # Get current config as base
    stored = crud.get_quota(db, target_type="role", target_id=role)
    base = QuotaConfig(**(stored or _DEFAULT_QUOTAS.get(role, QuotaConfig()).model_dump()))

    # Merge updates
    updates = body.model_dump(exclude_none=True)
    merged = base.model_dump()
    merged.update(updates)

    crud.set_quota(db, target_type="role", target_id=role, config=merged)

    role_labels = {"simple_user": "Utilisateur Simple", "super_user": "Super Utilisateur", "admin": "Administrateur"}
    return QuotaResponse(
        target_type="role",
        target_id=role,
        target_label=role_labels.get(role, role),
        config=QuotaConfig(**merged),
    )


# ═══════════════════════════════════════════════════════════════════════
#  GET /api/quotas/user/{uid} — get quotas for a specific user
# ═══════════════════════════════════════════════════════════════════════

@router.get("/user/{uid}", response_model=QuotaResponse)
def get_user_quota(uid: str, user=Depends(require_admin), db=Depends(get_db)):
    """Get the quota config for a specific user (overrides or falls back to role)."""
    target_user = crud.get_user(db, uid)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found.")

    # Check for user-specific override
    stored = crud.get_quota(db, target_type="user", target_id=uid)
    if stored:
        config = QuotaConfig(**stored)
    else:
        # Fall back to role quota
        role = getattr(target_user, "role", "simple_user") or "simple_user"
        role_stored = crud.get_quota(db, target_type="role", target_id=role)
        config = QuotaConfig(**(role_stored or _DEFAULT_QUOTAS.get(role, QuotaConfig()).model_dump()))

    return QuotaResponse(
        target_type="user",
        target_id=uid,
        target_label=getattr(target_user, "display_name", "") or getattr(target_user, "email", uid),
        config=config,
    )


# ═══════════════════════════════════════════════════════════════════════
#  PUT /api/quotas/user/{uid} — set quotas for a specific user
# ═══════════════════════════════════════════════════════════════════════

@router.put("/user/{uid}", response_model=QuotaResponse)
def set_user_quota(
    uid: str,
    body: QuotaUpdate,
    user=Depends(require_admin),
    db=Depends(get_db),
):
    """Set a user-specific quota override (admin only)."""
    target_user = crud.get_user(db, uid)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found.")

    # Get base config (user override or role default)
    stored = crud.get_quota(db, target_type="user", target_id=uid)
    if stored:
        base = QuotaConfig(**stored)
    else:
        role = getattr(target_user, "role", "simple_user") or "simple_user"
        role_stored = crud.get_quota(db, target_type="role", target_id=role)
        base = QuotaConfig(**(role_stored or _DEFAULT_QUOTAS.get(role, QuotaConfig()).model_dump()))

    updates = body.model_dump(exclude_none=True)
    merged = base.model_dump()
    merged.update(updates)

    crud.set_quota(db, target_type="user", target_id=uid, config=merged)

    return QuotaResponse(
        target_type="user",
        target_id=uid,
        target_label=getattr(target_user, "display_name", "") or getattr(target_user, "email", uid),
        config=QuotaConfig(**merged),
    )


# ═══════════════════════════════════════════════════════════════════════
#  DELETE /api/quotas/user/{uid} — remove user override (revert to role)
# ═══════════════════════════════════════════════════════════════════════

@router.delete("/user/{uid}")
def delete_user_quota(uid: str, user=Depends(require_admin), db=Depends(get_db)):
    """Remove user-specific quota override, reverting to role defaults."""
    crud.delete_quota(db, target_type="user", target_id=uid)
    return {"deleted": True, "uid": uid}


# ═══════════════════════════════════════════════════════════════════════
#  GET /api/quotas/my-usage — current user's quota usage
# ═══════════════════════════════════════════════════════════════════════

@router.get("/my-usage", response_model=QuotaUsage)
def get_my_usage(user=Depends(require_approved), db=Depends(get_db)):
    """Get the current user's quota usage and limits."""
    usage = crud.get_user_quota_usage(db, user.uid, getattr(user, "email", ""))

    # Resolve effective quota
    stored_user = crud.get_quota(db, target_type="user", target_id=user.uid)
    if stored_user:
        quota = QuotaConfig(**stored_user)
    else:
        role = getattr(user, "role", "simple_user") or "simple_user"
        stored_role = crud.get_quota(db, target_type="role", target_id=role)
        quota = QuotaConfig(**(stored_role or _DEFAULT_QUOTAS.get(role, QuotaConfig()).model_dump()))

    within_limits = True
    if quota.max_deployments_per_day >= 0 and usage["deployments_today"] >= quota.max_deployments_per_day:
        within_limits = False
    if quota.max_concurrent_deployments >= 0 and usage["active_deployments"] >= quota.max_concurrent_deployments:
        within_limits = False
    if quota.max_total_deployments >= 0 and usage["total_deployments"] >= quota.max_total_deployments:
        within_limits = False

    return QuotaUsage(
        deployments_today=usage["deployments_today"],
        active_deployments=usage["active_deployments"],
        total_deployments=usage["total_deployments"],
        quota=quota,
        within_limits=within_limits,
    )


# ═══════════════════════════════════════════════════════════════════════
#  Enforcement helper (used by deployments route)
# ═══════════════════════════════════════════════════════════════════════

def check_quota(db, user) -> None:
    """Raise 429 if the user has exceeded their quota. Call before creating deployment."""
    usage = crud.get_user_quota_usage(db, user.uid, getattr(user, "email", ""))

    stored_user = crud.get_quota(db, target_type="user", target_id=user.uid)
    if stored_user:
        quota = QuotaConfig(**stored_user)
    else:
        role = getattr(user, "role", "simple_user") or "simple_user"
        stored_role = crud.get_quota(db, target_type="role", target_id=role)
        quota = QuotaConfig(**(stored_role or _DEFAULT_QUOTAS.get(role, QuotaConfig()).model_dump()))

    if quota.max_deployments_per_day >= 0 and usage["deployments_today"] >= quota.max_deployments_per_day:
        raise HTTPException(
            status_code=429,
            detail=f"Quota exceeded: maximum {quota.max_deployments_per_day} deployments per day.",
        )
    if quota.max_concurrent_deployments >= 0 and usage["active_deployments"] >= quota.max_concurrent_deployments:
        raise HTTPException(
            status_code=429,
            detail=f"Quota exceeded: maximum {quota.max_concurrent_deployments} concurrent deployments.",
        )
    if quota.max_total_deployments >= 0 and usage["total_deployments"] >= quota.max_total_deployments:
        raise HTTPException(
            status_code=429,
            detail=f"Quota exceeded: maximum {quota.max_total_deployments} total deployments.",
        )
