"""Tests for agent/scripts/gh-refresh-wrapper.sh — the gh CLI shim that
auto-refreshes GH_TOKEN on 401 by fetching a fresh App installation
token from the dashboard.

Strategy: the wrapper takes REAL_GH from a hard-coded path in the
script, so for testing we run the wrapper with PATH munging that
puts a fake `gh` (we control) first. We accomplish this by writing
the wrapper to a temp dir and overriding the REAL_GH constant via
sed.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
WRAPPER_SRC = REPO_ROOT / "agent" / "scripts" / "gh-refresh-wrapper.sh"


def _make_fake_gh(workdir: Path, *, behavior: str) -> Path:
    """Write a fake gh binary that emits one of the test behaviors.

    `behavior` values:
      - "success": exit 0, prints "ok"
      - "401": exit 1, prints "HTTP 401: Bad credentials" to stderr
      - "401_then_success": stateful — first call 401, second call success.
        State stored in a counter file alongside the binary.
      - "other_error": exit 1 with an unrelated message (refresh must NOT kick in)
    """
    counter = workdir / "_call_counter"
    fake = workdir / "gh-fake"
    if behavior == "success":
        body = '#!/usr/bin/env bash\necho "ok"\nexit 0\n'
    elif behavior == "401":
        body = '#!/usr/bin/env bash\necho "HTTP 401: Bad credentials" >&2\nexit 1\n'
    elif behavior == "401_then_success":
        body = f'''#!/usr/bin/env bash
n=$(cat "{counter}" 2>/dev/null || echo 0)
n=$((n+1))
echo "$n" > "{counter}"
if [ "$n" -lt 2 ]; then
    echo "HTTP 401: Bad credentials" >&2
    exit 1
fi
echo "success on retry, GH_TOKEN=${{GH_TOKEN:-(unset)}}"
exit 0
'''
    elif behavior == "other_error":
        body = '#!/usr/bin/env bash\necho "some other gh failure" >&2\nexit 2\n'
    else:
        raise ValueError(behavior)
    fake.write_text(body)
    fake.chmod(0o755)
    return fake


def _make_fake_dashboard(workdir: Path, *, token: str | None) -> tuple[Path, Path]:
    """Write a fake `curl` (named curl-fake) that returns a JSON token.
    Returns (curl_path, dashboard_dir). When token is None, curl emits
    nothing (simulates network failure).
    """
    curl_fake = workdir / "curl-fake"
    if token is not None:
        body = (
            '#!/usr/bin/env bash\n'
            'printf \'%s\\n\' \'{"token":"' + token + '","source":"app"}\'\n'
            'exit 0\n'
        )
    else:
        body = '#!/usr/bin/env bash\nexit 1\n'
    curl_fake.write_text(body)
    curl_fake.chmod(0o755)
    return curl_fake, workdir


def _prepare_wrapper(workdir: Path, real_gh: Path, fake_curl: Path) -> Path:
    """Copy the wrapper into the workdir with REAL_GH and curl-call
    overridden so the test doesn't touch the real /usr/bin/gh or the real
    network."""
    raw = WRAPPER_SRC.read_text()
    # Substitute the REAL_GH constant
    patched = raw.replace("REAL_GH=/usr/bin/gh", f'REAL_GH="{real_gh}"')
    # Substitute the bare `curl` invocation with our fake. The wrapper
    # invokes curl exactly once via:  curl "${refresh_args[@]}" URL
    patched = patched.replace('refresh=$(curl ', f'refresh=$({fake_curl} ')
    target = workdir / "gh-wrapper-under-test"
    target.write_text(patched)
    target.chmod(0o755)
    return target


def _run(wrapper: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [str(wrapper), *args],
        capture_output=True,
        text=True,
        env=full_env,
    )


@pytest.fixture
def workdir(tmp_path):
    yield tmp_path


def test_passes_through_on_success(workdir):
    fake_gh = _make_fake_gh(workdir, behavior="success")
    fake_curl, _ = _make_fake_dashboard(workdir, token="fresh-token-abc")
    wrapper = _prepare_wrapper(workdir, fake_gh, fake_curl)

    result = _run(wrapper, "issue", "list", env={"GH_TOKEN_CACHE": str(workdir / "_cache_pass")})
    assert result.returncode == 0
    assert "ok" in result.stdout


def test_retries_on_401_with_fresh_token(workdir):
    fake_gh = _make_fake_gh(workdir, behavior="401_then_success")
    fake_curl, _ = _make_fake_dashboard(workdir, token="ghs_freshA1B2")
    wrapper = _prepare_wrapper(workdir, fake_gh, fake_curl)

    cache = workdir / "_cache_retry"
    result = _run(wrapper, "issue", "list", env={
        "GH_TOKEN_CACHE": str(cache),
        "GH_TOKEN": "ghs_staleORIG",
    })
    assert result.returncode == 0, f"stderr={result.stderr}"
    assert "success on retry" in result.stdout
    assert "GH_TOKEN=ghs_freshA1B2" in result.stdout
    # Cache should now hold the fresh token
    assert cache.exists()
    assert cache.read_text() == "ghs_freshA1B2"


def test_propagates_failure_when_refresh_returns_no_token(workdir):
    fake_gh = _make_fake_gh(workdir, behavior="401")
    fake_curl, _ = _make_fake_dashboard(workdir, token=None)  # curl fails
    wrapper = _prepare_wrapper(workdir, fake_gh, fake_curl)

    result = _run(wrapper, "issue", "list", env={
        "GH_TOKEN_CACHE": str(workdir / "_cache_norefresh"),
    })
    assert result.returncode != 0
    assert "Bad credentials" in result.stderr


def test_does_not_retry_on_unrelated_failure(workdir):
    """If gh fails with a non-auth error, the wrapper must NOT try to
    refresh — refreshing on every error would mask real bugs and waste
    dashboard calls."""
    fake_gh = _make_fake_gh(workdir, behavior="other_error")
    fake_curl, _ = _make_fake_dashboard(workdir, token="should-not-be-used")
    wrapper = _prepare_wrapper(workdir, fake_gh, fake_curl)

    cache = workdir / "_cache_unrelated"
    result = _run(wrapper, "issue", "list", env={"GH_TOKEN_CACHE": str(cache)})
    assert result.returncode == 2
    assert "some other gh failure" in result.stderr
    # Cache must NOT have been written
    assert not cache.exists()


def test_uses_cached_token_when_fresh(workdir):
    """A pre-existing recent cache file must be sourced into GH_TOKEN
    before the first gh call — letting the gh binary see the right token
    without needing to refresh first."""
    fake_gh = _make_fake_gh(workdir, behavior="success")
    # Patch the fake gh to echo the GH_TOKEN value it sees so we can
    # assert the wrapper sourced from cache.
    fake_gh.write_text(
        '#!/usr/bin/env bash\necho "saw GH_TOKEN=${GH_TOKEN:-(unset)}"\nexit 0\n'
    )
    fake_curl, _ = _make_fake_dashboard(workdir, token="should-not-be-fetched")
    wrapper = _prepare_wrapper(workdir, fake_gh, fake_curl)

    cache = workdir / "_cache_fresh"
    cache.write_text("ghs_cachedXYZ")
    cache.chmod(0o600)

    result = _run(wrapper, "issue", "list", env={
        "GH_TOKEN_CACHE": str(cache),
        "GH_TOKEN": "ghs_oldEnvToken",  # stale env value
    })
    assert result.returncode == 0
    assert "saw GH_TOKEN=ghs_cachedXYZ" in result.stdout
