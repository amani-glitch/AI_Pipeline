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


def _broadcast_sync(deployment_id: str, message: str) -> None:
    """Push a log line to all WS subscribers (must run on the event-loop thread)."""
    for q in list(_ws_subscribers.get(deployment_id, [])):
        try:
            q.put_nowait(message)
        except asyncio.QueueFull:
            pass  # Drop if client is slow


async def broadcast_log(deployment_id: str, message: str) -> None:
    """Push a log line to all WebSocket subscribers for a deployment."""
    _broadcast_sync(deployment_id, message)


def get_log_callback(deployment_id: str) -> Callable:
    """
    Returns a callable that:
      1. Logs to Python logger
      2. Broadcasts to WebSocket subscribers (thread-safe)

    The callback is safe to call from any thread (e.g. inside
    asyncio.to_thread workers) because it uses call_soon_threadsafe
    to schedule the broadcast on the main event loop.
    """
    # Capture the event loop at creation time — we are on the async thread
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    def _log(message: str, level: str = "INFO", step: str | None = None) -> None:
        logger.log(logging.getLevelName(level), "[%s] %s", deployment_id[:8], message)

        # Broadcast to WebSocket clients — thread-safe
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(_broadcast_sync, deployment_id, message)
            except RuntimeError:
                pass  # Loop closed

    return _log
