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
    """
    body: dict[str, Any] = {"event": event, "run_id": run_id}
    if payload:
        body.update(payload)

    last_err: str | None = None
    for attempt in range(RETRIES):
        try:
            resp = httpx.post(_url(), json=body, headers=_headers(), timeout=10.0)
            if 200 <= resp.status_code < 300:
                return
            last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if 400 <= resp.status_code < 500:
                logger.error("webhook_emitter: non-retryable %s for %s",
                             last_err, event)
                return
        except httpx.HTTPError as exc:
            last_err = f"transport error: {exc}"
        if attempt < RETRIES - 1:
            time.sleep(BACKOFF_BASE * (2 ** attempt))
    logger.error("webhook_emitter: gave up after %d attempts (%s) for %s",
                 RETRIES, last_err, event)


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
