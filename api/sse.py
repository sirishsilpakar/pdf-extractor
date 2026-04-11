"""Server-Sent Events broadcaster.

Each SSE client that connects to GET /api/events gets its own asyncio.Queue.
When broadcast() is called from the pipeline thread, the event is put into
every queue. Each client's generator reads from its queue and yields the
event in the SSE wire format.
"""

from __future__ import annotations

import asyncio
import json
import logging
from asyncio import Queue
from typing import Set

logger = logging.getLogger(__name__)

# One Queue per connected SSE client.  The Queue is bounded so a slow or
# stalled client doesn't grow memory unboundedly, events are dropped for
# that client instead of accumulating forever.
_clients: Set[Queue] = set()

# The asyncio event loop running in the main thread.  Set once at startup.
_loop: asyncio.AbstractEventLoop | None = None


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


async def subscribe() -> Queue:
    """Create a queue for a new SSE client and register it."""
    q: Queue = Queue(maxsize=256)
    _clients.add(q)
    logger.info("SSE client connected. Active: %d", len(_clients))
    return q


def unsubscribe(q: Queue) -> None:
    """Remove a client's queue when its connection closes."""
    _clients.discard(q)
    logger.info("SSE client disconnected. Active: %d", len(_clients))


async def broadcast_async(event: dict) -> None:
    """Push event into every connected client's queue (runs on the event loop)."""
    dead: Set[Queue] = set()
    for q in list(_clients):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # Client is too slow / stalled, evict it so it reconnects fresh.
            dead.add(q)
    _clients.difference_update(dead)


def broadcast(event: dict) -> None:
    """Thread-safe entry point called from the pipeline thread via job_manager.

    Schedules broadcast_async on the event loop without blocking the caller.
    """
    if _loop is None or not _clients:
        return
    asyncio.run_coroutine_threadsafe(broadcast_async(event), _loop)
