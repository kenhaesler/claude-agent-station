"""Tests for the log parser service.

Covers:
- Run ID extraction from filenames
- Repo name extraction from filenames
- Timestamp parsing
- Result JSON parsing
- Stream JSONL result extraction
- File discovery and grouping
"""

import json
import os
import tempfile

from app.services.log_parser import (
    parse_run_id_from_filename,
    parse_repo_from_filename,
    parse_run_timestamp,
    parse_result_json,
    parse_stream_result,
    discover_run_files,
)


class TestParseRunIdFromFilename:
    """Extract run ID timestamps from various filename formats."""

    def test_employee_stream_file(self):
        result = parse_run_id_from_filename(
            "run-20260308T130028Z-employee-ai-portainer-dashboard.stream.jsonl"
        )
        assert result == "20260308T130028Z"

    def test_old_format_stream(self):
        result = parse_run_id_from_filename(
            "run-20260307T203444Z-github-issues.stream.jsonl"
        )
        assert result == "20260307T203444Z"

    def test_stderr_log(self):
        result = parse_run_id_from_filename(
            "run-20260308T130028Z-employee-my-repo.stderr.log"
        )
        assert result == "20260308T130028Z"

    def test_result_json(self):
        result = parse_run_id_from_filename(
            "run-20260308T130028Z-employee-my-repo.json"
        )
        assert result == "20260308T130028Z"

    def test_verdicts_json(self):
        result = parse_run_id_from_filename(
            "run-20260308T130028Z-verdicts.json"
        )
        assert result == "20260308T130028Z"

    def test_no_match(self):
        assert parse_run_id_from_filename("random-file.txt") is None

    def test_empty_string(self):
        assert parse_run_id_from_filename("") is None


class TestParseRepoFromFilename:
    """Extract repository name from employee log filenames."""

    def test_employee_stream(self):
        result = parse_repo_from_filename(
            "run-20260308T130028Z-employee-ai-portainer-dashboard.stream.jsonl"
        )
        assert result == "ai-portainer-dashboard"

    def test_employee_stderr(self):
        result = parse_repo_from_filename(
            "run-20260308T130028Z-employee-my-repo.stderr.log"
        )
        assert result == "my-repo"

    def test_old_format_fallback(self):
        result = parse_repo_from_filename(
            "run-20260307T203444Z-github-issues.stream.jsonl"
        )
        assert result == "github-issues"

    def test_no_match(self):
        assert parse_repo_from_filename("random.txt") is None

    def test_empty(self):
        assert parse_repo_from_filename("") is None


class TestParseRunTimestamp:
    """Parse run ID strings into datetime objects."""

    def test_valid_timestamp(self):
        dt = parse_run_timestamp("20260308T130028Z")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 8
        assert dt.hour == 13
        assert dt.minute == 0
        assert dt.second == 28

    def test_invalid_timestamp(self):
        assert parse_run_timestamp("not-a-timestamp") is None

    def test_empty_string(self):
        assert parse_run_timestamp("") is None


class TestParseResultJson:
    """Parse run result .json files."""

    def test_valid_result(self):
        data = {
            "type": "result",
            "subtype": "success",
            "total_cost_usd": 0.15,
            "num_turns": 42,
            "duration_ms": 120000,
            "modelUsage": {
                "claude-sonnet-4-20250514": {
                    "inputTokens": 50000,
                    "outputTokens": 10000,
                }
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = f.name

        try:
            result = parse_result_json(path)
            assert result is not None
            assert result["status"] == "success"
            assert result["cost_usd"] == 0.15
            assert result["turns"] == 42
            assert result["duration_ms"] == 120000
            assert result["tokens_input"] == 50000
            assert result["tokens_output"] == 10000
            assert result["tokens_total"] == 60000
            assert result["model"] == "claude-sonnet-4-20250514"
        finally:
            os.unlink(path)

    def test_failed_result(self):
        data = {
            "type": "result",
            "subtype": "error",
            "total_cost_usd": 0.02,
            "num_turns": 3,
            "duration_ms": 5000,
            "modelUsage": {},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = f.name

        try:
            result = parse_result_json(path)
            assert result is not None
            assert result["status"] == "failed"
            assert result["tokens_input"] is None
        finally:
            os.unlink(path)

    def test_non_result_type_returns_none(self):
        data = {"type": "message", "content": "hello"}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = f.name

        try:
            assert parse_result_json(path) is None
        finally:
            os.unlink(path)

    def test_invalid_json_returns_none(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not valid json{{{")
            path = f.name

        try:
            assert parse_result_json(path) is None
        finally:
            os.unlink(path)

    def test_nonexistent_file_returns_none(self):
        assert parse_result_json("/tmp/nonexistent_file_12345.json") is None

    def test_multi_model_usage(self):
        """Token counts should be summed across multiple models."""
        data = {
            "type": "result",
            "subtype": "success",
            "num_turns": 10,
            "modelUsage": {
                "claude-sonnet-4-20250514": {
                    "inputTokens": 30000,
                    "outputTokens": 5000,
                },
                "claude-haiku-3-20240307": {
                    "inputTokens": 10000,
                    "outputTokens": 2000,
                },
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = f.name

        try:
            result = parse_result_json(path)
            assert result["tokens_input"] == 40000
            assert result["tokens_output"] == 7000
            assert result["tokens_total"] == 47000
        finally:
            os.unlink(path)


class TestParseStreamResult:
    """Parse stream JSONL files for the result event."""

    def test_valid_stream_with_result(self):
        lines = [
            json.dumps({"type": "message", "content": "working..."}),
            json.dumps({"type": "tool_use", "name": "Bash"}),
            json.dumps({
                "type": "result",
                "subtype": "success",
                "total_cost_usd": 0.25,
                "num_turns": 15,
                "duration_ms": 60000,
                "modelUsage": {
                    "claude-sonnet-4-20250514": {
                        "inputTokens": 40000,
                        "outputTokens": 8000,
                    }
                },
            }),
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for line in lines:
                f.write(line + "\n")
            path = f.name

        try:
            result = parse_stream_result(path)
            assert result is not None
            assert result["status"] == "success"
            assert result["turns"] == 15
            assert result["tokens_total"] == 48000
        finally:
            os.unlink(path)

    def test_stream_without_result(self):
        lines = [
            json.dumps({"type": "message", "content": "working..."}),
            json.dumps({"type": "tool_use", "name": "Bash"}),
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for line in lines:
                f.write(line + "\n")
            path = f.name

        try:
            assert parse_stream_result(path) is None
        finally:
            os.unlink(path)

    def test_stream_with_invalid_json_lines(self):
        """Invalid JSON lines should be skipped, not crash."""
        lines = [
            "not valid json",
            json.dumps({
                "type": "result",
                "subtype": "success",
                "num_turns": 5,
                "modelUsage": {},
            }),
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for line in lines:
                f.write(line + "\n")
            path = f.name

        try:
            result = parse_stream_result(path)
            assert result is not None
            assert result["turns"] == 5
        finally:
            os.unlink(path)


class TestDiscoverRunFiles:
    """Discover and group log files by run ID."""

    def test_groups_files_by_run_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test files for two runs
            files = [
                "run-20260308T130028Z-employee-my-repo.stream.jsonl",
                "run-20260308T130028Z-employee-my-repo.stderr.log",
                "run-20260308T130028Z-verdicts.json",
                "run-20260309T100000Z-employee-other-repo.stream.jsonl",
                "unrelated-file.txt",
            ]
            for fname in files:
                open(os.path.join(tmpdir, fname), "w").close()

            result = discover_run_files(tmpdir)

            assert "20260308T130028Z" in result
            assert "20260309T100000Z" in result
            assert len(result) == 2

            run1 = result["20260308T130028Z"]
            assert len(run1["streams"]) == 1
            assert len(run1["stderr"]) == 1
            assert len(run1["verdicts"]) == 1

            run2 = result["20260309T100000Z"]
            assert len(run2["streams"]) == 1

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = discover_run_files(tmpdir)
            assert result == {}

    def test_nonexistent_directory(self):
        result = discover_run_files("/tmp/nonexistent_dir_12345")
        assert result == {}

    def test_json_results_separated_from_verdicts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            files = [
                "run-20260308T130028Z-employee-repo.json",
                "run-20260308T130028Z-verdicts.json",
            ]
            for fname in files:
                open(os.path.join(tmpdir, fname), "w").close()

            result = discover_run_files(tmpdir)
            run = result["20260308T130028Z"]
            assert len(run["results"]) == 1
            assert len(run["verdicts"]) == 1
