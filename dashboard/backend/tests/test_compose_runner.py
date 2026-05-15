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


def test_db_service_is_on_agent_net():
    """Runner containers join only ``agent-net``; if ``db`` is not also
    on that network, every runner aborts the first time it dials
    Postgres with ``socket.gaierror: Name or service not known``.
    See #386 follow-up.
    """
    c = _compose()
    nets = c["services"]["db"].get("networks") or []
    assert "agent-net" in nets, (
        "db service must join agent-net so runner containers can reach it"
    )


def test_station_volumes_have_explicit_name_to_avoid_project_prefix():
    """Same project-prefix gotcha as ``agent-net``: ``runner_spawn.py``
    mounts ``station-data`` / ``station-logs`` by bare name, but compose
    defaults to ``claude-agent-station_station-data``. Without the
    ``name:`` override the runner gets EMPTY auto-created volumes and
    aborts in preflight ("manager-config.json: No such file"). See
    follow-up to #386.
    """
    c = _compose()
    for vol_name in ("station-data", "station-logs"):
        vol = c["volumes"][vol_name]
        assert isinstance(vol, dict), (
            f"{vol_name} must be a mapping with an explicit ``name:`` override"
        )
        assert vol.get("name") == vol_name, (
            f"compose.yml must set ``name: {vol_name}`` so the bare name "
            f"matches what runner_spawn.py mounts"
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
