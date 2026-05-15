"""Real Docker spawn of a runner container (#386).

Marked ``@pytest.mark.requires_docker``; skipped if the docker daemon is
unreachable (e.g. on CI without docker-in-docker). The goal is to prove
end-to-end that ``spawn_runner`` produces a real container that obeys the
``--rm`` semantics we rely on for cleanup.
"""
from __future__ import annotations

import time

import pytest

docker = pytest.importorskip("docker")

pytestmark = pytest.mark.requires_docker


def _docker_available() -> bool:
    try:
        return bool(docker.from_env().ping())
    except Exception:
        return False


@pytest.fixture(scope="module")
def docker_client():
    if not _docker_available():
        pytest.skip("docker daemon not reachable")
    client = docker.from_env()
    # ``spawn_runner`` pins the runner to the ``agent-net`` network (compose
    # creates this on ``up``). When running these tests outside the compose
    # stack we need to materialise it ourselves, and remove it on teardown
    # so we don't leak a network into the host's docker state.
    created = False
    try:
        client.networks.get("agent-net")
    except docker.errors.NotFound:
        client.networks.create("agent-net", driver="bridge")
        created = True
    yield client
    if created:
        try:
            client.networks.get("agent-net").remove()
        except Exception:
            pass


def test_runner_spawn_and_auto_remove(docker_client):
    """Spawn a runner; assert auto-cleanup via Docker's ``--rm`` semantics.

    We override the runner image to ``alpine:3.20`` and let
    ``spawn_runner``'s default command miss — alpine has no
    ``agent.station_orchestrator`` module, so the container exits almost
    immediately. The relevant invariant is that ``remove=True`` was set,
    so within a few seconds the container disappears from the daemon's
    state. That confirms the runner-spawn path won't leak containers.
    """
    from agent.runner_spawn import spawn_runner

    handle = spawn_runner(
        docker_client,
        hint_run_id="run-integ-1",
        project_repo=None,
        quotas={"memory": "256m", "cpus": "0.5"},
        env_passthrough={},
        image="alpine:3.20",
        config_path="/tmp/x",
        workspaces_dir="/tmp/y",
    )

    deadline = time.time() + 30
    gone = False
    while time.time() < deadline:
        try:
            container = docker_client.containers.get(handle.container_name)
            container.reload()
            if container.status in ("exited", "removing"):
                # Give --rm a moment to finish the cleanup.
                time.sleep(1.0)
                continue
        except docker.errors.NotFound:
            gone = True
            break
        time.sleep(0.5)

    if not gone:
        # Belt-and-braces cleanup so a failure here doesn't leak.
        try:
            docker_client.containers.get(handle.container_name).remove(force=True)
        except docker.errors.NotFound:
            pass

    with pytest.raises(docker.errors.NotFound):
        docker_client.containers.get(handle.container_name)
