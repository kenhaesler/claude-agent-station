#!/usr/bin/env python3
"""Detect Claude plan usage via CLI output scraping and heuristic token tracking.

Supports three detection strategies (tried in order):
  - Option A (CLI scrape): Parse `claude usage` or `/usage` command output for
    session %, weekly %, and per-model breakdown.
  - Option B (error detection): Detect rate-limit errors and overuse credits
    from recent Claude CLI output / stream logs.
  - Option C (heuristic): Estimate usage from tracked token consumption stored
    in the station database, applying known plan limits.

Key concepts:
  - Session limit: Resets approximately every 4 hours. Controls burst usage.
  - Weekly rolling limit: Per-model token budget that resets on a rolling 7-day
    window. Different limits per model and plan tier.
  - Overuse credits: When plan limits are exceeded, Anthropic may allow
    continued usage at a per-token cost. This should be detected and reported.

Usage:
    python detect_plan_usage.py [--db-path /path/to/station.db] [--json]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Plan tier limits (approximate, publicly documented as of early 2026)
# ---------------------------------------------------------------------------

# Session reset interval (approximately 4 hours)
SESSION_RESET_HOURS = 4

# Known plan tier token limits (weekly per-model).
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

# Session limits per tier (approximate tokens per session window)
SESSION_LIMITS: dict[str, dict[str, int]] = {
    "max_5x": {
        "claude-opus-4-6": 32_000_000,
        "claude-sonnet-4-6": 128_000_000,
        "claude-haiku-4-5-20251001": 640_000_000,
        "default": 128_000_000,
    },
    "pro": {
        "claude-opus-4-6": 6_400_000,
        "claude-sonnet-4-6": 25_600_000,
        "claude-haiku-4-5-20251001": 128_000_000,
        "default": 25_600_000,
    },
    "team": {
        "claude-opus-4-6": 12_800_000,
        "claude-sonnet-4-6": 51_200_000,
        "claude-haiku-4-5-20251001": 256_000_000,
        "default": 51_200_000,
    },
}

# Default weekly session limit (tokens) -- conservative fallback
DEFAULT_WEEKLY_LIMIT = 180_000_000

# Rate-limit error keywords to detect in CLI output
RATE_LIMIT_KEYWORDS = [
    "rate_limit",
    "rate limit",
    "throttled",
    "429",
    "too many requests",
    "overloaded",
    "capacity",
    "slow down",
    "try again later",
]

# Overuse / billing keywords
OVERUSE_KEYWORDS = [
    "overuse",
    "additional usage",
    "overage",
    "extra credit",
    "billing",
    "usage cap exceeded",
    "beyond your plan",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ModelUsage:
    """Per-model usage breakdown."""
    model: str
    tokens_used: int = 0
    tokens_limit: int = 0
    usage_percent: float = 0.0


@dataclass
class SessionState:
    """Session-level usage tracking with reset awareness."""
    tokens_used: int = 0
    tokens_limit: int = 0
    usage_percent: float = 0.0
    session_started_at: str = ""
    session_reset_at: str = ""
    seconds_until_reset: int = 0
    is_exhausted: bool = False


@dataclass
class OveruseState:
    """Overuse / additional credit status."""
    is_overuse_active: bool = False
    overuse_detected_at: str = ""
    overuse_signals: list[str] = field(default_factory=list)


@dataclass
class PlanUsageSnapshot:
    """A snapshot of current plan usage."""
    timestamp: str = ""
    # Session-level (with reset tracking)
    session_tokens_used: int = 0
    session_tokens_limit: int = 0
    session_usage_percent: float = 0.0
    session_reset_at: str = ""
    seconds_until_session_reset: int = 0
    session_is_exhausted: bool = False
    # Weekly aggregate
    weekly_tokens_used: int = 0
    weekly_tokens_limit: int = 0
    weekly_usage_percent: float = 0.0
    weekly_reset_at: str = ""
    seconds_until_weekly_reset: int = 0
    # Per-model breakdown
    per_model: list[ModelUsage] = field(default_factory=list)
    # Overuse credit status
    overuse_active: bool = False
    overuse_signals: list[str] = field(default_factory=list)
    # Metadata
    detection_method: str = "heuristic"  # "cli_scrape", "error_detection", or "heuristic"
    plan_tier: str = "unknown"
    is_throttled: bool = False
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Real-time plan state API (primary entry point for manager)
# ---------------------------------------------------------------------------

def get_realtime_plan_state(
    db_path: str = "/var/lib/claude-agent-station/station.db",
    plan_tier: str = "max_5x",
) -> dict:
    """Return current plan usage percentage and time until next reset.

    This is the primary function the manager calls before spawning employees.
    It returns a simple, actionable dictionary with everything needed for
    spawn decisions.

    Returns:
        {
            "can_spawn": bool,
            "weekly_usage_percent": float,
            "session_usage_percent": float,
            "session_is_exhausted": bool,
            "seconds_until_session_reset": int,
            "seconds_until_weekly_reset": int,
            "is_throttled": bool,
            "overuse_active": bool,
            "reason": str,          # why spawning is blocked (empty if OK)
            "recommended_action": str,  # "proceed", "reduce", "wait", "stop"
            "snapshot": PlanUsageSnapshot,
        }
    """
    snapshot = detect_plan_usage(db_path, plan_tier)

    # Determine recommended action and spawn permission
    can_spawn = True
    reason = ""
    action = "proceed"

    if snapshot.is_throttled or snapshot.session_is_exhausted:
        can_spawn = False
        if snapshot.session_is_exhausted:
            reason = (
                f"Session limit exhausted. "
                f"Resets in {_format_duration(snapshot.seconds_until_session_reset)}."
            )
        else:
            reason = "Rate limiting detected -- plan usage is at capacity."
        action = "wait"

    elif snapshot.overuse_active:
        can_spawn = False
        reason = "Overuse credits active -- continued usage incurs extra charges."
        action = "stop"

    elif snapshot.weekly_usage_percent >= 95.0:
        can_spawn = False
        reason = (
            f"Weekly usage at {snapshot.weekly_usage_percent:.1f}% "
            f"(critical threshold: 95%)."
        )
        action = "stop"

    elif snapshot.weekly_usage_percent >= 90.0:
        can_spawn = True  # allow 1 at most, handled by caller
        reason = (
            f"Weekly usage at {snapshot.weekly_usage_percent:.1f}% "
            f"(approaching limit)."
        )
        action = "reduce"

    elif snapshot.weekly_usage_percent >= 85.0:
        can_spawn = True
        reason = (
            f"Weekly usage at {snapshot.weekly_usage_percent:.1f}% "
            f"(throttle threshold: 85%)."
        )
        action = "reduce"

    # Check per-model limits
    for mu in snapshot.per_model:
        if mu.usage_percent >= 95.0:
            can_spawn = False
            reason = (
                f"Model {mu.model} at {mu.usage_percent:.1f}% "
                f"(critical threshold: 95%)."
            )
            action = "stop"
            break
        elif mu.usage_percent >= 85.0 and action == "proceed":
            action = "reduce"
            reason = (
                f"Model {mu.model} at {mu.usage_percent:.1f}% "
                f"(throttle threshold: 85%)."
            )

    return {
        "can_spawn": can_spawn,
        "weekly_usage_percent": snapshot.weekly_usage_percent,
        "session_usage_percent": snapshot.session_usage_percent,
        "session_is_exhausted": snapshot.session_is_exhausted,
        "seconds_until_session_reset": snapshot.seconds_until_session_reset,
        "seconds_until_weekly_reset": snapshot.seconds_until_weekly_reset,
        "is_throttled": snapshot.is_throttled,
        "overuse_active": snapshot.overuse_active,
        "reason": reason,
        "recommended_action": action,
        "snapshot": snapshot,
    }


# ---------------------------------------------------------------------------
# Option A: CLI scraping
# ---------------------------------------------------------------------------

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
    """Parse the text output from `claude usage` command.

    Handles various output formats including:
    - English: "Usage: 45.2% of weekly limit"
    - German: "100% verwendet (Zuruecksetzung in 6 Min.)"
    - Token counts: "12,345 / 100,000 tokens"
    - Per-model breakdown
    - Session vs weekly distinction
    """
    now = datetime.now(timezone.utc)
    snapshot = PlanUsageSnapshot(
        timestamp=now.isoformat(),
        detection_method="cli_scrape",
    )

    # Try to extract weekly percentage patterns
    pct_match = re.search(
        r"(\d+\.?\d*)%\s*(?:of\s*)?(?:weekly|week|wöchentlich)",
        output, re.IGNORECASE,
    )
    if pct_match:
        snapshot.weekly_usage_percent = float(pct_match.group(1))

    # Session percentage: "session: 75%" or "Sitzung: 100% verwendet"
    session_pct = re.search(
        r"(?:session|sitzung|aktuelle)[:\s]+(\d+\.?\d*)%",
        output, re.IGNORECASE,
    )
    if session_pct:
        snapshot.session_usage_percent = float(session_pct.group(1))
        if snapshot.session_usage_percent >= 99.0:
            snapshot.session_is_exhausted = True

    # Extract reset time: "Zuruecksetzung in 6 Min." or "resets in 3h 45m"
    reset_match = re.search(
        r"(?:reset|zurücksetzung|resets?\s+in)\s+(?:in\s+)?(\d+)\s*(?:min|m(?:in)?\.?|h(?:ours?)?|std)",
        output, re.IGNORECASE,
    )
    if reset_match:
        reset_val = int(reset_match.group(1))
        unit = reset_match.group(0).lower()
        if any(h in unit for h in ["h", "std", "hour"]):
            reset_seconds = reset_val * 3600
        else:
            reset_seconds = reset_val * 60
        snapshot.seconds_until_session_reset = reset_seconds
        snapshot.session_reset_at = (now + timedelta(seconds=reset_seconds)).isoformat()

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
    lower_output = output.lower()
    if any(kw in lower_output for kw in RATE_LIMIT_KEYWORDS):
        snapshot.is_throttled = True

    # Check for overuse indicators
    overuse_signals = [kw for kw in OVERUSE_KEYWORDS if kw in lower_output]
    if overuse_signals:
        snapshot.overuse_active = True
        snapshot.overuse_signals = overuse_signals

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

    # Alternative per-model: "Model claude-sonnet-4-6: 45% verwendet"
    for m in re.finditer(
        r"(?:model\s+)?(claude-[\w-]+)[:\s]+(\d+\.?\d*)%",
        output, re.IGNORECASE,
    ):
        model_name = m.group(1)
        pct = float(m.group(2))
        # Avoid duplicates
        if not any(mu.model == model_name for mu in snapshot.per_model):
            snapshot.per_model.append(ModelUsage(
                model=model_name,
                usage_percent=pct,
            ))

    return snapshot


# ---------------------------------------------------------------------------
# Option B: Error / rate-limit signal detection
# ---------------------------------------------------------------------------

def detect_rate_limit_errors(
    log_dir: str = "/var/log/claude-agent",
    lookback_minutes: int = 30,
) -> Optional[PlanUsageSnapshot]:
    """Detect rate-limit and overuse signals from recent Claude CLI output.

    Scans stream logs and stderr files for rate-limit errors, 429 responses,
    and overuse credit indicators.

    Args:
        log_dir: Directory containing agent log files.
        lookback_minutes: How far back to scan for signals.

    Returns:
        PlanUsageSnapshot if rate limiting or overuse detected, None otherwise.
    """
    log_path = Path(log_dir)
    if not log_path.exists():
        return None

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=lookback_minutes)
    rate_limit_count = 0
    overuse_signals: list[str] = []
    latest_error_time: Optional[datetime] = None

    # Check stream JSONL files
    stream_files = sorted(
        log_path.glob("*.stream.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for sf in stream_files[:10]:
        try:
            mtime = datetime.fromtimestamp(sf.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                continue

            with open(sf) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Only inspect actual error events, not all events.
                    # Avoid converting the entire event dict to string
                    # which causes false positives from tool output content.
                    event_type = event.get("type", "")

                    # Skip rate_limit_event with status:"allowed" (not an error)
                    if event_type == "rate_limit_event":
                        if event.get("status") == "allowed":
                            continue

                    # Only check events that are genuine errors
                    is_error_event = event_type in ("error", "system") or event.get("is_error")
                    if not is_error_event:
                        continue

                    # Extract the error message text, not the entire event
                    error_text = str(
                        event.get("error", event.get("message", event.get("content", "")))
                    ).lower()

                    for kw in RATE_LIMIT_KEYWORDS:
                        if kw in error_text:
                            rate_limit_count += 1
                            latest_error_time = now
                            break

                    for kw in OVERUSE_KEYWORDS:
                        if kw in error_text:
                            overuse_signals.append(kw)

        except OSError:
            continue

    # Check stderr/log files for Claude CLI errors
    log_files = sorted(
        list(log_path.glob("*.log")) + list(log_path.glob("*.stderr")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for lf in log_files[:10]:
        try:
            mtime = datetime.fromtimestamp(lf.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                continue

            with open(lf) as f:
                content = f.read()
            lower_content = content.lower()

            for kw in RATE_LIMIT_KEYWORDS:
                if kw in lower_content:
                    rate_limit_count += 1
                    latest_error_time = now
                    break

            for kw in OVERUSE_KEYWORDS:
                if kw in lower_content:
                    overuse_signals.append(kw)

        except OSError:
            continue

    if rate_limit_count == 0 and not overuse_signals:
        return None

    snapshot = PlanUsageSnapshot(
        timestamp=now.isoformat(),
        detection_method="error_detection",
        is_throttled=rate_limit_count > 0,
        overuse_active=len(overuse_signals) > 0,
        overuse_signals=list(set(overuse_signals)),
    )

    if rate_limit_count > 0:
        # Conservative estimate: if we're hitting rate limits, assume high usage
        snapshot.weekly_usage_percent = 95.0
        snapshot.session_usage_percent = 100.0
        snapshot.session_is_exhausted = True
        snapshot.error = (
            f"Rate limit detected ({rate_limit_count} signal(s) "
            f"in last {lookback_minutes} minutes)"
        )

    if overuse_signals:
        snapshot.overuse_active = True

    return snapshot


def _check_rate_limit_signals() -> Optional[PlanUsageSnapshot]:
    """Check recent stream/log files for rate-limit or throttle events.

    Legacy compatibility wrapper around detect_rate_limit_errors().
    """
    return detect_rate_limit_errors()


# ---------------------------------------------------------------------------
# Option C: Heuristic estimation from DB
# ---------------------------------------------------------------------------

def detect_usage_heuristic(
    db_path: str = "/var/lib/claude-agent-station/station.db",
    plan_tier: str = "max_5x",
) -> PlanUsageSnapshot:
    """Option C: Estimate plan usage from tracked token consumption in the DB.

    Queries the runs table for token usage within the current weekly period
    and session window, then estimates usage percentage based on known plan limits.

    Tracks both:
    - Session usage: tokens used in the last SESSION_RESET_HOURS hours
    - Weekly usage: tokens used since the weekly reset point (rolling 7-day window)
    """
    now = datetime.now(timezone.utc)
    snapshot = PlanUsageSnapshot(
        timestamp=now.isoformat(),
        detection_method="heuristic",
        plan_tier=plan_tier,
    )

    # Calculate weekly window (reset is Monday 00:00 UTC typically)
    days_since_monday = now.weekday()
    week_start = (now - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    next_weekly_reset = week_start + timedelta(days=7)
    snapshot.weekly_reset_at = next_weekly_reset.isoformat()
    snapshot.seconds_until_weekly_reset = max(
        0, int((next_weekly_reset - now).total_seconds())
    )

    # Calculate session window (~4 hours rolling)
    session_start = now - timedelta(hours=SESSION_RESET_HOURS)
    session_reset_at = now + timedelta(hours=SESSION_RESET_HOURS)
    snapshot.session_reset_at = session_reset_at.isoformat()
    # Approximate: next reset is at most SESSION_RESET_HOURS from last activity
    # Use a conservative estimate of remaining time
    snapshot.seconds_until_session_reset = SESSION_RESET_HOURS * 3600

    # Get tier limits
    tier_limits = PLAN_LIMITS.get(plan_tier, PLAN_LIMITS.get("pro", {}))
    session_tier_limits = SESSION_LIMITS.get(plan_tier, SESSION_LIMITS.get("pro", {}))
    default_weekly_limit = tier_limits.get("default", DEFAULT_WEEKLY_LIMIT)
    default_session_limit = session_tier_limits.get("default", default_weekly_limit // 7)

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # ------- Weekly aggregate usage -------
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
        snapshot.weekly_tokens_limit = default_weekly_limit
        if default_weekly_limit > 0:
            snapshot.weekly_usage_percent = (weekly_tokens / default_weekly_limit) * 100.0

        # ------- Session usage (last SESSION_RESET_HOURS hours) -------
        cursor.execute(
            """
            SELECT
                COALESCE(SUM(tokens_input), 0) + COALESCE(SUM(tokens_output), 0) AS total_tokens
            FROM runs
            WHERE started_at >= ? AND status IS NOT NULL
            """,
            (session_start.isoformat(),),
        )
        session_row = cursor.fetchone()
        session_tokens = session_row["total_tokens"] if session_row else 0
        snapshot.session_tokens_used = session_tokens
        snapshot.session_tokens_limit = default_session_limit
        if default_session_limit > 0:
            snapshot.session_usage_percent = (session_tokens / default_session_limit) * 100.0
        snapshot.session_is_exhausted = snapshot.session_usage_percent >= 99.0

        # Estimate when session resets: find earliest run in session window
        cursor.execute(
            """
            SELECT MIN(started_at) AS earliest
            FROM runs
            WHERE started_at >= ? AND status IS NOT NULL
            """,
            (session_start.isoformat(),),
        )
        earliest_row = cursor.fetchone()
        if earliest_row and earliest_row["earliest"]:
            try:
                earliest_dt = datetime.fromisoformat(earliest_row["earliest"])
                if earliest_dt.tzinfo is None:
                    earliest_dt = earliest_dt.replace(tzinfo=timezone.utc)
                estimated_reset = earliest_dt + timedelta(hours=SESSION_RESET_HOURS)
                if estimated_reset > now:
                    snapshot.seconds_until_session_reset = max(
                        0, int((estimated_reset - now).total_seconds())
                    )
                    snapshot.session_reset_at = estimated_reset.isoformat()
                else:
                    # Session window has likely already reset
                    snapshot.seconds_until_session_reset = 0
            except (ValueError, TypeError):
                pass

        # ------- Per-model breakdown (weekly) -------
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
            model_limit = tier_limits.get(model_name, default_weekly_limit)
            pct = (tokens / model_limit * 100.0) if model_limit > 0 else 0.0
            snapshot.per_model.append(ModelUsage(
                model=model_name,
                tokens_used=tokens,
                tokens_limit=model_limit,
                usage_percent=pct,
            ))

        # ------- Check for overuse signals in recent runs -------
        # Note: the runs table does NOT have an error_message column.
        # Overuse signal detection is handled by detect_rate_limit_errors()
        # which scans stream JSONL files directly. This section intentionally
        # left empty to avoid sqlite3.OperationalError on missing column.

        conn.close()

    except sqlite3.Error as e:
        snapshot.error = f"Database error: {e}"
        logger.error("Failed to query plan usage from DB: %s", e)
    except Exception as e:
        snapshot.error = f"Unexpected error: {e}"
        logger.exception("Unexpected error detecting plan usage")

    return snapshot


# ---------------------------------------------------------------------------
# Combined detection
# ---------------------------------------------------------------------------

def detect_plan_usage(
    db_path: str = "/var/lib/claude-agent-station/station.db",
    plan_tier: str = "max_5x",
) -> PlanUsageSnapshot:
    """Combined detection: try CLI first, then error detection, fall back to heuristic.

    Returns the best available usage snapshot with session limits, weekly limits,
    per-model breakdown, and overuse credit status.
    """
    # Option A: Try CLI scraping first
    cli_snapshot = detect_usage_cli()
    if cli_snapshot and not cli_snapshot.error:
        logger.info(
            "Plan usage detected via CLI scrape: weekly=%.1f%% session=%.1f%%",
            cli_snapshot.weekly_usage_percent,
            cli_snapshot.session_usage_percent,
        )
        return cli_snapshot

    # Option B: Check for rate-limit error signals
    error_snapshot = detect_rate_limit_errors()
    if error_snapshot and (error_snapshot.is_throttled or error_snapshot.overuse_active):
        logger.warning(
            "Rate limit / overuse detected via error signals: throttled=%s overuse=%s",
            error_snapshot.is_throttled,
            error_snapshot.overuse_active,
        )
        # Merge with heuristic data for richer context
        heuristic_snapshot = detect_usage_heuristic(db_path, plan_tier)
        return _merge_snapshots(error_snapshot, heuristic_snapshot)

    # Option C: Fall back to heuristic
    heuristic_snapshot = detect_usage_heuristic(db_path, plan_tier)
    logger.info(
        "Plan usage estimated via heuristic: weekly=%.1f%% session=%.1f%%",
        heuristic_snapshot.weekly_usage_percent,
        heuristic_snapshot.session_usage_percent,
    )
    return heuristic_snapshot


def _merge_snapshots(
    primary: PlanUsageSnapshot,
    secondary: PlanUsageSnapshot,
) -> PlanUsageSnapshot:
    """Merge two snapshots, preferring primary for detection signals
    but filling in numerical data from secondary where primary lacks it.

    If the primary snapshot has an error (e.g. error_detection failed to
    find real signals), prefer secondary's numerical data instead of
    taking the artificially inflated "worst case" values from the primary.
    """
    # If primary errored or has no real token data, prefer secondary's numbers
    primary_has_real_data = (
        primary.weekly_tokens_used > 0 or primary.session_tokens_used > 0
    )
    primary_has_error = bool(primary.error)

    # For percentage fields: only take primary's inflated values if it has
    # genuine data, not heuristic error guesses
    if primary_has_real_data and not primary_has_error:
        session_pct = max(primary.session_usage_percent, secondary.session_usage_percent)
        weekly_pct = max(primary.weekly_usage_percent, secondary.weekly_usage_percent)
        is_exhausted = primary.session_is_exhausted or secondary.session_is_exhausted
    else:
        # Primary was error-based (95%/100% guesses) — use secondary's real data
        session_pct = secondary.session_usage_percent
        weekly_pct = secondary.weekly_usage_percent
        is_exhausted = secondary.session_is_exhausted

    merged = PlanUsageSnapshot(
        timestamp=primary.timestamp or secondary.timestamp,
        detection_method=f"{primary.detection_method}+{secondary.detection_method}",
        plan_tier=primary.plan_tier if primary.plan_tier != "unknown" else secondary.plan_tier,
        # Use real token counts from whichever source has them
        session_tokens_used=max(primary.session_tokens_used, secondary.session_tokens_used),
        session_tokens_limit=secondary.session_tokens_limit or primary.session_tokens_limit,
        session_usage_percent=session_pct,
        session_reset_at=primary.session_reset_at or secondary.session_reset_at,
        seconds_until_session_reset=(
            primary.seconds_until_session_reset
            if primary.seconds_until_session_reset > 0
            else secondary.seconds_until_session_reset
        ),
        session_is_exhausted=is_exhausted,
        weekly_tokens_used=max(primary.weekly_tokens_used, secondary.weekly_tokens_used),
        weekly_tokens_limit=secondary.weekly_tokens_limit or primary.weekly_tokens_limit,
        weekly_usage_percent=weekly_pct,
        weekly_reset_at=primary.weekly_reset_at or secondary.weekly_reset_at,
        seconds_until_weekly_reset=(
            primary.seconds_until_weekly_reset
            if primary.seconds_until_weekly_reset > 0
            else secondary.seconds_until_weekly_reset
        ),
        per_model=primary.per_model if primary.per_model else secondary.per_model,
        overuse_active=primary.overuse_active or secondary.overuse_active,
        overuse_signals=list(set(primary.overuse_signals + secondary.overuse_signals)),
        is_throttled=primary.is_throttled or secondary.is_throttled,
        error=primary.error or secondary.error,
    )
    return merged


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_usage_snapshot(
    snapshot: PlanUsageSnapshot,
    db_path: str = "/var/lib/claude-agent-station/station.db",
) -> None:
    """Persist a usage snapshot to the plan_usage_history table."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Ensure table exists (with new columns)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS plan_usage_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                detection_method TEXT,
                plan_tier TEXT,
                session_tokens_used INTEGER DEFAULT 0,
                session_tokens_limit INTEGER DEFAULT 0,
                session_usage_percent REAL DEFAULT 0.0,
                session_reset_at TEXT,
                seconds_until_session_reset INTEGER DEFAULT 0,
                session_is_exhausted INTEGER DEFAULT 0,
                weekly_tokens_used INTEGER DEFAULT 0,
                weekly_tokens_limit INTEGER DEFAULT 0,
                weekly_usage_percent REAL DEFAULT 0.0,
                weekly_reset_at TEXT,
                seconds_until_weekly_reset INTEGER DEFAULT 0,
                per_model_json TEXT,
                is_throttled INTEGER DEFAULT 0,
                overuse_active INTEGER DEFAULT 0,
                overuse_signals_json TEXT,
                error TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        cursor.execute(
            """
            INSERT INTO plan_usage_history (
                timestamp, detection_method, plan_tier,
                session_tokens_used, session_tokens_limit, session_usage_percent,
                session_reset_at, seconds_until_session_reset, session_is_exhausted,
                weekly_tokens_used, weekly_tokens_limit, weekly_usage_percent,
                weekly_reset_at, seconds_until_weekly_reset,
                per_model_json, is_throttled,
                overuse_active, overuse_signals_json, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.timestamp,
                snapshot.detection_method,
                snapshot.plan_tier,
                snapshot.session_tokens_used,
                snapshot.session_tokens_limit,
                snapshot.session_usage_percent,
                snapshot.session_reset_at,
                snapshot.seconds_until_session_reset,
                1 if snapshot.session_is_exhausted else 0,
                snapshot.weekly_tokens_used,
                snapshot.weekly_tokens_limit,
                snapshot.weekly_usage_percent,
                snapshot.weekly_reset_at,
                snapshot.seconds_until_weekly_reset,
                json.dumps([asdict(m) for m in snapshot.per_model]),
                1 if snapshot.is_throttled else 0,
                1 if snapshot.overuse_active else 0,
                json.dumps(snapshot.overuse_signals),
                snapshot.error,
            ),
        )
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        logger.error("Failed to save usage snapshot: %s", e)


# ---------------------------------------------------------------------------
# Throttling decisions
# ---------------------------------------------------------------------------

def should_throttle_spawning(
    snapshot: PlanUsageSnapshot,
    max_usage_percent: float = 85.0,
) -> tuple[bool, str]:
    """Check if the plan usage is high enough to throttle new employee spawning.

    Now checks session limits and overuse status in addition to weekly limits.

    Returns (should_throttle, reason).
    """
    # Session exhausted -- must wait for reset
    if snapshot.session_is_exhausted:
        wait_str = _format_duration(snapshot.seconds_until_session_reset)
        return True, (
            f"Session limit exhausted. Resets in {wait_str}."
        )

    # Active rate limiting
    if snapshot.is_throttled:
        return True, "Rate limiting detected -- plan usage is at capacity."

    # Overuse credits active
    if snapshot.overuse_active:
        return True, (
            "Overuse credits active -- continued usage incurs extra charges. "
            f"Signals: {', '.join(snapshot.overuse_signals)}"
        )

    # Weekly aggregate over threshold
    if snapshot.weekly_usage_percent >= max_usage_percent:
        return True, (
            f"Weekly usage at {snapshot.weekly_usage_percent:.1f}% "
            f"(threshold: {max_usage_percent:.1f}%)"
        )

    # Session approaching limit (>90% of session)
    if snapshot.session_usage_percent >= 90.0:
        wait_str = _format_duration(snapshot.seconds_until_session_reset)
        return True, (
            f"Session usage at {snapshot.session_usage_percent:.1f}% "
            f"(near session limit). Resets in {wait_str}."
        )

    # Check per-model -- any single model near limit should trigger caution
    for mu in snapshot.per_model:
        if mu.usage_percent >= max_usage_percent:
            return True, (
                f"Model {mu.model} usage at {mu.usage_percent:.1f}% "
                f"(threshold: {max_usage_percent:.1f}%)"
            )

    return False, ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_duration(seconds: int) -> str:
    """Format seconds as a human-readable duration string."""
    if seconds <= 0:
        return "now"
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remaining_minutes = minutes % 60
    if remaining_minutes > 0:
        return f"{hours}h {remaining_minutes}m"
    return f"{hours}h"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

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
    parser.add_argument(
        "--realtime",
        action="store_true",
        help="Use get_realtime_plan_state() for actionable spawn decision output",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if args.realtime:
        state = get_realtime_plan_state(args.db_path, args.plan_tier)
        if args.json:
            # Serialize snapshot separately
            output = {k: v for k, v in state.items() if k != "snapshot"}
            output["snapshot"] = asdict(state["snapshot"])
            print(json.dumps(output, indent=2))
        else:
            print(f"Real-time Plan State")
            print(f"  Can spawn:    {state['can_spawn']}")
            print(f"  Action:       {state['recommended_action']}")
            print(f"  Weekly:       {state['weekly_usage_percent']:.1f}%")
            print(f"  Session:      {state['session_usage_percent']:.1f}%")
            print(f"  Exhausted:    {state['session_is_exhausted']}")
            print(f"  Throttled:    {state['is_throttled']}")
            print(f"  Overuse:      {state['overuse_active']}")
            if state['seconds_until_session_reset'] > 0:
                print(f"  Session reset: {_format_duration(state['seconds_until_session_reset'])}")
            if state['seconds_until_weekly_reset'] > 0:
                print(f"  Weekly reset:  {_format_duration(state['seconds_until_weekly_reset'])}")
            if state['reason']:
                print(f"  Reason:       {state['reason']}")
        if not state['can_spawn']:
            sys.exit(1)
        return

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
        if snapshot.session_is_exhausted:
            print(f"  SESSION EXHAUSTED! Resets in {_format_duration(snapshot.seconds_until_session_reset)}")
        if snapshot.weekly_reset_at:
            print(f"  Weekly reset:   {snapshot.weekly_reset_at}")
        if snapshot.session_reset_at:
            print(f"  Session reset:  {snapshot.session_reset_at}")
        if snapshot.per_model:
            print(f"  Models:")
            for mu in snapshot.per_model:
                print(f"    {mu.model}: {mu.usage_percent:.1f}% ({mu.tokens_used:,} tokens)")
        if snapshot.overuse_active:
            print(f"  OVERUSE ACTIVE: {', '.join(snapshot.overuse_signals)}")
        if snapshot.is_throttled:
            print(f"  WARNING: Throttled!")

        throttle, reason = should_throttle_spawning(snapshot, args.threshold)
        if throttle:
            print(f"  THROTTLE: {reason}")
            sys.exit(1)


if __name__ == "__main__":
    main()
