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

from models.enums import DeploymentStatus, LogLevel, PipelineStep, StepStatus, UserStatus

# Collection names
_DEPLOYMENTS = "deployments"
_LOGS = "deployment_logs"
_USERS = "users"


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
    deployer_first_name: str = "",
    deployer_last_name: str = "",
    deployer_email: str = "",
    ai_enabled: bool = False,
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
        "deployer_first_name": deployer_first_name,
        "deployer_last_name": deployer_last_name,
        "deployer_email": deployer_email,
        "ai_enabled": ai_enabled,
        "ai_token_usage": None,
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


# ═══════════════════════════════════════════════════════════════════════
#  User CRUD
# ═══════════════════════════════════════════════════════════════════════

def create_user(
    db: FirestoreClient,
    *,
    uid: str,
    email: str,
    display_name: str = "",
    requested_role: str = "simple_user",
) -> SimpleNamespace:
    """Create a new user document (pending approval). Document ID = Firebase UID."""
    data = {
        "email": email,
        "display_name": display_name,
        "role": None,
        "status": UserStatus.PENDING.value,
        "requested_role": requested_role,
        "created_at": datetime.now(timezone.utc),
        "approved_at": None,
        "approved_by": None,
    }
    db.collection(_USERS).document(uid).set(data)
    data["uid"] = uid
    data["id"] = uid
    return SimpleNamespace(**data)


def get_user(db: FirestoreClient, uid: str) -> Optional[SimpleNamespace]:
    """Get a user by Firebase UID. Returns None if not found."""
    doc = db.collection(_USERS).document(uid).get()
    if not doc.exists:
        return None
    record = _doc_to_record(doc)
    record.uid = doc.id
    return record


def update_user_status(
    db: FirestoreClient,
    uid: str,
    *,
    status: str,
    role: Optional[str] = None,
    approved_by: Optional[str] = None,
) -> None:
    """Update a user's status and optionally assign a role."""
    updates: dict = {"status": status}
    if role is not None:
        updates["role"] = role
    if approved_by is not None:
        updates["approved_by"] = approved_by
    if status == UserStatus.APPROVED.value:
        updates["approved_at"] = datetime.now(timezone.utc)
    db.collection(_USERS).document(uid).update(updates)


def list_users(db: FirestoreClient) -> list[SimpleNamespace]:
    """Return all users ordered by created_at descending."""
    query = (
        db.collection(_USERS)
        .order_by("created_at", direction="DESCENDING")
    )
    results = []
    for doc in query.stream():
        record = _doc_to_record(doc)
        record.uid = doc.id
        results.append(record)
    return results


# ═══════════════════════════════════════════════════════════════════════
#  Notification Preferences CRUD
# ═══════════════════════════════════════════════════════════════════════

def get_notification_preferences(db: FirestoreClient, uid: str) -> Optional[dict]:
    """Get notification preferences for a user. Returns None if not set."""
    doc = db.collection(_USERS).document(uid).get()
    if not doc.exists:
        return None
    data = doc.to_dict() or {}
    return data.get("notification_preferences")


def update_notification_preferences(db: FirestoreClient, uid: str, prefs: dict) -> dict:
    """Update notification preferences for a user (merge with existing)."""
    doc_ref = db.collection(_USERS).document(uid)
    doc = doc_ref.get()
    if not doc.exists:
        return {}

    current = (doc.to_dict() or {}).get("notification_preferences", {}) or {}
    # Merge: only update fields that are provided (not None)
    merged = {**current}
    for key, value in prefs.items():
        if value is not None:
            merged[key] = value

    doc_ref.update({"notification_preferences": merged})
    return merged


def list_users_with_report_preference(
    db: FirestoreClient, frequency: str,
) -> list[SimpleNamespace]:
    """Return all approved users who opted in for reports at the given frequency.

    Uses single-field query + Python filter to avoid composite index requirements.
    """
    query = db.collection(_USERS).where("status", "==", UserStatus.APPROVED.value)
    results = []
    for doc in query.stream():
        data = doc.to_dict() or {}
        prefs = data.get("notification_preferences") or {}
        if prefs.get("report_enabled") is True and prefs.get("report_frequency") == frequency:
            record = _doc_to_record(doc)
            record.uid = doc.id
            results.append(record)
    return results


# ═══════════════════════════════════════════════════════════════════════
#  Git Integration CRUD
# ═══════════════════════════════════════════════════════════════════════

_GIT_CONNECTIONS = "git_connections"
_PUSH_EVENTS = "git_push_events"


def create_git_connection(
    db: FirestoreClient,
    *,
    uid: str,
    user_email: str,
    provider: str,
    repo_url: str,
    repo_name: str,
    branch: str,
    access_token: str,
) -> SimpleNamespace:
    """Create a new Git connection."""
    import secrets
    webhook_secret = secrets.token_hex(20)
    data = {
        "uid": uid,
        "user_email": user_email,
        "provider": provider,
        "repo_url": repo_url,
        "repo_name": repo_name,
        "branch": branch,
        "access_token": access_token,
        "webhook_secret": webhook_secret,
        "created_at": datetime.now(timezone.utc),
    }
    _, doc_ref = db.collection(_GIT_CONNECTIONS).add(data)
    data["id"] = doc_ref.id
    return SimpleNamespace(**data)


def get_git_connection(db: FirestoreClient, connection_id: str) -> Optional[SimpleNamespace]:
    doc = db.collection(_GIT_CONNECTIONS).document(connection_id).get()
    if not doc.exists:
        return None
    return _doc_to_record(doc)


def list_git_connections(db: FirestoreClient, uid: str) -> list[SimpleNamespace]:
    # Single-field query to avoid composite index requirement; sort in Python
    query = db.collection(_GIT_CONNECTIONS).where("uid", "==", uid)
    results = [_doc_to_record(doc) for doc in query.stream()]
    results.sort(key=lambda r: getattr(r, "created_at", None) or datetime.min, reverse=True)
    return results


def update_git_connection_branch(db: FirestoreClient, connection_id: str, branch: str) -> None:
    db.collection(_GIT_CONNECTIONS).document(connection_id).update({"branch": branch})


def delete_git_connection(db: FirestoreClient, connection_id: str) -> None:
    # Delete push events
    events = db.collection(_PUSH_EVENTS).where("connection_id", "==", connection_id)
    for doc in events.stream():
        doc.reference.delete()
    db.collection(_GIT_CONNECTIONS).document(connection_id).delete()


def create_push_event(
    db: FirestoreClient,
    *,
    connection_id: str,
    branch: str,
    commit_sha: str,
    commit_message: str,
    author: str,
) -> SimpleNamespace:
    data = {
        "connection_id": connection_id,
        "branch": branch,
        "commit_sha": commit_sha,
        "commit_message": commit_message,
        "author": author,
        "timestamp": datetime.now(timezone.utc),
        "deployed": False,
        "deployment_id": None,
    }
    _, doc_ref = db.collection(_PUSH_EVENTS).add(data)
    data["id"] = doc_ref.id
    return SimpleNamespace(**data)


def list_push_events(
    db: FirestoreClient, connection_id: str, limit: int = 50,
) -> list[SimpleNamespace]:
    # Single-field query to avoid composite index requirement; sort in Python
    query = db.collection(_PUSH_EVENTS).where("connection_id", "==", connection_id)
    results = [_doc_to_record(doc) for doc in query.stream()]
    results.sort(key=lambda r: getattr(r, "timestamp", None) or datetime.min, reverse=True)
    return results[:limit]


def mark_push_event_deployed(db: FirestoreClient, event_id: str, deployment_id: str) -> None:
    db.collection(_PUSH_EVENTS).document(event_id).update({
        "deployed": True,
        "deployment_id": deployment_id,
    })


# ═══════════════════════════════════════════════════════════════════════
#  Quota CRUD
# ═══════════════════════════════════════════════════════════════════════

_QUOTAS = "quotas"


def get_quota(db: FirestoreClient, target_type: str, target_id: str) -> Optional[dict]:
    """Get quota config. target_type is 'role' or 'user', target_id is role name or uid."""
    doc_id = f"{target_type}:{target_id}"
    doc = db.collection(_QUOTAS).document(doc_id).get()
    if not doc.exists:
        return None
    return doc.to_dict().get("config")


def set_quota(db: FirestoreClient, target_type: str, target_id: str, config: dict) -> None:
    doc_id = f"{target_type}:{target_id}"
    db.collection(_QUOTAS).document(doc_id).set({
        "target_type": target_type,
        "target_id": target_id,
        "config": config,
        "updated_at": datetime.now(timezone.utc),
    })


def delete_quota(db: FirestoreClient, target_type: str, target_id: str) -> None:
    doc_id = f"{target_type}:{target_id}"
    db.collection(_QUOTAS).document(doc_id).delete()


def get_user_quota_usage(db: FirestoreClient, uid: str, email: str) -> dict:
    """Get current deployment usage for a user.

    Uses a single-field query on deployer_email and filters in Python
    to avoid composite index requirements.
    """
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # Fetch all user deployments in one query (single field = no composite index)
    user_query = db.collection(_DEPLOYMENTS).where("deployer_email", "==", email)
    user_docs = list(user_query.stream())

    today_count = 0
    active_count = 0
    total_count = len(user_docs)

    for doc in user_docs:
        data = doc.to_dict()
        status = data.get("status", "")

        # Count active
        if status in ("running", "queued"):
            active_count += 1

        # Count today
        created = data.get("created_at")
        if created and hasattr(created, "timestamp"):
            created_utc = created.replace(tzinfo=timezone.utc) if created.tzinfo is None else created
            if created_utc >= today_start:
                today_count += 1

    return {
        "deployments_today": today_count,
        "active_deployments": active_count,
        "total_deployments": total_count,
    }


# ═══════════════════════════════════════════════════════════════════════
#  System Alerts CRUD
# ═══════════════════════════════════════════════════════════════════════

_ALERTS = "system_alerts"


def create_alert(
    db: FirestoreClient,
    *,
    severity: str,
    source: str,
    title: str,
    message: str,
) -> SimpleNamespace:
    data = {
        "severity": severity,
        "source": source,
        "title": title,
        "message": message,
        "resolved": False,
        "unique_key": None,
        "created_at": datetime.now(timezone.utc),
        "resolved_at": None,
    }
    _, doc_ref = db.collection(_ALERTS).add(data)
    data["id"] = doc_ref.id
    return SimpleNamespace(**data)


def list_alerts(
    db: FirestoreClient, resolved: Optional[bool] = None, limit: int = 50,
) -> list[SimpleNamespace]:
    # Avoid composite index requirement: filter first, sort in Python
    if resolved is not None:
        query = db.collection(_ALERTS).where("resolved", "==", resolved)
    else:
        query = db.collection(_ALERTS)
    results = [_doc_to_record(doc) for doc in query.stream()]
    results.sort(key=lambda r: getattr(r, "created_at", None) or datetime.min, reverse=True)
    return results[:limit]


def resolve_alert(db: FirestoreClient, alert_id: str) -> None:
    db.collection(_ALERTS).document(alert_id).update({
        "resolved": True,
        "resolved_at": datetime.now(timezone.utc),
    })


def delete_alert(db: FirestoreClient, alert_id: str) -> None:
    db.collection(_ALERTS).document(alert_id).delete()


def count_unresolved_alerts(db: FirestoreClient) -> int:
    query = db.collection(_ALERTS).where("resolved", "==", False)
    return len(list(query.stream()))
