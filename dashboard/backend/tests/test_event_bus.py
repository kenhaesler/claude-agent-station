"""Tests for the event bus service.

Covers:
- publish sends events to subscribers
- publish drops slow subscribers (QueueFull)
- subscriber_count returns correct count
- subscribe/unsubscribe lifecycle
"""

import asyncio

import pytest

from app.services.event_bus import _lock, _subscribers, publish, subscribe, subscriber_count


@pytest.fixture(autouse=True)
async def clean_subscribers():
    """Ensure subscriber list is clean before and after each test."""
    async with _lock:
        _subscribers.clear()
    yield
    async with _lock:
        _subscribers.clear()


# ---------------------------------------------------------------------------
# subscriber_count
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subscriber_count_zero():
    """subscriber_count should return 0 when no subscribers."""
    assert subscriber_count() == 0


@pytest.mark.asyncio
async def test_subscriber_count_after_subscribe():
    """subscriber_count should reflect active subscribers."""
    subscribe()
    # Start the generator — it registers immediately on first __anext__
    # But actually subscribe() is an async generator, so we need to enter it
    # The subscribe function adds to _subscribers when iterated
    # We'll manually add a queue to simulate
    q = asyncio.Queue(maxsize=64)
    async with _lock:
        _subscribers.append(q)
    assert subscriber_count() == 1

    async with _lock:
        _subscribers.remove(q)
    assert subscriber_count() == 0


# ---------------------------------------------------------------------------
# publish sends events to subscribers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_publish_delivers_to_subscriber():
    """publish should deliver event to all subscribed queues."""
    q = asyncio.Queue(maxsize=64)
    async with _lock:
        _subscribers.append(q)

    await publish({"type": "test_event", "data": {"key": "value"}})

    event = q.get_nowait()
    assert event["type"] == "test_event"
    assert event["data"]["key"] == "value"
    assert "timestamp" in event  # auto-injected


@pytest.mark.asyncio
async def test_publish_delivers_to_multiple_subscribers():
    """publish should deliver to all subscribers."""
    q1 = asyncio.Queue(maxsize=64)
    q2 = asyncio.Queue(maxsize=64)
    async with _lock:
        _subscribers.append(q1)
        _subscribers.append(q2)

    await publish({"type": "broadcast", "data": {}})

    assert not q1.empty()
    assert not q2.empty()
    e1 = q1.get_nowait()
    e2 = q2.get_nowait()
    assert e1["type"] == "broadcast"
    assert e2["type"] == "broadcast"


@pytest.mark.asyncio
async def test_publish_injects_timestamp():
    """publish should inject a timestamp if not present."""
    q = asyncio.Queue(maxsize=64)
    async with _lock:
        _subscribers.append(q)

    await publish({"type": "ts_test", "data": {}})

    event = q.get_nowait()
    assert "timestamp" in event


@pytest.mark.asyncio
async def test_publish_preserves_existing_timestamp():
    """publish should not overwrite an existing timestamp."""
    q = asyncio.Queue(maxsize=64)
    async with _lock:
        _subscribers.append(q)

    await publish({"type": "ts_test", "data": {}, "timestamp": "2026-01-01T00:00:00"})

    event = q.get_nowait()
    assert event["timestamp"] == "2026-01-01T00:00:00"


# ---------------------------------------------------------------------------
# publish drops slow subscribers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_publish_drops_slow_subscriber():
    """publish should remove subscriber whose queue is full."""
    q = asyncio.Queue(maxsize=1)
    async with _lock:
        _subscribers.append(q)

    # Fill the queue
    await publish({"type": "event1", "data": {}})
    assert subscriber_count() == 1

    # Second publish should overflow the queue and drop the subscriber
    await publish({"type": "event2", "data": {}})
    assert subscriber_count() == 0


@pytest.mark.asyncio
async def test_publish_keeps_healthy_subscriber_when_dropping_slow():
    """publish should only drop the slow subscriber, not healthy ones."""
    slow_q = asyncio.Queue(maxsize=1)
    healthy_q = asyncio.Queue(maxsize=64)
    async with _lock:
        _subscribers.append(slow_q)
        _subscribers.append(healthy_q)

    # Fill the slow queue
    await publish({"type": "event1", "data": {}})
    # This should drop slow_q but keep healthy_q
    await publish({"type": "event2", "data": {}})

    assert subscriber_count() == 1
    # Healthy queue should have both events
    assert healthy_q.qsize() == 2


# ---------------------------------------------------------------------------
# subscribe/unsubscribe lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subscribe_yields_published_events():
    """subscribe() generator should yield events as they are published."""
    received = []

    async def reader():
        async for event in subscribe():
            received.append(event)
            if len(received) >= 2:
                break

    task = asyncio.create_task(reader())

    # Give the generator time to register
    await asyncio.sleep(0.05)
    assert subscriber_count() == 1

    await publish({"type": "event_a", "data": {}})
    await publish({"type": "event_b", "data": {}})

    await asyncio.wait_for(task, timeout=2.0)

    assert len(received) == 2
    assert received[0]["type"] == "event_a"
    assert received[1]["type"] == "event_b"


@pytest.mark.asyncio
async def test_subscribe_cleans_up_on_cancel():
    """Cancelling a subscribe task should remove it from subscribers."""
    async def reader():
        async for _ in subscribe():
            pass

    task = asyncio.create_task(reader())
    await asyncio.sleep(0.05)
    assert subscriber_count() == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Give cleanup a moment
    await asyncio.sleep(0.05)
    assert subscriber_count() == 0


@pytest.mark.asyncio
async def test_publish_no_subscribers():
    """publish should succeed silently with no subscribers."""
    await publish({"type": "no_one_listening", "data": {}})
    assert subscriber_count() == 0  # no crash, no subscribers
