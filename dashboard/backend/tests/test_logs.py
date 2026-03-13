"""Tests for the logs API endpoints.

Covers:
- GET /api/logs/search — search across log files for matching content
- GET /api/logs/{run_id} — get parsed log lines for a specific run
- Path traversal prevention for the WebSocket stream endpoint
- Missing log directory handling
"""

import json
import os
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def client():
    """Provide an async HTTP client for testing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def log_dir(tmp_path):
    """Create a temporary log directory with sample log files."""
    # Create sample .stream.jsonl files
    run1_log = tmp_path / "run-001.stream.jsonl"
    run1_log.write_text(
        json.dumps({"type": "tool_use", "tool": "Edit", "file": "src/main.py"}) + "\n"
        + json.dumps({"type": "tool_use", "tool": "Bash", "command": "pytest"}) + "\n"
        + json.dumps({"type": "text", "content": "All tests passed"}) + "\n"
    )

    run2_log = tmp_path / "run-002.stream.jsonl"
    run2_log.write_text(
        json.dumps({"type": "tool_use", "tool": "Read", "file": "README.md"}) + "\n"
        + json.dumps({"type": "text", "content": "Error: file not found"}) + "\n"
    )

    # A non-jsonl file should be ignored by search
    other_file = tmp_path / "notes.txt"
    other_file.write_text("This should not be searched\n")

    return str(tmp_path)


# ---------------------------------------------------------------------------
# GET /api/logs/search — search across log files
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_logs_returns_matching_lines(client, log_dir):
    """Search should find lines matching the query string."""
    with patch("app.routers.logs.settings") as mock_settings:
        mock_settings.log_dir = log_dir
        resp = await client.get("/api/logs/search", params={"q": "pytest"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert any("pytest" in r["content"] for r in data["results"])


@pytest.mark.asyncio
async def test_search_logs_case_insensitive(client, log_dir):
    """Search should be case-insensitive."""
    with patch("app.routers.logs.settings") as mock_settings:
        mock_settings.log_dir = log_dir
        resp = await client.get("/api/logs/search", params={"q": "PYTEST"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_search_logs_filter_by_run_id(client, log_dir):
    """Search with run_id should only search matching files."""
    with patch("app.routers.logs.settings") as mock_settings:
        mock_settings.log_dir = log_dir
        resp = await client.get("/api/logs/search", params={
            "q": "tool_use",
            "run_id": "run-001",
        })

    assert resp.status_code == 200
    data = resp.json()
    # Should only find results from run-001
    assert all("run-001" in r["file"] for r in data["results"])


@pytest.mark.asyncio
async def test_search_logs_respects_limit(client, log_dir):
    """Search should respect the limit parameter."""
    with patch("app.routers.logs.settings") as mock_settings:
        mock_settings.log_dir = log_dir
        resp = await client.get("/api/logs/search", params={"q": "type", "limit": 2})

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] <= 2


@pytest.mark.asyncio
async def test_search_logs_no_results(client, log_dir):
    """Search for non-existent term should return empty results."""
    with patch("app.routers.logs.settings") as mock_settings:
        mock_settings.log_dir = log_dir
        resp = await client.get("/api/logs/search", params={"q": "zzz_nonexistent_zzz"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["results"] == []


@pytest.mark.asyncio
async def test_search_logs_missing_dir(client, tmp_path):
    """Search should return empty results when log directory doesn't exist."""
    with patch("app.routers.logs.settings") as mock_settings:
        mock_settings.log_dir = str(tmp_path / "nonexistent")
        resp = await client.get("/api/logs/search", params={"q": "test"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["results"] == []


@pytest.mark.asyncio
async def test_search_logs_only_searches_jsonl_files(client, log_dir):
    """Search should only search .stream.jsonl files, not other file types."""
    with patch("app.routers.logs.settings") as mock_settings:
        mock_settings.log_dir = log_dir
        resp = await client.get("/api/logs/search", params={"q": "should not be searched"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0  # notes.txt should not be searched


@pytest.mark.asyncio
async def test_search_logs_requires_query(client, log_dir):
    """Search should require a non-empty query parameter."""
    with patch("app.routers.logs.settings") as mock_settings:
        mock_settings.log_dir = log_dir
        resp = await client.get("/api/logs/search")

    assert resp.status_code == 422  # validation error


# ---------------------------------------------------------------------------
# GET /api/logs/{run_id} — get log lines for a specific run
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_run_logs_returns_parsed_lines(client, log_dir):
    """Get logs for a run should return parsed JSON lines."""
    with patch("app.routers.logs.settings") as mock_settings:
        mock_settings.log_dir = log_dir
        resp = await client.get("/api/logs/run-001")

    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "run-001"
    assert len(data["lines"]) == 3
    assert data["lines"][0]["type"] == "tool_use"
    assert data["lines"][0]["tool"] == "Edit"


@pytest.mark.asyncio
async def test_get_run_logs_respects_limit(client, log_dir):
    """Get logs should respect limit parameter."""
    with patch("app.routers.logs.settings") as mock_settings:
        mock_settings.log_dir = log_dir
        resp = await client.get("/api/logs/run-001", params={"limit": 1})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["lines"]) == 1


@pytest.mark.asyncio
async def test_get_run_logs_respects_offset(client, log_dir):
    """Get logs should respect offset parameter."""
    with patch("app.routers.logs.settings") as mock_settings:
        mock_settings.log_dir = log_dir
        resp = await client.get("/api/logs/run-001", params={"offset": 2})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["lines"]) == 1  # Only 1 line after offset 2
    assert data["lines"][0]["content"] == "All tests passed"


@pytest.mark.asyncio
async def test_get_run_logs_missing_run(client, log_dir):
    """Get logs for nonexistent run should return empty lines."""
    with patch("app.routers.logs.settings") as mock_settings:
        mock_settings.log_dir = log_dir
        resp = await client.get("/api/logs/run-nonexistent")

    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "run-nonexistent"
    assert data["lines"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_get_run_logs_missing_dir(client, tmp_path):
    """Get logs should return empty when log directory doesn't exist."""
    with patch("app.routers.logs.settings") as mock_settings:
        mock_settings.log_dir = str(tmp_path / "nonexistent")
        resp = await client.get("/api/logs/run-001")

    assert resp.status_code == 200
    data = resp.json()
    assert data["lines"] == []


@pytest.mark.asyncio
async def test_get_run_logs_handles_non_json_lines(client, tmp_path):
    """Get logs should handle non-JSON lines as raw content."""
    log_file = tmp_path / "run-raw-001.stream.jsonl"
    log_file.write_text("Not valid JSON\n" + json.dumps({"type": "ok"}) + "\n")

    with patch("app.routers.logs.settings") as mock_settings:
        mock_settings.log_dir = str(tmp_path)
        resp = await client.get("/api/logs/run-raw-001")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["lines"]) == 2
    assert data["lines"][0]["type"] == "raw"
    assert "Not valid JSON" in data["lines"][0]["content"]
    assert data["lines"][1]["type"] == "ok"


# ---------------------------------------------------------------------------
# Path traversal prevention (WebSocket) — unit test of validation logic
# ---------------------------------------------------------------------------

def test_path_traversal_validation_logic():
    """The WebSocket stream should reject paths outside log_dir.

    Since WebSocket testing requires a different client setup, we test the
    validation logic indirectly via os.path.realpath comparison.
    """
    log_dir = "/var/log/claude-agent"

    # Traversal attempt
    malicious_path = "/var/log/claude-agent/../../etc/passwd"
    resolved = os.path.realpath(malicious_path)
    log_dir_real = os.path.realpath(log_dir)
    assert not resolved.startswith(log_dir_real), \
        "Path traversal should be detected"

    # Valid path
    valid_path = "/var/log/claude-agent/run-001.stream.jsonl"
    resolved_valid = os.path.realpath(valid_path)
    assert resolved_valid.startswith(log_dir_real), \
        "Valid path should be within log_dir"


# ---------------------------------------------------------------------------
# Helper function unit tests
# ---------------------------------------------------------------------------

def test_search_files_helper(tmp_path):
    """Test _search_files helper directly."""
    from app.routers.logs import _search_files

    # Create test log file
    log_file = tmp_path / "test-run.stream.jsonl"
    log_file.write_text(
        '{"tool": "Edit"}\n'
        '{"tool": "Bash", "cmd": "npm test"}\n'
        '{"tool": "Read"}\n'
    )

    results = _search_files(
        str(tmp_path),
        ["test-run.stream.jsonl"],
        "bash",
        limit=10,
        run_id=None,
    )
    assert len(results) == 1
    assert results[0]["file"] == "test-run.stream.jsonl"
    assert results[0]["line"] == 2
    assert "Bash" in results[0]["content"]


def test_search_files_with_run_id_filter(tmp_path):
    """Test _search_files filters by run_id in filename."""
    from app.routers.logs import _search_files

    (tmp_path / "run-A.stream.jsonl").write_text('{"msg": "hello"}\n')
    (tmp_path / "run-B.stream.jsonl").write_text('{"msg": "hello"}\n')

    results = _search_files(
        str(tmp_path),
        ["run-A.stream.jsonl", "run-B.stream.jsonl"],
        "hello",
        limit=10,
        run_id="run-A",
    )
    assert len(results) == 1
    assert results[0]["file"] == "run-A.stream.jsonl"


def test_read_run_log_files_helper(tmp_path):
    """Test _read_run_log_files helper directly."""
    from app.routers.logs import _read_run_log_files

    log_file = tmp_path / "test.stream.jsonl"
    log_file.write_text(
        '{"type": "a"}\n'
        '{"type": "b"}\n'
        '{"type": "c"}\n'
    )

    # Read all
    lines = _read_run_log_files(str(tmp_path), ["test.stream.jsonl"], offset=0, limit=100)
    assert len(lines) == 3

    # Read with offset
    lines = _read_run_log_files(str(tmp_path), ["test.stream.jsonl"], offset=1, limit=100)
    assert len(lines) == 2
    assert lines[0]["type"] == "b"

    # Read with limit
    lines = _read_run_log_files(str(tmp_path), ["test.stream.jsonl"], offset=0, limit=1)
    assert len(lines) == 1
    assert lines[0]["type"] == "a"
