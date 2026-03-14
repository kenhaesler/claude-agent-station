"""Migration 0003: Simplify config schema.

Removes old overlapping limit fields from station-config.json and replaces
them with two simple controls:
  - max_usage_percent  (replaces max_session_percent)
  - reserve_percent    (replaces token_reserve_percent)

Removed fields:
  - token_limit_daily
  - token_limit_monthly
  - token_reserve_percent  (replaced by reserve_percent)
  - session_limit_24h
  - max_session_percent    (replaced by max_usage_percent)

Kept fields (unchanged):
  - max_employee_turns, max_analyst_turns, max_manager_turns
  - max_rejection_retries, max_concurrent_employees,
    max_employees_per_project, token_budget_strategy

This migration is safe to run multiple times (idempotent).
"""

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Fields to remove
_REMOVED_FIELDS = {
    "token_limit_daily",
    "token_limit_monthly",
    "token_reserve_percent",
    "session_limit_24h",
    "max_session_percent",
}

# Mapping: old field -> new field (for value migration)
_FIELD_RENAMES = {
    "max_session_percent": "max_usage_percent",
    "token_reserve_percent": "reserve_percent",
}

# Defaults for new fields
_NEW_DEFAULTS = {
    "max_usage_percent": 80,
    "reserve_percent": 20,
}


def migrate_limits(limits: dict[str, Any]) -> dict[str, Any]:
    """Migrate a limits dict from old schema to new schema.

    Args:
        limits: The current limits dictionary (may be old or new schema).

    Returns:
        A new dict with old fields removed and new fields set.
    """
    result = dict(limits)

    # Derive new fields from old ones (if old exist and new don't)
    for old_name, new_name in _FIELD_RENAMES.items():
        if old_name in result and new_name not in result:
            result[new_name] = result[old_name]
            logger.info(
                "Migrated limits.%s=%s -> limits.%s",
                old_name, result[old_name], new_name,
            )

    # Set defaults for any new fields still missing
    for field, default in _NEW_DEFAULTS.items():
        if field not in result:
            result[field] = default
            logger.info("Set default limits.%s=%s", field, default)

    # Remove old fields
    for field in _REMOVED_FIELDS:
        if field in result:
            logger.info("Removed limits.%s (value was %s)", field, result[field])
            del result[field]

    return result


def migrate_config_file(config_path: str) -> bool:
    """Migrate a station-config.json file in place.

    Args:
        config_path: Path to station-config.json.

    Returns:
        True if the file was modified, False if no changes were needed.
    """
    path = Path(config_path)
    if not path.exists():
        logger.info("Config file %s does not exist, nothing to migrate", config_path)
        return False

    with open(path) as f:
        config = json.load(f)

    limits = config.get("limits")
    if limits is None:
        logger.info("No 'limits' section in config, nothing to migrate")
        return False

    # Check if migration is needed
    has_old_fields = any(field in limits for field in _REMOVED_FIELDS)
    has_new_fields = all(field in limits for field in _NEW_DEFAULTS)

    if not has_old_fields and has_new_fields:
        logger.info("Config already migrated, no changes needed")
        return False

    # Perform migration
    config["limits"] = migrate_limits(limits)

    # Atomic write
    dir_path = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, str(path))
        logger.info("Migration 0003 applied to %s", config_path)
        return True
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def run(config_path: str | None = None) -> bool:
    """Entry point for the migration.

    Args:
        config_path: Override path to config file. If None, uses the
            default path from app.config.settings.

    Returns:
        True if changes were made.
    """
    if config_path is None:
        try:
            from app.config import settings
            config_path = settings.config_path
        except ImportError:
            # Fallback for standalone execution
            config_path = "/var/lib/claude-agent-station/station-config.json"

    return migrate_config_file(config_path)


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    path = sys.argv[1] if len(sys.argv) > 1 else None
    changed = run(path)
    sys.exit(0 if changed else 2)
