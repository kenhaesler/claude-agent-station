"""CLI decision gateway: bridges bash orchestration to Python intelligence.

Usage from run-manager.sh:
    python3 -m agent.coordinator.decide select-mode --issue-json <file> --config <file> --project-mode <mode>
    python3 -m agent.coordinator.decide check-confidence --report-file <file> --config <file>
    python3 -m agent.coordinator.decide check-escalation --report-file <file> --config <file> --queue-item-id <id>
    python3 -m agent.coordinator.decide record-outcome --project-repo <repo> --issue-number <n> ...
    python3 -m agent.coordinator.decide should-verify --report-file <file> --config <file>

Each subcommand prints JSON to stdout and exits 0.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Base URL for the dashboard API (webhook events + outcome recording)
DASHBOARD_API = "http://127.0.0.1:8420"


def _load_json(path: str) -> dict:
    """Load a JSON file, returning empty dict on failure."""
    try:
        return json.loads(Path(path).read_text())
    except Exception as e:
        logger.warning("Failed to load %s: %s", path, e)
        return {}


def _config_get(config: dict, dotpath: str, default=None):
    """Read a dot-separated path from a nested config dict."""
    keys = dotpath.split(".")
    current = config
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return default
        if current is None:
            return default
    return current


def _emit_event(run_id: str | None, event_type: str, event_data: dict) -> None:
    """Fire-and-forget POST to the agent events API for audit trail."""
    try:
        import urllib.request
        payload = json.dumps({
            "workflow_id": run_id or "unknown",
            "run_id": run_id,
            "agent_id": "intelligence",
            "event_type": event_type,
            "event_data": json.dumps(event_data),
        }).encode()
        req = urllib.request.Request(
            f"{DASHBOARD_API}/api/agent-events",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass  # Best-effort audit, never block the decision


# ---------------------------------------------------------------------------
# Subcommand: select-mode
# ---------------------------------------------------------------------------

def cmd_select_mode(args: argparse.Namespace) -> None:
    """Select optimal mode/model/turns for an issue."""
    issue = _load_json(args.issue_json)
    config = _load_json(args.config) if args.config else {}
    project_mode = args.project_mode or "full"

    from agent.coordinator.mode_selector import (
        ModeDecision,
        default_mode_decision,
        select_mode_from_labels,
    )

    # Check if auto mode selection is enabled
    auto_enabled = _config_get(config, "intelligence.auto_mode_selection", False)
    if not auto_enabled:
        decision = default_mode_decision(project_mode)
        _print_decision(decision, args.run_id)
        return

    # Tier 1: Label-based fast path
    labels = issue.get("labels", [])
    decision = select_mode_from_labels(labels)
    if decision:
        _emit_event(args.run_id, "intelligence.mode_selected", {
            "mode": decision.mode,
            "model": decision.model,
            "max_turns": decision.max_turns,
            "complexity_score": decision.complexity_score,
            "source": decision.source,
            "reasoning": decision.reasoning,
        })
        _print_decision(decision, args.run_id)
        return

    # Tier 2: Haiku complexity scoring
    from agent.coordinator.mode_selector import build_complexity_prompt, parse_complexity_response
    prompt = build_complexity_prompt(issue)

    # Call Haiku via Claude CLI for complexity assessment
    haiku_decision = _call_haiku(prompt)
    if haiku_decision:
        # Tier 3: Adaptive override (if enabled and enough data)
        adaptive_enabled = _config_get(config, "intelligence.adaptive_scheduling", False)
        if adaptive_enabled and haiku_decision.complexity_score:
            adaptive_decision = _try_adaptive_override(
                issue, haiku_decision, config
            )
            if adaptive_decision:
                _emit_event(args.run_id, "intelligence.adaptive_override", {
                    "haiku_mode": haiku_decision.mode,
                    "haiku_model": haiku_decision.model,
                    "adaptive_mode": adaptive_decision.mode,
                    "adaptive_model": adaptive_decision.model,
                    "sample_count": adaptive_decision.complexity_score,  # repurposed
                    "reasoning": adaptive_decision.reasoning,
                })
                _emit_event(args.run_id, "intelligence.mode_selected", {
                    "mode": adaptive_decision.mode,
                    "model": adaptive_decision.model,
                    "max_turns": adaptive_decision.max_turns,
                    "complexity_score": haiku_decision.complexity_score,
                    "source": adaptive_decision.source,
                    "reasoning": adaptive_decision.reasoning,
                })
                _print_decision(adaptive_decision, args.run_id)
                return

        _emit_event(args.run_id, "intelligence.mode_selected", {
            "mode": haiku_decision.mode,
            "model": haiku_decision.model,
            "max_turns": haiku_decision.max_turns,
            "complexity_score": haiku_decision.complexity_score,
            "source": haiku_decision.source,
            "reasoning": haiku_decision.reasoning,
        })
        _print_decision(haiku_decision, args.run_id)
        return

    # Fallback to project default
    decision = default_mode_decision(project_mode)
    _emit_event(args.run_id, "intelligence.mode_selected", {
        "mode": decision.mode, "model": decision.model,
        "max_turns": decision.max_turns, "source": "default",
        "reasoning": "Haiku assessment failed, using project default",
    })
    _print_decision(decision, args.run_id)


def _call_haiku(prompt: str):
    """Call Haiku via Anthropic SDK for complexity assessment."""
    from agent.coordinator.mode_selector import parse_complexity_response

    try:
        from agent.coordinator.llm import call_llm
        resp = call_llm(prompt, model="claude-haiku-4-5-20251001", max_tokens=512)
        if resp.text.strip():
            return parse_complexity_response(resp.text)
    except Exception as e:
        logger.warning("Haiku call failed: %s", e)
    return None


def _try_adaptive_override(issue: dict, haiku_decision, config: dict):
    """Tier 3: Check if historical data suggests a better config."""
    from agent.coordinator.mode_selector import ModeDecision

    # Determine issue type from labels
    labels = issue.get("labels", [])
    label_names = [
        l.get("name", "") if isinstance(l, dict) else str(l) for l in labels
    ]
    issue_type = "feature"  # default
    for name in label_names:
        if name in ("bug", "fix", "hotfix"):
            issue_type = "bug"
            break
        elif name in ("chore", "maintenance", "docs"):
            issue_type = "chore"
            break

    try:
        from dashboard.backend.app.services.adaptive_scheduler import predict_effort_sync
        prediction = predict_effort_sync(
            issue_type=issue_type,
            complexity=haiku_decision.complexity_score,
            db_path=_config_get(config, "dashboard.db_path")
                    or "/var/lib/claude-agent-station/station.db",
        )
        if prediction and prediction.confidence > (haiku_decision.complexity_score or 3) / 5.0:
            from agent.coordinator.modes import MODE_REGISTRY
            spec = MODE_REGISTRY.get(prediction.mode)
            return ModeDecision(
                mode=prediction.mode,
                model=prediction.model,
                max_turns=spec.default_max_turns if spec else 100,
                complexity_score=prediction.sample_count,
                reasoning=f"Adaptive: {prediction.sample_count} samples, "
                          f"{prediction.confidence:.0%} success rate",
                source="adaptive",
            )
    except Exception as e:
        logger.warning("Adaptive lookup failed: %s", e)

    return None


def _print_decision(decision, run_id: str | None = None) -> None:
    """Print a ModeDecision as JSON to stdout."""
    print(json.dumps({
        "mode": decision.mode,
        "model": decision.model,
        "max_turns": decision.max_turns,
        "complexity_score": decision.complexity_score,
        "reasoning": decision.reasoning,
        "source": decision.source,
    }))


# ---------------------------------------------------------------------------
# Subcommand: check-confidence
# ---------------------------------------------------------------------------

def cmd_check_confidence(args: argparse.Namespace) -> None:
    """Check if an employee report passes the confidence gate for auto-PR."""
    report = _load_json(args.report_file)
    config = _load_json(args.config) if args.config else {}

    confidence_enabled = _config_get(config, "intelligence.confidence_gating", False)
    if not confidence_enabled:
        print(json.dumps({"gate_passed": False, "reason": "confidence_gating disabled"}))
        return

    # Extract report data
    confidence = float(report.get("confidence", 0))
    tests_passed = report.get("tests_passed", False)
    status = report.get("status", "")
    mode = report.get("mode", "full")

    # Get threshold from mode spec
    from agent.coordinator.modes import MODE_REGISTRY
    spec = MODE_REGISTRY.get(mode)
    threshold = spec.confidence_threshold if spec else 0.9

    gate_passed = (
        confidence >= threshold
        and tests_passed is True
        and status == "success"
    )

    result = {
        "gate_passed": gate_passed,
        "confidence": confidence,
        "threshold": threshold,
        "tests_passed": tests_passed,
        "status": status,
        "mode": mode,
    }

    if not gate_passed:
        reasons = []
        if confidence < threshold:
            reasons.append(f"confidence {confidence:.2f} < threshold {threshold:.2f}")
        if not tests_passed:
            reasons.append("tests not passed")
        if status != "success":
            reasons.append(f"status is '{status}', not 'success'")
        result["reason"] = "; ".join(reasons)

    event_type = "intelligence.confidence_gate_passed" if gate_passed else "intelligence.confidence_gate_failed"
    _emit_event(args.run_id, event_type, result)

    print(json.dumps(result))


# ---------------------------------------------------------------------------
# Subcommand: check-escalation
# ---------------------------------------------------------------------------

def cmd_check_escalation(args: argparse.Namespace) -> None:
    """Check if an employee report should trigger escalation."""
    report = _load_json(args.report_file)
    config = _load_json(args.config) if args.config else {}

    deepening_enabled = _config_get(config, "intelligence.progressive_deepening", False)
    if not deepening_enabled:
        print(json.dumps({"should_escalate": False, "reason": "progressive_deepening disabled"}))
        return

    confidence = float(report.get("confidence", 0))
    tests_passed = report.get("tests_passed", True)
    status = report.get("status", "success")
    mode = report.get("mode", "full")
    current_rung = int(report.get("escalation_rung", 0))

    from agent.coordinator.modes import MODE_REGISTRY, ESCALATION_LADDER, next_rung
    spec = MODE_REGISTRY.get(mode)
    threshold = spec.confidence_threshold if spec else 0.9

    # Determine if escalation is needed
    triggers = []
    if confidence < threshold:
        triggers.append(f"confidence {confidence:.2f} < threshold {threshold:.2f}")
    if not tests_passed:
        triggers.append("tests failed")
    if status in ("partial", "failure", "failed"):
        triggers.append(f"status: {status}")

    should_escalate = len(triggers) > 0
    rung_info = next_rung(current_rung)

    if not rung_info:
        should_escalate = False
        triggers.append("already at max rung")

    result = {
        "should_escalate": should_escalate,
        "current_rung": current_rung,
        "triggers": triggers,
    }

    if should_escalate and rung_info:
        result["next_rung"] = rung_info["rung"]
        result["next_mode"] = rung_info["mode"]
        result["next_model"] = rung_info["model"]
        result["next_max_turns"] = rung_info["max_turns"]
        result["next_thinking"] = rung_info["thinking"]
        result["escalation_reason"] = "; ".join(triggers)

        # Build handoff context
        result["handoff_context"] = {
            "previous_rung": current_rung,
            "previous_mode": mode,
            "previous_model": report.get("model", ""),
            "previous_confidence": confidence,
            "previous_branch": report.get("branch", ""),
            "escalation_reason": "; ".join(triggers),
            "failed_tests": report.get("failed_tests", []),
            "context_for_next_employee": report.get("summary", ""),
        }

        _emit_event(args.run_id, "intelligence.escalation_triggered", {
            "from_rung": current_rung,
            "to_rung": rung_info["rung"],
            "trigger_reason": "; ".join(triggers),
            "queue_item_id": args.queue_item_id,
        })
    else:
        result["reason"] = "; ".join(triggers) if triggers else "no triggers"

    print(json.dumps(result))


# ---------------------------------------------------------------------------
# Subcommand: record-outcome
# ---------------------------------------------------------------------------

def cmd_record_outcome(args: argparse.Namespace) -> None:
    """Record a task outcome for the learning loop."""
    success = args.verdict.upper() in ("APPROVE", "PR")

    payload = {
        "project_repo": args.project_repo,
        "issue_number": int(args.issue_number) if args.issue_number else None,
        "issue_type": args.issue_type or None,
        "mode_used": args.mode or "full",
        "model_used": args.model or "claude-sonnet-4-6",
        "success": success,
        "verdict": args.verdict,
        "confidence_reported": float(args.confidence) if args.confidence else None,
        "tokens_consumed": int(args.tokens) if args.tokens else None,
        "duration_seconds": int(args.duration) if args.duration else None,
        "complexity_score": int(args.complexity) if args.complexity else None,
        "escalation_rung": int(args.escalation_rung) if args.escalation_rung else 0,
        "subsystem": args.subsystem or None,
    }

    # POST to dashboard API
    try:
        import urllib.request
        req_data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{DASHBOARD_API}/api/intelligence/outcomes",
            data=req_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        logger.warning("Failed to record outcome: %s", e)

    _emit_event(args.run_id, "intelligence.outcome_recorded", {
        "verdict": args.verdict,
        "confidence": args.confidence,
        "mode": args.mode,
        "tokens": args.tokens,
        "success": success,
    })

    print(json.dumps({"recorded": True, **payload}))


# ---------------------------------------------------------------------------
# Subcommand: should-verify
# ---------------------------------------------------------------------------

def cmd_should_verify(args: argparse.Namespace) -> None:
    """Check if independent verification is warranted."""
    report = _load_json(args.report_file)
    config = _load_json(args.config) if args.config else {}

    verification_enabled = _config_get(config, "intelligence.independent_verification", False)
    if not verification_enabled:
        print(json.dumps({"should_verify": False, "reason": "independent_verification disabled"}))
        return

    # Risk criteria
    files_changed = len(report.get("files_changed", []))
    complexity = int(report.get("complexity_score", 0))
    escalation_rung = int(report.get("escalation_rung", 0))
    is_auto_pr = report.get("auto_pr", False)

    should_verify = (
        files_changed > 5
        or escalation_rung > 0
        or complexity >= 4
        or is_auto_pr
    )

    reasons = []
    if files_changed > 5:
        reasons.append(f"cross-cutting: {files_changed} files changed")
    if escalation_rung > 0:
        reasons.append(f"escalated item (rung {escalation_rung})")
    if complexity >= 4:
        reasons.append(f"high complexity ({complexity})")
    if is_auto_pr:
        reasons.append("auto-PR candidate (no human in loop)")

    result = {
        "should_verify": should_verify,
        "reasons": reasons,
        "files_changed": files_changed,
        "complexity": complexity,
        "escalation_rung": escalation_rung,
    }

    print(json.dumps(result))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="decide",
        description="Intelligence decision gateway for agent orchestration",
    )
    parser.add_argument("--run-id", default=None, help="Current run ID for event correlation")
    sub = parser.add_subparsers(dest="command", required=True)

    # select-mode
    p_mode = sub.add_parser("select-mode", help="Select optimal mode/model/turns")
    p_mode.add_argument("--issue-json", required=True, help="Path to issue JSON file")
    p_mode.add_argument("--config", help="Path to config JSON file")
    p_mode.add_argument("--project-mode", default="full", help="Project default mode")
    p_mode.set_defaults(func=cmd_select_mode)

    # check-confidence
    p_conf = sub.add_parser("check-confidence", help="Check confidence gate for auto-PR")
    p_conf.add_argument("--report-file", required=True, help="Path to employee report")
    p_conf.add_argument("--config", help="Path to config JSON")
    p_conf.set_defaults(func=cmd_check_confidence)

    # check-escalation
    p_esc = sub.add_parser("check-escalation", help="Check escalation triggers")
    p_esc.add_argument("--report-file", required=True, help="Path to employee report")
    p_esc.add_argument("--config", help="Path to config JSON")
    p_esc.add_argument("--queue-item-id", help="Queue item ID")
    p_esc.set_defaults(func=cmd_check_escalation)

    # record-outcome
    p_out = sub.add_parser("record-outcome", help="Record task outcome")
    p_out.add_argument("--project-repo", required=True)
    p_out.add_argument("--issue-number", default=None)
    p_out.add_argument("--mode", default="full")
    p_out.add_argument("--model", default="claude-sonnet-4-6")
    p_out.add_argument("--verdict", required=True)
    p_out.add_argument("--confidence", default=None)
    p_out.add_argument("--tokens", default=None)
    p_out.add_argument("--duration", default=None)
    p_out.add_argument("--complexity", default=None)
    p_out.add_argument("--escalation-rung", default="0")
    p_out.add_argument("--issue-type", default=None, help="Issue type: bug, feature, chore, refactor")
    p_out.add_argument("--subsystem", default=None, help="Subsystem: frontend, backend, agent, infra, mixed")
    p_out.set_defaults(func=cmd_record_outcome)

    # should-verify
    p_ver = sub.add_parser("should-verify", help="Check if verification is needed")
    p_ver.add_argument("--report-file", required=True)
    p_ver.add_argument("--config", help="Path to config JSON")
    p_ver.set_defaults(func=cmd_should_verify)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
