"""
FastAPI application entry point for the WebDeploy platform.

Start the server with::

    uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from db.database import init_db

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


# ── Lifespan (startup / shutdown) ────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: run setup on startup, teardown on shutdown."""
    logger.info("Initialising database...")
    init_db()
    logger.info("Database ready.")
    yield
    logger.info("Shutting down WebDeploy.")


# ── Application factory ──────────────────────────────────────────────

app = FastAPI(
    title="WebDeploy",
    description=(
        "Automated Vite-project deployment platform. "
        "Upload a ZIP, and WebDeploy handles extraction, AI validation, "
        "building, GCP infrastructure provisioning, CDN upload, and "
        "email notification."
    ),
    version="1.0.0",
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
