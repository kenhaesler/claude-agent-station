"""Regression tests for build_team_prompt webhook URL resolution.

Per the project-vision PR review fix #2: the misalignment-webhook curl
command in the lead's prompt must NOT hardcode http://dashboard:8420
(only resolves on the compose network — silently breaks Hook 2 on
systemd-mode deployments). The URL must follow the same precedence as
post_webhook(): env > config > localhost default.
"""

import os
import pytest
from unittest.mock import patch
from agent.station_orchestrator import build_team_prompt


VISION = {
    "problem": "P", "users": "U", "end_state": "E", "non_goals": "N",
    "principles": "Pr", "horizons": "H", "anti_patterns": "A",
}


def _prompt_with(config: dict, env: dict | None = None) -> str:
    issues = [{"number": 1, "title": "t", "body": "", "labels": []}]
    if env is not None:
        with patch.dict(os.environ, env, clear=False):
            return build_team_prompt(
                "o/r", issues, config, run_id="r", workspace="/w",
                worktree_paths={}, vision=VISION,
            )
    return build_team_prompt(
        "o/r", issues, config, run_id="r", workspace="/w",
        worktree_paths={}, vision=VISION,
    )


def test_webhook_url_uses_station_webhook_url_env_when_set():
    """STATION_WEBHOOK_URL env wins (compose-mode path)."""
    prompt = _prompt_with(
        config={},
        env={"STATION_WEBHOOK_URL": "http://dashboard:8420/api/webhook/run-event"},
    )
    assert "curl -s -X POST http://dashboard:8420/api/webhook/run-event" in prompt


def test_webhook_url_falls_through_to_config_when_env_unset():
    """When env unset, config dashboard.webhook_url wins."""
    # Ensure env var is unset for this test
    env = {k: v for k, v in os.environ.items() if k != "STATION_WEBHOOK_URL"}
    with patch.dict(os.environ, env, clear=True):
        prompt = build_team_prompt(
            "o/r", [{"number": 1, "title": "t", "body": "", "labels": []}],
            config={"dashboard": {"webhook_url": "http://my-host:8420/api/webhook/run-event"}},
            run_id="r", workspace="/w", worktree_paths={}, vision=VISION,
        )
    assert "curl -s -X POST http://my-host:8420/api/webhook/run-event" in prompt


def test_webhook_url_defaults_to_localhost_when_unconfigured():
    """When neither env nor config is set, default to 127.0.0.1 (systemd default)."""
    env = {k: v for k, v in os.environ.items() if k != "STATION_WEBHOOK_URL"}
    with patch.dict(os.environ, env, clear=True):
        prompt = build_team_prompt(
            "o/r", [{"number": 1, "title": "t", "body": "", "labels": []}],
            config={}, run_id="r", workspace="/w", worktree_paths={}, vision=VISION,
        )
    assert "curl -s -X POST http://127.0.0.1:8420/api/webhook/run-event" in prompt
    # Importantly, the broken hardcoded value is NOT present
    assert "http://dashboard:8420" not in prompt


def test_webhook_url_omitted_when_no_vision():
    """No vision = no Vision-check section = no curl line. Sanity check."""
    prompt = build_team_prompt(
        "o/r", [{"number": 1, "title": "t", "body": "", "labels": []}],
        config={}, run_id="r", workspace="/w", worktree_paths={}, vision=None,
    )
    assert "vision_misalignment" not in prompt
    assert "Vision check" not in prompt
