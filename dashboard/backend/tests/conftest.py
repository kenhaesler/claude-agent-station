"""Shared test configuration: sets a temp database path before importing the app."""

import os
import tempfile

# Must set env var BEFORE any app imports to override settings.db_path
_tmp_db = tempfile.mktemp(suffix=".db")
os.environ["STATION_DB_PATH"] = _tmp_db
