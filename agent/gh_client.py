"""Thin wrapper around the ``gh`` CLI for subprocess + JSON conversion.

Centralises ``gh`` invocations so they have a single error-handling and
argv-construction site, which we'll reuse for issue picking (#362) and
verdict execution (#363). Avoids ``shlex`` traps from ad-hoc bash-style
quoting by always passing argv lists.

The module is deliberately small: this is plumbing, not a domain model.
The ``gh`` binary must be installed and authenticated in the environment
(``GH_TOKEN``) where this runs — the agent container takes care of both.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

GH_BIN = "gh"
DEFAULT_TIMEOUT = 30.0


class GhError(Exception):
    """gh subprocess returned non-zero. Carries the command and stderr
    so callers can surface a useful diagnostic without re-running."""

    def __init__(self, cmd: list[str], returncode: int, stderr: str) -> None:
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"gh {' '.join(cmd[1:])!s} failed (rc={returncode}): {stderr.strip()[:200]}"
        )


@dataclass
class GhResult:
    """Light wrapper around subprocess.CompletedProcess for callers that
    want stdout/stderr but don't need raw bytes."""

    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def gh_json(args: list[str], *, env: dict | None = None,
            timeout: float = DEFAULT_TIMEOUT) -> Any:
    """Run ``gh ARGS`` and parse stdout as JSON.

    Raises :class:`GhError` on non-zero exit. JSON decode errors raise
    :class:`json.JSONDecodeError` (so they surface separately from gh
    process failures).
    """
    cmd = [GH_BIN, *args]
    logger.debug("gh_json: %s", cmd)
    result = subprocess.run(
        cmd, capture_output=True, text=True, env=env, timeout=timeout,
    )
    if result.returncode != 0:
        raise GhError(cmd, result.returncode, result.stderr)
    return json.loads(result.stdout) if result.stdout.strip() else None


def gh_run(args: list[str], *, env: dict | None = None,
           timeout: float = DEFAULT_TIMEOUT) -> GhResult:
    """Run ``gh ARGS`` and return a :class:`GhResult`. Does NOT raise on
    non-zero exit — callers inspect ``result.ok`` / ``result.returncode``
    and decide. Use this for verdict-execution paths (``gh pr create``,
    ``gh issue comment``) where the caller may want to surface ``stderr``
    in a webhook payload rather than crash.
    """
    cmd = [GH_BIN, *args]
    logger.debug("gh_run: %s", cmd)
    cp = subprocess.run(
        cmd, capture_output=True, text=True, env=env, timeout=timeout,
    )
    return GhResult(cmd=cmd, returncode=cp.returncode, stdout=cp.stdout, stderr=cp.stderr)
