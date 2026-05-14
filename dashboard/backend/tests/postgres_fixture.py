"""Optional ephemeral Postgres for parametrized tests (#393).

Skipped when Docker isn't available. Tests opting into postgres
parametrization request the ``postgres_url`` fixture.
"""
from __future__ import annotations

import os
import socket
import subprocess
import time
import uuid
from contextlib import contextmanager

import pytest


def _docker_available() -> bool:
    try:
        return subprocess.run(["docker", "ps"], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


@contextmanager
def _ephemeral_postgres():
    name = f"cas-pg-test-{uuid.uuid4().hex[:8]}"
    port = _find_free_port()
    subprocess.check_call([
        "docker", "run", "-d", "--rm", "--name", name,
        "-e", "POSTGRES_PASSWORD=test",
        "-e", "POSTGRES_USER=test",
        "-e", "POSTGRES_DB=test",
        "-p", f"{port}:5432",
        "postgres:16-alpine",
    ])
    try:
        url = f"postgresql+asyncpg://test:test@127.0.0.1:{port}/test"
        # Wait for readiness (max 30 s).
        deadline = time.time() + 30
        while time.time() < deadline:
            ready = subprocess.run(
                ["docker", "exec", name, "pg_isready", "-U", "test"],
                capture_output=True,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("postgres test container never became ready")
        yield url
    finally:
        subprocess.run(["docker", "kill", name], capture_output=True)


def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def postgres_url():
    if not _docker_available():
        pytest.skip("docker not available; skip postgres-parametrized run")
    with _ephemeral_postgres() as url:
        yield url
