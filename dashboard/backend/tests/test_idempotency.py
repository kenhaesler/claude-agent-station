"""Tests for the idempotency store.

Covers:
- First event returns False (not duplicate)
- Same event_id returns True (duplicate)
- Different event_ids don't conflict
- TTL eviction
- Max size eviction
"""

from unittest.mock import patch

from app.services.idempotency import IdempotencyStore


def test_first_event_is_not_duplicate():
    """First time seeing an event_id should return False (not duplicate)."""
    store = IdempotencyStore()
    assert store.check_and_mark("evt-001") is False


def test_same_event_id_is_duplicate():
    """Second call with the same event_id should return True (duplicate)."""
    store = IdempotencyStore()
    store.check_and_mark("evt-002")
    assert store.check_and_mark("evt-002") is True


def test_different_event_ids_do_not_conflict():
    """Different event_ids should each be treated as new (not duplicates)."""
    store = IdempotencyStore()
    assert store.check_and_mark("evt-aaa") is False
    assert store.check_and_mark("evt-bbb") is False
    assert store.check_and_mark("evt-ccc") is False


def test_ttl_eviction():
    """Events older than TTL should be evicted and no longer flagged as duplicates."""
    store = IdempotencyStore(ttl=10)
    # Use a fixed base time for the first insert
    base_time = 1000.0
    with patch("time.monotonic", return_value=base_time):
        assert store.check_and_mark("evt-ttl") is False

    # Advance time past TTL
    with patch("time.monotonic", return_value=base_time + 11):
        # The entry should have been evicted, so it's no longer a duplicate
        assert store.check_and_mark("evt-ttl") is False


def test_max_size_eviction():
    """When store reaches max_size, oldest entries should be evicted."""
    store = IdempotencyStore(max_size=3, ttl=3600)
    store.check_and_mark("evt-1")
    store.check_and_mark("evt-2")
    store.check_and_mark("evt-3")

    # Store is now full (3/3). Adding a 4th should evict the oldest (evt-1).
    store.check_and_mark("evt-4")

    # evt-1 was evicted, so it should be treated as new
    assert store.check_and_mark("evt-1") is False

    # evt-3 and evt-4 should still be tracked (evt-2 was evicted when evt-1
    # was re-added since the store was at capacity)
    assert store.check_and_mark("evt-3") is True
    assert store.check_and_mark("evt-4") is True


def test_eviction_preserves_recent_entries():
    """Eviction should only remove the oldest, not recent entries."""
    store = IdempotencyStore(max_size=2, ttl=3600)
    store.check_and_mark("evt-a")
    store.check_and_mark("evt-b")
    # Adding evt-c evicts evt-a
    store.check_and_mark("evt-c")

    assert store.check_and_mark("evt-a") is False  # evicted, treated as new
    # Now evt-b was evicted (oldest after evt-a was re-added)
    # evt-c should still be there
    assert store.check_and_mark("evt-c") is True
