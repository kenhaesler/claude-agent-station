"""Sync HTTP client that emits orchestrator webhook events.

Replaces the bash ``webhook_event`` helper. Provides retries with
exponential backoff so an EXIT-trap (or any other call site) cannot
silently drop a critical lifecycle event.

Usage (Python):
    from agent.webhook_emitter import emit
    emit("run_start", run_id="run-1", payload={"project": "x/y"})

Usage (bash, via CLI):
    python3 -m agent.webhook_emitter run_start \\
        --run-id "run-1" \\
        --json '{"project":"x/y"}'

Env:
    STATION_WEBHOOK_URL       (default: http://127.0.0.1:8420/api/webhook/run-event)
    STATION_WEBHOOK_SECRET    (optional; sent as X-Webhook-Token)
"""

from __future__ import annotations

import json as json_mod
import logging
import os
import sys
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:8420/api/webhook/run-event"
RETRIES = 3
BACKOFF_BASE = 0.5  # 0.5s, 1s, 2s

# env var that carries the launcher base URL (e.g. http://localhost:8421).
# When unset (systemd installs, CI without compose) the ping is silently
# skipped. See #360.
LAUNCHER_URL_ENV = "STATION_AGENT_LAUNCHER_URL"


def _ping_launcher(run_id: str | None = None, timeout: float = 1.0) -> None:
    """Best-effort heartbeat to the launcher's /webhook-tick.

    ``run_id`` MUST be passed in container mode (#386) so the launcher
    updates the per-run ``handle.last_webhook_at`` in its ``_runners``
    map. Without it, the launcher's handler falls through to the
    legacy global ``_last_webhook_at``, the container-aware reaper
    sees the runner handle's timestamp stuck at spawn time, and
    SIGTERMs the runner at the 120s mark even while it's doing useful
    work. Discovered after PRs #426/#429/#430 got the spawn working:
    each subsequent live run died at exactly 133s with
    ``reaper: cas-runner-... idle 133s, stopping``.

    Silently swallows all errors — the launcher may not be reachable
    (e.g. orchestrator running outside compose mode) and that's fine.
    Defaults to ``http://localhost:8421`` because the emitter runs
    inside the same container as the launcher in inline mode; the env
    var ``STATION_AGENT_LAUNCHER_URL`` is the container-mode override
    that points at ``http://agent:8421``.
    """
    base = os.environ.get(LAUNCHER_URL_ENV) or "http://localhost:8421"
    token = os.environ.get("STATION_LAUNCHER_TOKEN", "")
    headers: dict[str, str] = {}
    if token:
        headers["X-Launcher-Token"] = token
    params: dict[str, str] = {"run_id": run_id} if run_id else {}
    try:
        httpx.post(
            f"{base.rstrip('/')}/webhook-tick",
            headers=headers,
            params=params,
            timeout=timeout,
        )
    except Exception:
        pass


def _url() -> str:
    return os.environ.get("STATION_WEBHOOK_URL", DEFAULT_URL)


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    secret = os.environ.get("STATION_WEBHOOK_SECRET", "")
    if secret:
        # Dashboard router at app/routers/webhook.py:59 expects this exact
        # header name. The env var name (STATION_WEBHOOK_SECRET) is the
        # operator-facing convention; the header name is the wire contract.
        h["X-Webhook-Token"] = secret
    return h


def emit(event: str, *, run_id: str, payload: dict[str, Any] | None = None) -> None:
    """Post a webhook event. Retries on 5xx and connection errors.

    Does not raise on final failure — the orchestrator should not be
    killed by a dashboard outage. The failure is logged.

    After the retry loop (success or failure), pings the launcher's
    /webhook-tick so the zombie reaper knows the subprocess is still
    alive. See #360.
    """
    body: dict[str, Any] = {"event": event, "run_id": run_id}
    if payload:
        body.update(payload)

    last_err: str | None = None
    for attempt in range(RETRIES):
        try:
            resp = httpx.post(_url(), json=body, headers=_headers(), timeout=10.0)
            if 200 <= resp.status_code < 300:
                _ping_launcher(run_id=run_id)
                return
            last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if 400 <= resp.status_code < 500:
                logger.error("webhook_emitter: non-retryable %s for %s",
                             last_err, event)
                _ping_launcher(run_id=run_id)
                return
        except httpx.HTTPError as exc:
            last_err = f"transport error: {exc}"
        if attempt < RETRIES - 1:
            time.sleep(BACKOFF_BASE * (2 ** attempt))
    logger.error("webhook_emitter: gave up after %d attempts (%s) for %s",
                 RETRIES, last_err, event)
    _ping_launcher(run_id=run_id)


def _cli() -> int:
    """CLI entrypoint: python3 -m agent.webhook_emitter EVENT --run-id ID --json JSON"""
    import argparse
    p = argparse.ArgumentParser(prog="agent.webhook_emitter")
    p.add_argument("event")
    p.add_argument("--run-id", required=True)
    p.add_argument("--json", default="{}", help="JSON-encoded payload")
    args = p.parse_args()
    try:
        payload = json_mod.loads(args.json) if args.json else {}
    except json_mod.JSONDecodeError as e:
        print(f"webhook_emitter: invalid --json: {e}", file=sys.stderr)
        return 2
    emit(args.event, run_id=args.run_id, payload=payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
