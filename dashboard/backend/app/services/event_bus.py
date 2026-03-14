from __future__ import annotations

"""In-memory pub/sub event bus for broadcasting SSE events to connected clients."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# All active subscriber queues
_subscribers: list[asyncio.Queue[dict[str, Any]]] = []
_lock = asyncio.Lock()


async def publish(event: dict[str, Any]) -> None:
    """Broadcast an event dict to all connected SSE subscribers.

    The event dict should contain at least:
      - "type": str  (e.g. "run_start", "employee_complete", "verdict_execute")
      - "data": dict (event payload)

    A server-side timestamp is injected automatically.
    """
    event.setdefault("timestamp", datetime.now(timezone.utc).isoformat())

    dead: list[asyncio.Queue[dict[str, Any]]] = []
    async with _lock:
        for q in _subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Subscriber is too slow; drop it
                logger.warning("Dropping slow SSE subscriber (queue full)")
                dead.append(q)

        for q in dead:
            _subscribers.remove(q)

    logger.debug("Published event %s to %d subscribers", event.get("type"), len(_subscribers))


async def subscribe() -> AsyncGenerator[dict[str, Any], None]:
    """Yield events as they are published. Use in an async for loop.

    Usage in an SSE endpoint:
        async for event in subscribe():
            yield format_sse(event)
    """
    q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
    async with _lock:
        _subscribers.append(q)
    logger.info("New SSE subscriber (total: %d)", len(_subscribers))
    try:
        while True:
            event = await q.get()
            yield event
    finally:
        async with _lock:
            _subscribers.remove(q)
        logger.info("SSE subscriber disconnected (remaining: %d)", len(_subscribers))


def subscriber_count() -> int:
    """Return the number of active SSE subscribers."""
    return len(_subscribers)
