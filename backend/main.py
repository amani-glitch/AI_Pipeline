"""
FastAPI application entry point for the WebDeploy platform.

Start the server with::

    uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from db.database import init_db, SessionLocal

# ── Route imports ─────────────────────────────────────────────────────
from api.routes.deployments import router as deployments_router
from api.routes.health import router as health_router
from api.routes.websocket import router as websocket_router


# ── Logging configuration ────────────────────────────────────────────

def _configure_logging() -> None:
    """Set up Python logging based on the application settings."""
    settings = get_settings()
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Silence noisy third-party loggers
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


_configure_logging()
logger = logging.getLogger("webdeploy")


# ── Stale deployment watchdog ─────────────────────────────────────────

def _recover_stale_deployments() -> int:
    """
    Find all deployments stuck in 'running' or 'queued' status and mark
    them as failed.  This handles cases where the container was OOM-killed
    or restarted mid-pipeline.

    Returns the number of recovered deployments.
    """
    db = SessionLocal()
    recovered = 0

    for stale_status in ("running", "queued"):
        query = (
            db.collection("deployments")
            .where("status", "==", stale_status)
        )
        for doc in query.stream():
            data = doc.to_dict()
            deployment_id = doc.id
            current_step = data.get("current_step", "UNKNOWN")

            # Update steps_status — mark current and remaining as failed/skipped
            steps = {}
            raw = data.get("steps_status")
            if raw:
                try:
                    steps = json.loads(raw)
                except json.JSONDecodeError:
                    pass

            for step_name, step_status in steps.items():
                if step_status == "running":
                    steps[step_name] = "failed"
                elif step_status == "pending":
                    steps[step_name] = "skipped"

            doc.reference.update({
                "status": "failed",
                "steps_status": json.dumps(steps),
                "error_message": (
                    f"Deployment was interrupted (container restart/OOM) "
                    f"during step {current_step}. Please retry."
                ),
                "completed_at": datetime.now(timezone.utc),
            })

            logger.warning(
                "Recovered stale deployment %s (was %s at step %s)",
                deployment_id, stale_status, current_step,
            )
            recovered += 1

    return recovered


async def _stale_deployment_watchdog(interval_seconds: int = 120) -> None:
    """
    Background task that periodically checks for deployments stuck in
    'running' state for longer than PIPELINE_MAX_TIMEOUT_SECONDS.

    Runs every *interval_seconds* (default: 2 minutes).
    """
    settings = get_settings()
    max_age = settings.PIPELINE_MAX_TIMEOUT_SECONDS

    while True:
        await asyncio.sleep(interval_seconds)
        try:
            db = SessionLocal()
            query = db.collection("deployments").where("status", "==", "running")
            now = datetime.now(timezone.utc)

            for doc in query.stream():
                data = doc.to_dict()
                started_at = data.get("started_at")
                if started_at is None:
                    continue

                # Firestore returns datetime objects directly
                if hasattr(started_at, 'timestamp'):
                    elapsed = (now - started_at.replace(tzinfo=timezone.utc)).total_seconds()
                else:
                    continue

                if elapsed > max_age:
                    deployment_id = doc.id
                    current_step = data.get("current_step", "UNKNOWN")

                    steps = {}
                    raw = data.get("steps_status")
                    if raw:
                        try:
                            steps = json.loads(raw)
                        except json.JSONDecodeError:
                            pass

                    for step_name, step_status in steps.items():
                        if step_status == "running":
                            steps[step_name] = "failed"
                        elif step_status == "pending":
                            steps[step_name] = "skipped"

                    doc.reference.update({
                        "status": "failed",
                        "steps_status": json.dumps(steps),
                        "error_message": (
                            f"Pipeline timed out after {int(elapsed)}s "
                            f"at step {current_step}. Please retry."
                        ),
                        "completed_at": now,
                    })

                    logger.warning(
                        "Watchdog: marked deployment %s as failed "
                        "(running for %ds, limit %ds)",
                        deployment_id, int(elapsed), max_age,
                    )

        except Exception as exc:
            logger.error("Stale deployment watchdog error: %s", exc)


# ── Lifespan (startup / shutdown) ────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: run setup on startup, teardown on shutdown."""
    logger.info("Initialising database...")
    init_db()
    logger.info("Database ready.")

    # Recover any deployments left in 'running' state from a previous crash
    recovered = _recover_stale_deployments()
    if recovered:
        logger.info("Recovered %d stale deployment(s) from previous crash.", recovered)

    # Start background watchdog
    watchdog_task = asyncio.create_task(
        _stale_deployment_watchdog(),
        name="stale-deployment-watchdog",
    )
    logger.info("Stale deployment watchdog started.")

    yield

    watchdog_task.cancel()
    logger.info("Shutting down WebDeploy.")


# ── Application factory ──────────────────────────────────────────────

app = FastAPI(
    title="WebDeploy",
    description=(
        "Automated website deployment platform. "
        "Upload a ZIP (Vite or static HTML/CSS/JS), and WebDeploy handles "
        "extraction, AI validation, building, GCP infrastructure provisioning, "
        "CDN upload, and email notification."
    ),
    version="1.1.0",
    lifespan=lifespan,
)

# ── CORS — allow the frontend dev server (port 3000) ─────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Development: accept all origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Register routers ─────────────────────────────────────────────────
app.include_router(deployments_router)
app.include_router(health_router)
app.include_router(websocket_router)
