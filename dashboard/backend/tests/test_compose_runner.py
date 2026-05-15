"""compose.yml shape for per-runner containers (#386)."""
from __future__ import annotations

from pathlib import Path

import yaml


def _compose():
    return yaml.safe_load((Path(__file__).resolve().parents[3] / "compose.yml").read_text())


def test_agent_service_mounts_docker_sock():
    c = _compose()
    agent = c["services"]["agent"]
    sock_mounts = [v for v in agent.get("volumes", []) if "docker.sock" in v]
    assert sock_mounts, "agent must mount /var/run/docker.sock"


def test_agent_net_network_declared():
    c = _compose()
    assert "agent-net" in c.get("networks", {})


def test_agent_net_has_explicit_name_to_avoid_project_prefix():
    """Compose prefixes network names with the project slug by default
    (``claude-agent-station_agent-net``), but ``agent/runner_spawn.py``
    joins runner containers by the bare name ``agent-net``. The two
    must match or every triggered run fails with ``failed to set up
    container networking: network agent-net not found``. Pin the
    ``name: agent-net`` override.
    """
    c = _compose()
    agent_net = c["networks"]["agent-net"]
    assert isinstance(agent_net, dict), "agent-net must be a mapping, not the implicit form"
    assert agent_net.get("name") == "agent-net", (
        "compose.yml must set `name: agent-net` on the agent-net network so "
        "compose doesn't prepend the project prefix; runner_spawn.py joins by "
        "the bare name"
    )


def test_agent_runner_mode_env_present():
    c = _compose()
    env = c["services"]["agent"]["environment"]
    assert env.get("STATION_RUNNER_MODE") in ("container", "inline")


def test_dashboard_on_agent_net():
    c = _compose()
    nets = c["services"]["dashboard"].get("networks") or []
    assert "agent-net" in nets


def test_operations_doc_has_runner_section():
    doc = (Path(__file__).resolve().parents[3] / "docs/operations.md").read_text()
    assert "## Inspecting a live runner" in doc
    assert "STATION_RUNNER_MODE" in doc
    assert "docker exec" in doc


def test_architecture_doc_mentions_per_run_container():
    doc = (Path(__file__).resolve().parents[3] / "docs/architecture.md").read_text()
    assert "cas-runner" in doc
    assert "cas-launcher" in doc or "per-run container" in doc
