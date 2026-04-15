"""WebSocket connection manager.

Maintains a set of active WebSocket connections and broadcasts JSON events to
all of them. Also sets itself as the broadcast target in job_manager.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)

_connections: Set[WebSocket] = set()
_loop: asyncio.AbstractEventLoop | None = None


def set_event_loop(loop: asyncio.AbstractEventLoop):
    global _loop
    _loop = loop


async def connect(ws: WebSocket):
    await ws.accept()
    _connections.add(ws)
    logger.info(f"WebSocket connected. Total: {len(_connections)}")


def disconnect(ws: WebSocket):
    _connections.discard(ws)
    logger.info(f"WebSocket disconnected. Total: {len(_connections)}")


async def broadcast_async(event: dict):
    """Send event to all connected clients (async — called from WS endpoint coroutine)."""
    dead = set()
    msg = json.dumps(event)
    for ws in list(_connections):
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    _connections.difference_update(dead)


def broadcast(event: dict):
    """Thread-safe broadcast. Called from the pipeline thread via job_manager."""
    if _loop is None or not _connections:
        return
    asyncio.run_coroutine_threadsafe(broadcast_async(event), _loop)
