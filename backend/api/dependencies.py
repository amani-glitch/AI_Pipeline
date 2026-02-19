"""FastAPI dependencies — database session, settings, logging."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Callable

from config import Settings, get_settings

logger = logging.getLogger("webdeploy")

# ── WebSocket log broadcast ──────────────────────────────────────────
# Maps deployment_id → set of asyncio.Queue (one per connected WS client)
_ws_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)


def subscribe_logs(deployment_id: str) -> asyncio.Queue:
    """Register a new WebSocket client for real-time logs."""
    q: asyncio.Queue = asyncio.Queue()
    _ws_subscribers[deployment_id].add(q)
    return q


def unsubscribe_logs(deployment_id: str, q: asyncio.Queue) -> None:
    _ws_subscribers[deployment_id].discard(q)
    if not _ws_subscribers[deployment_id]:
        del _ws_subscribers[deployment_id]


async def broadcast_log(deployment_id: str, message: str) -> None:
    """Push a log line to all WebSocket subscribers for a deployment."""
    for q in list(_ws_subscribers.get(deployment_id, [])):
        try:
            q.put_nowait(message)
        except asyncio.QueueFull:
            pass  # Drop if client is slow


def get_log_callback(deployment_id: str) -> Callable:
    """
    Returns a callable that:
      1. Logs to Python logger
      2. Persists to the database
      3. Broadcasts to WebSocket subscribers
    """
    def _log(message: str, level: str = "INFO", step: str | None = None) -> None:
        logger.log(logging.getLevelName(level), "[%s] %s", deployment_id[:8], message)
        # DB persistence is handled by the orchestrator (to keep this sync-safe)
        # WS broadcast must happen in an event loop
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(broadcast_log(deployment_id, message))
        except RuntimeError:
            pass  # No event loop — skip WS broadcast (e.g., during tests)

    return _log
