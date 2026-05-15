"""Container-aware zombie reaper (#386).

Replaces the single-subprocess reaper in agent/launcher.py with a loop
over _runners. A handle is reaped when (a) the runner container is gone
(normal exit, --rm removed it) or (b) the runner is still running but
last_webhook_at is older than ZOMBIE_TIMEOUT_SECONDS.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ZOMBIE_TIMEOUT_SECONDS = 120
REAP_INTERVAL_SECONDS = 15


def reap_once(client, *, zombie_timeout_seconds: int = ZOMBIE_TIMEOUT_SECONDS) -> None:
    from agent import launcher  # late-bind to avoid circular import

    import docker.errors as derr

    now = datetime.now(timezone.utc)
    for run_id, handle in list(launcher._runners.items()):
        try:
            container = client.containers.get(handle.container_name)
        except derr.NotFound:
            logger.info("reaper: %s gone, dropping", handle.container_name)
            launcher._runners.pop(run_id, None)
            continue
        idle = (now - handle.last_webhook_at).total_seconds()
        if idle > zombie_timeout_seconds:
            logger.warning("reaper: %s idle %.0fs, stopping", handle.container_name, idle)
            try:
                container.stop(timeout=30)
            except Exception as exc:
                logger.warning("reaper stop %s: %s", handle.container_name, exc)
            launcher._runners.pop(run_id, None)


async def reaper_loop() -> None:
    from agent import launcher
    while True:
        await asyncio.sleep(REAP_INTERVAL_SECONDS)
        try:
            reap_once(launcher._get_docker_client())
        except Exception:
            logger.exception("reaper_loop: unexpected")
