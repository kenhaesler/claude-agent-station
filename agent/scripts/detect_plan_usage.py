#!/usr/bin/env python3
"""Detect Claude plan usage via CLI output scraping and heuristic token tracking.

Supports two strategies:
  - Option A (CLI scrape): Parse `claude usage` or `/usage` command output for
    session %, weekly %, and per-model breakdown.
  - Option B (heuristic): Estimate usage from tracked token consumption stored
    in the station database, applying known plan limits.

Usage:
    python detect_plan_usage.py [--db-path /path/to/station.db] [--json]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Known plan tier token limits (weekly).
# These are approximate based on publicly documented Claude plan limits.
PLAN_LIMITS: dict[str, dict[str, int]] = {
    "max_5x": {
        "claude-opus-4-6": 225_000_000,
        "claude-sonnet-4-6": 900_000_000,
        "claude-haiku-4-5-20251001": 4_500_000_000,
        "default": 900_000_000,
    },
    "pro": {
        "claude-opus-4-6": 45_000_000,
        "claude-sonnet-4-6": 180_000_000,
        "claude-haiku-4-5-20251001": 900_000_000,
        "default": 180_000_000,
    },
    "team": {
        "claude-opus-4-6": 90_000_000,
        "claude-sonnet-4-6": 360_000_000,
        "claude-haiku-4-5-20251001": 1_800_000_000,
        "default": 360_000_000,
    },
}

# Default weekly session limit (tokens) — conservative fallback
DEFAULT_WEEKLY_LIMIT = 180_000_000


@dataclass
class ModelUsage:
    """Per-model usage breakdown."""
    model: str
    tokens_used: int = 0
    tokens_limit: int = 0
    usage_percent: float = 0.0


@dataclass
class PlanUsageSnapshot:
    """A snapshot of current plan usage."""
    timestamp: str = ""
    # Session-level
    session_tokens_used: int = 0
    session_tokens_limit: int = 0
    session_usage_percent: float = 0.0
    # Weekly aggregate
    weekly_tokens_used: int = 0
    weekly_tokens_limit: int = 0
    weekly_usage_percent: float = 0.0
    weekly_reset_at: str = ""
    # Per-model breakdown
    per_model: list[ModelUsage] = field(default_factory=list)
    # Metadata
    detection_method: str = "heuristic"  # "cli_scrape" or "heuristic"
    plan_tier: str = "unknown"
    is_throttled: bool = False
    error: Optional[str] = None


def detect_usage_cli() -> Optional[PlanUsageSnapshot]:
    """Option A: Try to scrape usage data from Claude CLI.

    Attempts to run `claude usage` or parse recent stream output for
    usage/rate-limit events.

    Returns PlanUsageSnapshot or None if CLI scraping is not available.
    """
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            logger.debug("Claude CLI not available: %s", result.stderr)
            return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.debug("Claude CLI not found or timed out")
        return None

    # Try `claude usage` (may not exist in all versions)
    try:
        result = subprocess.run(
            ["claude", "usage"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return _parse_usage_output(result.stdout)
    except (subprocess.TimeoutExpired, OSError):
        pass

    # Check for rate limit indicators in recent logs
    snapshot = _check_rate_limit_signals()
    if snapshot:
        return snapshot

    return None


def _parse_usage_output(output: str) -> Optional[PlanUsageSnapshot]:
    """Parse the text output from `claude usage` command."""
    snapshot = PlanUsageSnapshot(
        timestamp=datetime.now(timezone.utc).isoformat(),
        detection_method="cli_scrape",
    )

    # Try to extract percentage patterns like "Usage: 45.2% of weekly limit"
    pct_match = re.search(r"(\d+\.?\d*)%\s*(?:of\s*)?(?:weekly|week)", output, re.IGNORECASE)
    if pct_match:
        snapshot.weekly_usage_percent = float(pct_match.group(1))

    session_pct = re.search(r"session[:\s]+(\d+\.?\d*)%", output, re.IGNORECASE)
    if session_pct:
        snapshot.session_usage_percent = float(session_pct.group(1))

    # Extract token counts "12,345 / 100,000 tokens"
    token_match = re.search(r"([\d,]+)\s*/\s*([\d,]+)\s*tokens", output, re.IGNORECASE)
    if token_match:
        used = int(token_match.group(1).replace(",", ""))
        limit = int(token_match.group(2).replace(",", ""))
        snapshot.weekly_tokens_used = used
        snapshot.weekly_tokens_limit = limit
        if limit > 0:
            snapshot.weekly_usage_percent = (used / limit) * 100.0

    # Check for throttling indicators
    if any(kw in output.lower() for kw in ["throttled", "rate limit", "slow down"]):
        snapshot.is_throttled = True

    # Extract plan tier
    tier_match = re.search(r"plan[:\s]+(pro|team|max|enterprise|free)", output, re.IGNORECASE)
    if tier_match:
        snapshot.plan_tier = tier_match.group(1).lower()

    # Per-model breakdown: "claude-sonnet-4-6: 50,000 tokens (25%)"
    for m in re.finditer(
        r"(claude-[\w-]+)[:\s]+([\d,]+)\s*tokens?\s*\((\d+\.?\d*)%\)", output
    ):
        model_name = m.group(1)
        tokens = int(m.group(2).replace(",", ""))
        pct = float(m.group(3))
        snapshot.per_model.append(ModelUsage(
            model=model_name,
            tokens_used=tokens,
            usage_percent=pct,
        ))

    return snapshot


def _check_rate_limit_signals() -> Optional[PlanUsageSnapshot]:
    """Check recent stream/log files for rate-limit or throttle events."""
    log_dir = Path("/var/log/claude-agent")
    if not log_dir.exists():
        return None

    # Look at recent stream files for rate_limit events
    stream_files = sorted(log_dir.glob("*.stream.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for sf in stream_files[:5]:
        try:
            with open(sf) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # Check for rate limit / usage events in stream
                    if event.get("type") == "error" and "rate" in str(event).lower():
                        return PlanUsageSnapshot(
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            detection_method="cli_scrape",
                            is_throttled=True,
                            weekly_usage_percent=95.0,  # Conservative estimate
                            error="Rate limit detected in stream output",
                        )
        except OSError:
            continue

    return None


def detect_usage_heuristic(
    db_path: str = "/var/lib/claude-agent-station/station.db",
    plan_tier: str = "max_5x",
) -> PlanUsageSnapshot:
    """Option B: Estimate plan usage from tracked token consumption in the DB.

    Queries the runs table for token usage within the current weekly period
    and estimates usage percentage based on known plan limits.
    """
    snapshot = PlanUsageSnapshot(
        timestamp=datetime.now(timezone.utc).isoformat(),
        detection_method="heuristic",
        plan_tier=plan_tier,
    )

    # Calculate weekly window (reset is Monday 00:00 UTC typically)
    now = datetime.now(timezone.utc)
    days_since_monday = now.weekday()
    week_start = (now - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    next_reset = week_start + timedelta(days=7)
    snapshot.weekly_reset_at = next_reset.isoformat()

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Weekly aggregate usage
        cursor.execute(
            """
            SELECT
                COALESCE(SUM(tokens_input), 0) + COALESCE(SUM(tokens_output), 0) AS total_tokens,
                COALESCE(SUM(tokens_input), 0) AS input_tokens,
                COALESCE(SUM(tokens_output), 0) AS output_tokens
            FROM runs
            WHERE started_at >= ? AND status IS NOT NULL
            """,
            (week_start.isoformat(),),
        )
        row = cursor.fetchone()
        weekly_tokens = row["total_tokens"] if row else 0
        snapshot.weekly_tokens_used = weekly_tokens

        # Get limit for the plan tier
        tier_limits = PLAN_LIMITS.get(plan_tier, PLAN_LIMITS.get("pro", {}))
        default_limit = tier_limits.get("default", DEFAULT_WEEKLY_LIMIT)
        snapshot.weekly_tokens_limit = default_limit
        if default_limit > 0:
            snapshot.weekly_usage_percent = (weekly_tokens / default_limit) * 100.0

        # Per-model breakdown
        cursor.execute(
            """
            SELECT
                model,
                COALESCE(SUM(tokens_input), 0) + COALESCE(SUM(tokens_output), 0) AS total_tokens
            FROM runs
            WHERE started_at >= ? AND model IS NOT NULL AND status IS NOT NULL
            GROUP BY model
            """,
            (week_start.isoformat(),),
        )
        for mrow in cursor.fetchall():
            model_name = mrow["model"]
            tokens = mrow["total_tokens"]
            model_limit = tier_limits.get(model_name, default_limit)
            pct = (tokens / model_limit * 100.0) if model_limit > 0 else 0.0
            snapshot.per_model.append(ModelUsage(
                model=model_name,
                tokens_used=tokens,
                tokens_limit=model_limit,
                usage_percent=pct,
            ))

        # Current session (most recent running/just-completed run)
        cursor.execute(
            """
            SELECT
                COALESCE(tokens_input, 0) + COALESCE(tokens_output, 0) AS total_tokens
            FROM runs
            WHERE status = 'running'
            ORDER BY started_at DESC
            LIMIT 1
            """,
        )
        session_row = cursor.fetchone()
        if session_row:
            snapshot.session_tokens_used = session_row["total_tokens"]
            # Session limit is typically a fraction of the weekly
            snapshot.session_tokens_limit = default_limit // 7
            if snapshot.session_tokens_limit > 0:
                snapshot.session_usage_percent = (
                    snapshot.session_tokens_used / snapshot.session_tokens_limit * 100.0
                )

        conn.close()

    except sqlite3.Error as e:
        snapshot.error = f"Database error: {e}"
        logger.error("Failed to query plan usage from DB: %s", e)
    except Exception as e:
        snapshot.error = f"Unexpected error: {e}"
        logger.exception("Unexpected error detecting plan usage")

    return snapshot


def detect_plan_usage(
    db_path: str = "/var/lib/claude-agent-station/station.db",
    plan_tier: str = "max_5x",
) -> PlanUsageSnapshot:
    """Combined detection: try CLI first, fall back to heuristic.

    Returns the best available usage snapshot.
    """
    # Try CLI scraping first (Option A)
    cli_snapshot = detect_usage_cli()
    if cli_snapshot and not cli_snapshot.error:
        logger.info("Plan usage detected via CLI scrape: %.1f%%", cli_snapshot.weekly_usage_percent)
        return cli_snapshot

    # Fall back to heuristic (Option B)
    heuristic_snapshot = detect_usage_heuristic(db_path, plan_tier)
    logger.info("Plan usage estimated via heuristic: %.1f%%", heuristic_snapshot.weekly_usage_percent)
    return heuristic_snapshot


def save_usage_snapshot(
    snapshot: PlanUsageSnapshot,
    db_path: str = "/var/lib/claude-agent-station/station.db",
) -> None:
    """Persist a usage snapshot to the plan_usage_history table."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Ensure table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS plan_usage_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                detection_method TEXT,
                plan_tier TEXT,
                session_tokens_used INTEGER DEFAULT 0,
                session_tokens_limit INTEGER DEFAULT 0,
                session_usage_percent REAL DEFAULT 0.0,
                weekly_tokens_used INTEGER DEFAULT 0,
                weekly_tokens_limit INTEGER DEFAULT 0,
                weekly_usage_percent REAL DEFAULT 0.0,
                weekly_reset_at TEXT,
                per_model_json TEXT,
                is_throttled INTEGER DEFAULT 0,
                error TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        cursor.execute(
            """
            INSERT INTO plan_usage_history (
                timestamp, detection_method, plan_tier,
                session_tokens_used, session_tokens_limit, session_usage_percent,
                weekly_tokens_used, weekly_tokens_limit, weekly_usage_percent,
                weekly_reset_at, per_model_json, is_throttled, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.timestamp,
                snapshot.detection_method,
                snapshot.plan_tier,
                snapshot.session_tokens_used,
                snapshot.session_tokens_limit,
                snapshot.session_usage_percent,
                snapshot.weekly_tokens_used,
                snapshot.weekly_tokens_limit,
                snapshot.weekly_usage_percent,
                snapshot.weekly_reset_at,
                json.dumps([asdict(m) for m in snapshot.per_model]),
                1 if snapshot.is_throttled else 0,
                snapshot.error,
            ),
        )
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        logger.error("Failed to save usage snapshot: %s", e)


def should_throttle_spawning(
    snapshot: PlanUsageSnapshot,
    max_usage_percent: float = 85.0,
) -> tuple[bool, str]:
    """Check if the plan usage is high enough to throttle new employee spawning.

    Returns (should_throttle, reason).
    """
    if snapshot.is_throttled:
        return True, "Rate limiting detected — plan usage is at capacity"

    if snapshot.weekly_usage_percent >= max_usage_percent:
        return True, (
            f"Weekly usage at {snapshot.weekly_usage_percent:.1f}% "
            f"(threshold: {max_usage_percent:.1f}%)"
        )

    # Check per-model — any single model near limit should trigger caution
    for mu in snapshot.per_model:
        if mu.usage_percent >= max_usage_percent:
            return True, (
                f"Model {mu.model} usage at {mu.usage_percent:.1f}% "
                f"(threshold: {max_usage_percent:.1f}%)"
            )

    return False, ""


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Detect Claude plan usage")
    parser.add_argument(
        "--db-path",
        default="/var/lib/claude-agent-station/station.db",
        help="Path to station database",
    )
    parser.add_argument(
        "--plan-tier",
        default="max_5x",
        choices=list(PLAN_LIMITS.keys()),
        help="Plan tier for limit calculation",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save snapshot to database",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=85.0,
        help="Usage percentage threshold for throttling warning",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    snapshot = detect_plan_usage(args.db_path, args.plan_tier)

    if args.save:
        save_usage_snapshot(snapshot, args.db_path)

    if args.json:
        print(json.dumps(asdict(snapshot), indent=2))
    else:
        print(f"Plan Usage ({snapshot.detection_method})")
        print(f"  Tier:    {snapshot.plan_tier}")
        print(f"  Weekly:  {snapshot.weekly_usage_percent:.1f}% "
              f"({snapshot.weekly_tokens_used:,} / {snapshot.weekly_tokens_limit:,} tokens)")
        print(f"  Session: {snapshot.session_usage_percent:.1f}% "
              f"({snapshot.session_tokens_used:,} / {snapshot.session_tokens_limit:,} tokens)")
        if snapshot.weekly_reset_at:
            print(f"  Reset:   {snapshot.weekly_reset_at}")
        if snapshot.per_model:
            print(f"  Models:")
            for mu in snapshot.per_model:
                print(f"    {mu.model}: {mu.usage_percent:.1f}% ({mu.tokens_used:,} tokens)")
        if snapshot.is_throttled:
            print(f"  WARNING: Throttled!")

        throttle, reason = should_throttle_spawning(snapshot, args.threshold)
        if throttle:
            print(f"  THROTTLE: {reason}")
            sys.exit(1)


if __name__ == "__main__":
    main()
