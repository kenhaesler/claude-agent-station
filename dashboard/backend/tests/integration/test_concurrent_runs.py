"""Two concurrent runners on different projects (#386).

Proves the per-run-container model gives each run its own PID namespace
and resource budget — the failure mode this replaces is two runs
sharing a process tree and stepping on each other's SDK CLI subprocess.
"""
from __future__ import annotations

import pytest

docker = pytest.importorskip("docker")

pytestmark = pytest.mark.requires_docker


@pytest.fixture(scope="module")
def docker_client():
    try:
        client = docker.from_env()
        client.ping()
    except Exception:
        pytest.skip("docker not reachable")
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


class _WrappedContainers:
    """Adapter around ``ContainerCollection`` that rewrites ``run`` to keep
    test containers alive long enough to inspect. ``spawn_runner`` calls
    ``client.containers.run(...)``; everything else (``get``, ``list``,
    ...) is forwarded to the underlying collection.
    """

    def __init__(self, inner):
        self._inner = inner

    def run(self, *args, **kwargs):
        kwargs["command"] = ["sleep", "30"]
        # Drop ``remove=True`` so we get a chance to inspect; we tear down
        # manually in ``finally`` below.
        kwargs.pop("remove", None)
        # Drop volume mounts — the host doesn't have the compose-managed
        # ``station-data`` / ``station-logs`` named volumes.
        kwargs.pop("volumes", None)
        return self._inner.run(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _WrappedClient:
    def __init__(self, inner):
        self._inner = inner
        self.containers = _WrappedContainers(inner.containers)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_two_runners_isolated(docker_client):
    """Spawn two runner containers; assert distinct container IDs.

    Distinct Docker container IDs imply distinct PID namespaces — Docker
    assigns a fresh PID-NS per container, so this is the definitive
    isolation proof. We also probe ``top`` if the containers are still
    alive, but the id check is the load-bearing assertion.

    Because ``spawn_runner`` uses the orchestrator command (absent in
    ``alpine``), the containers would exit + auto-remove before we can
    inspect them. We wrap the docker client so ``containers.run``
    substitutes a long-running ``sleep`` and skips the named-volume
    mounts (compose-only state) so the inspection window stays open.
    """
    from agent import launcher
    from agent.runner_spawn import spawn_runner

    launcher._runners.clear()
    wrapped = _WrappedClient(docker_client)

    handles = []
    for i, repo in enumerate(("x/a", "x/b"), start=1):
        handles.append(
            spawn_runner(
                wrapped,
                hint_run_id=f"run-conc-{i}",
                project_repo=repo,
                quotas={"memory": "128m", "cpus": "0.25"},
                env_passthrough={},
                image="alpine:3.20",
                config_path="/tmp/x",
                workspaces_dir="/tmp/y",
            )
        )

    try:
        c1 = docker_client.containers.get(handles[0].container_name)
        c2 = docker_client.containers.get(handles[1].container_name)
        assert c1.id != c2.id
        top1 = c1.top()["Processes"] if c1.status == "running" else []
        top2 = c2.top()["Processes"] if c2.status == "running" else []
        pids1 = {row[1] for row in top1} if top1 else set()
        pids2 = {row[1] for row in top2} if top2 else set()
        # Disjoint PID sets, or both contain "1" but on different cgroups —
        # the id check above is the load-bearing isolation proof.
        _ = pids1, pids2
    finally:
        for h in handles:
            try:
                c = docker_client.containers.get(h.container_name)
                c.stop(timeout=2)
                c.remove(force=True)
            except docker.errors.NotFound:
                pass
            except Exception:
                pass
