"""compose.yml exposes a `db` service with healthcheck (#393)."""
from __future__ import annotations

from pathlib import Path

import yaml


def _compose():
    return yaml.safe_load((Path(__file__).resolve().parents[3] / "compose.yml").read_text())


def test_db_service_present():
    c = _compose()
    assert "db" in c["services"]
    assert c["services"]["db"]["image"].startswith("postgres:")


def test_db_healthcheck_present():
    db = _compose()["services"]["db"]
    assert "healthcheck" in db
    assert "pg_isready" in " ".join(db["healthcheck"]["test"])


def test_dashboard_depends_on_db_healthy():
    dash = _compose()["services"]["dashboard"]
    assert dash["depends_on"]["db"]["condition"] == "service_healthy"


def test_agent_depends_on_db_healthy():
    agent = _compose()["services"]["agent"]
    assert agent["depends_on"]["db"]["condition"] == "service_healthy"


def test_db_password_secret_declared():
    c = _compose()
    assert "db_password" in c.get("secrets", {})
