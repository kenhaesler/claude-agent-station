from __future__ import annotations

"""Simple in-memory idempotency check for webhook events."""

import logging
import time
from collections import OrderedDict

logger = logging.getLogger(__name__)

# Max events to track (oldest evicted first)
MAX_EVENTS = 10000
# TTL in seconds
EVENT_TTL = 3600  # 1 hour


class IdempotencyStore:
    """Track processed event IDs to prevent duplicate processing."""

    def __init__(self, max_size: int = MAX_EVENTS, ttl: int = EVENT_TTL):
        self._store: OrderedDict[str, float] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl

    def check_and_mark(self, event_id: str) -> bool:
        """Check if event was already processed. Returns True if it's a duplicate.

        If not a duplicate, marks it as processed and returns False.
        """
        now = time.monotonic()
        self._evict_expired(now)

        if event_id in self._store:
            logger.debug("Duplicate event detected: %s", event_id)
            return True

        # Evict oldest if at capacity
        while len(self._store) >= self._max_size:
            self._store.popitem(last=False)

        self._store[event_id] = now
        return False

    def _evict_expired(self, now: float) -> None:
        """Remove entries older than TTL."""
        cutoff = now - self._ttl
        while self._store:
            oldest_key = next(iter(self._store))
            if self._store[oldest_key] < cutoff:
                self._store.popitem(last=False)
            else:
                break


# Module-level singleton
_store = IdempotencyStore()


def is_duplicate(event_id: str) -> bool:
    """Check if event_id was already processed. Marks it if not."""
    return _store.check_and_mark(event_id)
