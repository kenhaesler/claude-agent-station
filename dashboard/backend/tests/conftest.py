"""Shared test configuration: sets a temp database path before importing the app."""

import os
import tempfile

# Must set env vars BEFORE any app imports to override settings.*
_fd, _tmp_db = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["STATION_DB_PATH"] = _tmp_db

# Clear auth/secret env vars that may leak from a developer's .env file. Tests
# assume open access; an authenticated baseline would 401 every fixture-driven
# request. Explicit empty values beat the .env file via pydantic-settings'
# precedence order (os.environ > .env).
os.environ["STATION_API_KEY"] = ""
os.environ["STATION_WEBHOOK_SECRET"] = ""
os.environ["STATION_GITHUB_WEBHOOK_SECRET"] = ""

# Default test runner to the inline (subprocess) launcher path. Individual
# tests that exercise the Docker SDK route explicitly set
# STATION_RUNNER_MODE=container via monkeypatch (#386 PR-2). Without this
# default the launcher would try to talk to a real Docker daemon when a
# test calls /run.
os.environ.setdefault("STATION_RUNNER_MODE", "inline")

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.postgres_fixture import postgres_url  # noqa: F401, re-export


@pytest.fixture(scope="session", params=["sqlite", "postgres"])
def db_url(request, postgres_url, tmp_path_factory):
    if request.param == "sqlite":
        # Use a file-based URL: alembic runs in a subprocess and creates the
        # schema on disk; the in-process engine then connects to the same file.
        # sqlite:///:memory: creates an isolated per-connection database that
        # cannot be shared with a subprocess-created schema.
        # Use pytest's tmp_path_factory so the file is cleaned up automatically
        # when the test session ends, rather than accumulating in /tmp across
        # runs.
        path = tmp_path_factory.mktemp("parametrized-sqlite") / "test.db"
        return f"sqlite+aiosqlite:///{path}"
    return postgres_url


@pytest.fixture(scope="session")
def async_session_factory(db_url):
    """Per-backend session factory.

    Runs Alembic upgrade head against the chosen URL once per session.
    Tests share the schema.
    """
    import subprocess
    import sys
    from pathlib import Path

    backend_root = Path(__file__).resolve().parent.parent
    env = {
        **os.environ,
        "STATION_DB_URL": db_url,
        "PYTHONPATH": str(backend_root),
    }
    subprocess.check_call(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(backend_root),
        env=env,
    )
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
