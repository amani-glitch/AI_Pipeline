"""CRUD operations for deployment and log records using Firestore.

All functions accept a Firestore client as the ``db`` parameter (replacing
the previous SQLAlchemy Session).  Return values use ``SimpleNamespace`` to
preserve attribute-style access (``rec.status``) expected by the rest of
the codebase.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Optional

from google.cloud.firestore_v1 import Client as FirestoreClient

from models.enums import DeploymentStatus, LogLevel, PipelineStep, StepStatus

# Collection names
_DEPLOYMENTS = "deployments"
_LOGS = "deployment_logs"


# ── Helpers ────────────────────────────────────────────────────────────

def _doc_to_record(doc_snapshot) -> SimpleNamespace:
    """Convert a Firestore document snapshot to a namespace with attribute access."""
    data = doc_snapshot.to_dict() or {}
    data["id"] = doc_snapshot.id
    return SimpleNamespace(**data)


# ═══════════════════════════════════════════════════════════════════════
#  Deployment CRUD
# ═══════════════════════════════════════════════════════════════════════

def create_deployment(
    db: FirestoreClient,
    *,
    deployment_id: str,
    website_name: str,
    mode: str,
    domain: Optional[str],
    notification_emails: str,
    zip_filename: str,
) -> SimpleNamespace:
    initial_steps = {step.value: StepStatus.PENDING.value for step in PipelineStep}
    data = {
        "website_name": website_name,
        "mode": mode,
        "domain": domain,
        "status": DeploymentStatus.QUEUED.value,
        "current_step": None,
        "steps_status": json.dumps(initial_steps),
        "result_url": None,
        "claude_summary": None,
        "error_message": None,
        "notification_emails": notification_emails,
        "zip_filename": zip_filename,
        "created_at": datetime.now(timezone.utc),
        "started_at": None,
        "completed_at": None,
    }
    db.collection(_DEPLOYMENTS).document(deployment_id).set(data)
    data["id"] = deployment_id
    return SimpleNamespace(**data)


def get_deployment(db: FirestoreClient, deployment_id: str) -> Optional[SimpleNamespace]:
    doc = db.collection(_DEPLOYMENTS).document(deployment_id).get()
    if not doc.exists:
        return None
    return _doc_to_record(doc)


def list_deployments(
    db: FirestoreClient, limit: int = 100, offset: int = 0,
) -> list[SimpleNamespace]:
    query = (
        db.collection(_DEPLOYMENTS)
        .order_by("created_at", direction="DESCENDING")
        .offset(offset)
        .limit(limit)
    )
    return [_doc_to_record(doc) for doc in query.stream()]


def delete_deployment(db: FirestoreClient, deployment_id: str) -> bool:
    """Delete a deployment record and its associated logs."""
    doc_ref = db.collection(_DEPLOYMENTS).document(deployment_id)
    doc = doc_ref.get()
    if not doc.exists:
        return False

    # Delete associated logs
    logs_query = db.collection(_LOGS).where("deployment_id", "==", deployment_id)
    for log_doc in logs_query.stream():
        log_doc.reference.delete()

    doc_ref.delete()
    return True


def update_deployment_status(
    db: FirestoreClient,
    deployment_id: str,
    *,
    status: Optional[str] = None,
    current_step: Optional[str] = None,
    result_url: Optional[str] = None,
    claude_summary: Optional[str] = None,
    error_message: Optional[str] = None,
    started_at: Optional[datetime] = None,
    completed_at: Optional[datetime] = None,
) -> None:
    updates = {}
    if status is not None:
        updates["status"] = status
    if current_step is not None:
        updates["current_step"] = current_step
    if result_url is not None:
        updates["result_url"] = result_url
    if claude_summary is not None:
        updates["claude_summary"] = claude_summary
    if error_message is not None:
        updates["error_message"] = error_message
    if started_at is not None:
        updates["started_at"] = started_at
    if completed_at is not None:
        updates["completed_at"] = completed_at

    if updates:
        db.collection(_DEPLOYMENTS).document(deployment_id).update(updates)


def update_step_status(
    db: FirestoreClient,
    deployment_id: str,
    step: str,
    step_status: str,
) -> None:
    doc_ref = db.collection(_DEPLOYMENTS).document(deployment_id)
    doc = doc_ref.get()
    if not doc.exists:
        return

    data = doc.to_dict()
    steps = {}
    raw = data.get("steps_status")
    if raw:
        try:
            steps = json.loads(raw)
        except json.JSONDecodeError:
            steps = {}
    steps[step] = step_status
    doc_ref.update({"steps_status": json.dumps(steps)})


# ═══════════════════════════════════════════════════════════════════════
#  Log CRUD
# ═══════════════════════════════════════════════════════════════════════

def add_log(
    db: FirestoreClient,
    deployment_id: str,
    message: str,
    level: str = LogLevel.INFO.value,
    step: Optional[str] = None,
) -> SimpleNamespace:
    data = {
        "deployment_id": deployment_id,
        "level": level,
        "step": step,
        "message": message,
        "timestamp": datetime.now(timezone.utc),
    }
    doc_ref = db.collection(_LOGS).add(data)
    data["id"] = doc_ref[1].id
    return SimpleNamespace(**data)


def get_logs(db: FirestoreClient, deployment_id: str) -> list[SimpleNamespace]:
    query = (
        db.collection(_LOGS)
        .where("deployment_id", "==", deployment_id)
        .order_by("timestamp")
    )
    return [_doc_to_record(doc) for doc in query.stream()]
