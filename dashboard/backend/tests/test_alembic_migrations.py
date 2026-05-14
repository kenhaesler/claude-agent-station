"""Alembic baseline tests (#393).

NOTE: The plan originally targeted test_migrations.py, but that file already
existed (testing _migrate_add_columns). This file uses the name
test_alembic_migrations.py to avoid collision. See PR-1 drift notes.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def test_alembic_config_exists():
    assert (Path(__file__).parent.parent / "alembic.ini").exists()


def test_alembic_history_runs():
    proc = subprocess.run(
        ["alembic", "history"],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
