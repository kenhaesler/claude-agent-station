"""Tests for the config sync service.

Covers:
- sync_config_to_db with valid config
- sync_config_to_db with missing file (empty config)
- sync_db_to_config writes JSON correctly
- _write_config_json atomic write (temp file -> os.replace)
- _read_config_json with missing file
"""

import json
from unittest.mock import patch

import pytest
import pytest_asyncio

from app.database import Base, async_session, engine
from app.models import Project
from app.services.config_sync import (
    _read_config_json,
    _write_config_json,
    sync_config_to_db,
    sync_db_to_config,
)


@pytest_asyncio.fixture
async def setup_db():
    """Create tables and provide a clean database for each test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ---------------------------------------------------------------------------
# _read_config_json
# ---------------------------------------------------------------------------

def test_read_config_json_missing_file(tmp_path):
    """_read_config_json should return empty projects when file doesn't exist."""
    with patch("app.services.config_sync.settings") as mock_settings:
        mock_settings.config_path = str(tmp_path / "nonexistent.json")
        result = _read_config_json()
    assert result == {"projects": []}


def test_read_config_json_valid_file(tmp_path):
    """_read_config_json should read and parse the JSON file."""
    config_file = tmp_path / "config.json"
    config_data = {
        "projects": [{"repo": "owner/repo", "priority": "high"}],
        "models": {"employee": "claude-sonnet"},
    }
    config_file.write_text(json.dumps(config_data))

    with patch("app.services.config_sync.settings") as mock_settings:
        mock_settings.config_path = str(config_file)
        result = _read_config_json()
    assert result["projects"][0]["repo"] == "owner/repo"
    assert result["models"]["employee"] == "claude-sonnet"


# ---------------------------------------------------------------------------
# _write_config_json — atomic write
# ---------------------------------------------------------------------------

def test_write_config_json_creates_file(tmp_path):
    """_write_config_json should create the config file."""
    config_file = tmp_path / "config.json"
    config_data = {"projects": [{"repo": "owner/test"}], "models": {}}

    with patch("app.services.config_sync.settings") as mock_settings:
        mock_settings.config_path = str(config_file)
        _write_config_json(config_data)

    assert config_file.exists()
    written = json.loads(config_file.read_text())
    assert written["projects"][0]["repo"] == "owner/test"


def test_write_config_json_overwrites_existing(tmp_path):
    """_write_config_json should overwrite existing content."""
    config_file = tmp_path / "config.json"
    config_file.write_text('{"old": true}')

    with patch("app.services.config_sync.settings") as mock_settings:
        mock_settings.config_path = str(config_file)
        _write_config_json({"new": True})

    written = json.loads(config_file.read_text())
    assert "old" not in written
    assert written["new"] is True


def test_write_config_json_atomic_no_partial(tmp_path):
    """_write_config_json should not leave partial files on error."""
    config_file = tmp_path / "config.json"
    config_file.write_text('{"original": true}')

    # Simulate os.replace failure
    with patch("app.services.config_sync.settings") as mock_settings:
        mock_settings.config_path = str(config_file)
        with patch("os.replace", side_effect=OSError("disk full")), pytest.raises(OSError):
            _write_config_json({"should_not_persist": True})

    # Original file should be unchanged
    written = json.loads(config_file.read_text())
    assert written["original"] is True


def test_write_config_json_ends_with_newline(tmp_path):
    """_write_config_json should write a trailing newline."""
    config_file = tmp_path / "config.json"

    with patch("app.services.config_sync.settings") as mock_settings:
        mock_settings.config_path = str(config_file)
        _write_config_json({"test": True})

    content = config_file.read_text()
    assert content.endswith("\n")


# ---------------------------------------------------------------------------
# sync_config_to_db — JSON -> DB
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_config_to_db_creates_projects(setup_db):
    """sync_config_to_db should create Project records from JSON config."""
    config = {
        "projects": [
            {"repo": "owner/repo-a", "priority": "high", "mode": "full", "branch": "main"},
            {"repo": "owner/repo-b", "priority": "low", "mode": "employee-only"},
        ]
    }
    with patch("app.services.config_sync._read_config_json", return_value=config):
        async with async_session() as session:
            count = await sync_config_to_db(session)

    assert count == 2

    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(select(Project))
        projects = result.scalars().all()
        repos = {p.repo for p in projects}
        assert "owner/repo-a" in repos
        assert "owner/repo-b" in repos


@pytest.mark.asyncio
async def test_sync_config_to_db_updates_existing(setup_db):
    """sync_config_to_db should update existing projects instead of duplicating."""
    # Create initial project
    async with async_session() as session:
        project = Project(repo="owner/repo-x", priority="medium", mode="full", enabled=True, branch="main")
        session.add(project)
        await session.commit()

    # Sync with updated priority
    config = {
        "projects": [
            {"repo": "owner/repo-x", "priority": "high", "mode": "employee-only"},
        ]
    }
    with patch("app.services.config_sync._read_config_json", return_value=config):
        async with async_session() as session:
            count = await sync_config_to_db(session)

    assert count == 1

    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(select(Project).where(Project.repo == "owner/repo-x"))
        project = result.scalar_one()
        assert project.priority == "high"
        assert project.mode == "employee-only"


@pytest.mark.asyncio
async def test_sync_config_to_db_missing_file(setup_db):
    """sync_config_to_db should handle missing config file (empty projects)."""
    with patch("app.services.config_sync._read_config_json", return_value={"projects": []}):
        async with async_session() as session:
            count = await sync_config_to_db(session)
    assert count == 0


@pytest.mark.asyncio
async def test_sync_config_to_db_skips_entries_without_repo(setup_db):
    """sync_config_to_db should skip project entries that have no repo field."""
    config = {
        "projects": [
            {"priority": "high"},  # missing repo
            {"repo": "owner/valid", "priority": "low"},
        ]
    }
    with patch("app.services.config_sync._read_config_json", return_value=config):
        async with async_session() as session:
            count = await sync_config_to_db(session)
    # Only the valid entry counted
    assert count == 1


# ---------------------------------------------------------------------------
# sync_db_to_config — DB -> JSON
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_db_to_config(setup_db, tmp_path):
    """sync_db_to_config should write current DB projects to JSON."""
    # Create projects in DB
    async with async_session() as session:
        session.add(Project(repo="owner/alpha", priority="high", mode="full", enabled=True, branch="main"))
        session.add(Project(repo="owner/beta", priority="low", mode="employee-only", enabled=False, branch="dev"))
        await session.commit()

    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"projects": [], "models": {"employee": "claude"}}))

    with patch("app.services.config_sync.settings") as mock_settings:
        mock_settings.config_path = str(config_file)
        async with async_session() as session:
            await sync_db_to_config(session)

    written = json.loads(config_file.read_text())
    repos = [p["repo"] for p in written["projects"]]
    assert "owner/alpha" in repos
    assert "owner/beta" in repos
    # Non-project config should be preserved
    assert written["models"]["employee"] == "claude"


@pytest.mark.asyncio
async def test_sync_db_to_config_includes_disabled(setup_db, tmp_path):
    """sync_db_to_config should include disabled projects."""
    async with async_session() as session:
        session.add(Project(repo="owner/disabled", priority="medium", mode="full", enabled=False, branch="main"))
        await session.commit()

    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"projects": []}))

    with patch("app.services.config_sync.settings") as mock_settings:
        mock_settings.config_path = str(config_file)
        async with async_session() as session:
            await sync_db_to_config(session)

    written = json.loads(config_file.read_text())
    assert len(written["projects"]) == 1
    assert written["projects"][0]["repo"] == "owner/disabled"
    assert written["projects"][0].get("enabled") is False


# ---------------------------------------------------------------------------
# promotion_target round-trip (PR #341)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_config_to_db_reads_promotion_target(setup_db):
    """JSON promotion_target is persisted to the DB column."""
    config = {
        "projects": [
            {"repo": "owner/with-target", "branch": "main", "promotion_target": "dev"},
        ]
    }
    with patch("app.services.config_sync._read_config_json", return_value=config):
        async with async_session() as session:
            await sync_config_to_db(session)

    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(select(Project).where(Project.repo == "owner/with-target"))
        project = result.scalar_one()
        assert project.promotion_target == "dev"


@pytest.mark.asyncio
async def test_sync_config_to_db_missing_promotion_target_is_null(setup_db):
    """When JSON omits promotion_target, the DB column is NULL (fall back to branch)."""
    config = {"projects": [{"repo": "owner/no-target", "branch": "main"}]}
    with patch("app.services.config_sync._read_config_json", return_value=config):
        async with async_session() as session:
            await sync_config_to_db(session)

    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(select(Project).where(Project.repo == "owner/no-target"))
        project = result.scalar_one()
        assert project.promotion_target is None


@pytest.mark.asyncio
async def test_sync_db_to_config_writes_promotion_target(setup_db, tmp_path):
    """sync_db_to_config emits promotion_target only when set."""
    async with async_session() as session:
        session.add(Project(
            repo="owner/with-target", priority="medium", mode="full",
            enabled=True, branch="main", promotion_target="dev",
        ))
        session.add(Project(
            repo="owner/no-target", priority="medium", mode="full",
            enabled=True, branch="main", promotion_target=None,
        ))
        await session.commit()

    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"projects": []}))

    with patch("app.services.config_sync.settings") as mock_settings:
        mock_settings.config_path = str(config_file)
        async with async_session() as session:
            await sync_db_to_config(session)

    written = json.loads(config_file.read_text())
    by_repo = {p["repo"]: p for p in written["projects"]}

    assert by_repo["owner/with-target"]["promotion_target"] == "dev"
    # Unset target is omitted entirely (consistent with branch/custom_instructions)
    assert "promotion_target" not in by_repo["owner/no-target"]


@pytest.mark.asyncio
async def test_promotion_target_round_trip(setup_db, tmp_path):
    """A full JSON -> DB -> JSON cycle preserves promotion_target."""
    starting = {
        "projects": [
            {"repo": "owner/round-trip", "branch": "main", "promotion_target": "dev"},
        ]
    }
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(starting))

    with patch("app.services.config_sync.settings") as mock_settings, \
         patch("app.services.config_sync._read_config_json", return_value=starting):
        mock_settings.config_path = str(config_file)
        async with async_session() as session:
            await sync_config_to_db(session)
            await sync_db_to_config(session)

    rewritten = json.loads(config_file.read_text())
    assert rewritten["projects"][0]["promotion_target"] == "dev"


@pytest.mark.asyncio
async def test_sync_config_to_db_updates_promotion_target(setup_db):
    """Re-syncing JSON with a new promotion_target updates the existing row."""
    async with async_session() as session:
        session.add(Project(
            repo="owner/update-target", priority="medium", mode="full",
            enabled=True, branch="main", promotion_target="dev",
        ))
        await session.commit()

    # Re-sync with a different target
    config = {
        "projects": [
            {"repo": "owner/update-target", "branch": "main", "promotion_target": "staging"},
        ]
    }
    with patch("app.services.config_sync._read_config_json", return_value=config):
        async with async_session() as session:
            await sync_config_to_db(session)

    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(select(Project).where(Project.repo == "owner/update-target"))
        project = result.scalar_one()
        assert project.promotion_target == "staging"
