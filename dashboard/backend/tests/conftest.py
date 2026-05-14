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
