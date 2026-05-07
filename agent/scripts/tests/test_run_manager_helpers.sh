#!/usr/bin/env bash
# test_run_manager_helpers.sh - Smoke tests for run-manager.sh helpers
# Covers issues #185 (json_get / ensure_gh_token shell-into-Python interpolation)
# and #180 (build_webhook_json / webhook_event JSON construction).
#
# Run with: bash agent/scripts/tests/test_run_manager_helpers.sh
# Exits 0 on success, non-zero on failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_MANAGER="$SCRIPT_DIR/../run-manager.sh"

if [ ! -f "$RUN_MANAGER" ]; then
    echo "ERROR: cannot find $RUN_MANAGER" >&2
    exit 2
fi

# Sourcing run-manager.sh requires sourcing integration-branch.sh which is
# fine — it only defines functions. Suppress its lib-source side effects by
# providing a placeholder STATION_CONFIG so json_get can be exercised.
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

# Source the script in test mode (BASH_SOURCE differs from $0 -> main NOT run).
# shellcheck source=../run-manager.sh
. "$RUN_MANAGER"

PASS=0
FAIL=0
FAILURES=()

assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        PASS=$((PASS + 1))
        echo "  PASS: $name"
    else
        FAIL=$((FAIL + 1))
        FAILURES+=("$name: expected [$expected] got [$actual]")
        echo "  FAIL: $name"
        echo "    expected: $expected"
        echo "    actual:   $actual"
    fi
}

assert_contains() {
    local name="$1" needle="$2" haystack="$3"
    if [[ "$haystack" == *"$needle"* ]]; then
        PASS=$((PASS + 1))
        echo "  PASS: $name"
    else
        FAIL=$((FAIL + 1))
        FAILURES+=("$name: substring [$needle] not found in [$haystack]")
        echo "  FAIL: $name"
        echo "    needle:   $needle"
        echo "    haystack: $haystack"
    fi
}

assert_json_valid() {
    local name="$1" payload="$2"
    if printf '%s' "$payload" | python3 -c "import json,sys; json.loads(sys.stdin.read())" 2>/dev/null; then
        PASS=$((PASS + 1))
        echo "  PASS: $name"
    else
        FAIL=$((FAIL + 1))
        FAILURES+=("$name: payload is not valid JSON: $payload")
        echo "  FAIL: $name"
        echo "    payload: $payload"
    fi
}

# ---------------------------------------------------------------------------
# Issue #185 — json_get / ensure_gh_token must use argv, not interpolation
# ---------------------------------------------------------------------------
echo "[#185] json_get tests"

# Test fixture: simple nested JSON
cat > "$TMPDIR/config.json" <<'JSON'
{
    "projects": [
        {"name": "alpha", "enabled": true},
        {"name": "beta", "enabled": false}
    ],
    "limits": {"max_concurrent": 3},
    "value_with_quote": "it's tricky",
    "missing_friendly": null
}
JSON

assert_eq "json_get nested string" "alpha" "$(json_get "$TMPDIR/config.json" "projects.0.name")"
assert_eq "json_get nested bool true" "true" "$(json_get "$TMPDIR/config.json" "projects.0.enabled")"
assert_eq "json_get nested bool false" "false" "$(json_get "$TMPDIR/config.json" "projects.1.enabled")"
assert_eq "json_get integer" "3" "$(json_get "$TMPDIR/config.json" "limits.max_concurrent")"
assert_eq "json_get value containing single quote" "it's tricky" "$(json_get "$TMPDIR/config.json" "value_with_quote")"

# Missing key returns non-zero and empty stdout
missing_out="$(json_get "$TMPDIR/config.json" "projects.0.does_not_exist" || echo "MISSING")"
assert_eq "json_get missing key returns MISSING sentinel" "MISSING" "$missing_out"

# Path containing single quote and Python source — must NOT execute as code,
# and must NOT crash json_get; expected to fail-key-lookup cleanly.
INJECT_PATH="x';__import__('os').system('echo PWNED > $TMPDIR/pwned.txt')#"
inj_out="$(json_get "$TMPDIR/config.json" "$INJECT_PATH" || echo "SAFE")"
assert_eq "json_get injection path returns SAFE" "SAFE" "$inj_out"
if [ -f "$TMPDIR/pwned.txt" ]; then
    FAIL=$((FAIL + 1))
    FAILURES+=("json_get path injection executed code (pwned.txt was created)")
    echo "  FAIL: json_get path injection did NOT execute"
else
    PASS=$((PASS + 1))
    echo "  PASS: json_get path injection did NOT execute"
fi

# File path containing a single quote should still work
QUOTED_DIR="$TMPDIR/o'brien"
mkdir -p "$QUOTED_DIR"
cp "$TMPDIR/config.json" "$QUOTED_DIR/config.json"
assert_eq "json_get with quoted file path" "alpha" "$(json_get "$QUOTED_DIR/config.json" "projects.0.name")"

# Returns full nested object as JSON
nested="$(json_get "$TMPDIR/config.json" "limits")"
assert_json_valid "json_get returns valid JSON for object" "$nested"
assert_contains "json_get nested object contents" "max_concurrent" "$nested"

# ---------------------------------------------------------------------------
# Issue #185 — ensure_gh_token also uses argv-safe Python
# ---------------------------------------------------------------------------
echo "[#185] ensure_gh_token tests"

# Build a fake token file; verify ensure_gh_token loads it. Override $HOME.
SAVED_HOME="$HOME"
SAVED_GH_TOKEN="${GH_TOKEN:-}"
export HOME="$TMPDIR/fakehome"
mkdir -p "$HOME/.claude-agent-station"
cat > "$HOME/.claude-agent-station/github_token" <<'JSON'
{"access_token": "ghs_safe_test_token_42"}
JSON
unset GH_TOKEN
ensure_gh_token
assert_eq "ensure_gh_token loads access_token from file" "ghs_safe_test_token_42" "${GH_TOKEN:-}"

# A token file path with a single quote should also work (we'll override
# the function's expectation by constructing $HOME with a quote in it).
unset GH_TOKEN
QUOTED_HOME="$TMPDIR/o'brien-home"
mkdir -p "$QUOTED_HOME/.claude-agent-station"
cat > "$QUOTED_HOME/.claude-agent-station/github_token" <<'JSON'
{"access_token": "ghs_quoted_path_token"}
JSON
HOME="$QUOTED_HOME" ensure_gh_token
assert_eq "ensure_gh_token works with quoted HOME path" "ghs_quoted_path_token" "${GH_TOKEN:-}"

export HOME="$SAVED_HOME"
if [ -n "$SAVED_GH_TOKEN" ]; then
    export GH_TOKEN="$SAVED_GH_TOKEN"
else
    unset GH_TOKEN
fi

# ---------------------------------------------------------------------------
# Issue #180 — build_webhook_json constructs valid JSON via json.dumps
# ---------------------------------------------------------------------------
echo "[#180] build_webhook_json tests"

simple="$(build_webhook_json "run_start" "run-1" project "owner/repo" project_count 5)"
assert_json_valid "build_webhook_json simple call is valid JSON" "$simple"
assert_contains "build_webhook_json includes event field" '"event": "run_start"' "$simple"
assert_contains "build_webhook_json includes run_id" '"run_id": "run-1"' "$simple"
assert_contains "build_webhook_json string field quoted" '"project": "owner/repo"' "$simple"
# project_count must be numeric (no quotes around 5)
assert_contains "build_webhook_json int field unquoted" '"project_count": 5' "$simple"

# Boolean and null coercion
bool_out="$(build_webhook_json "evt" "run-1" enabled true paused false placeholder null)"
assert_contains "build_webhook_json true unquoted" '"enabled": true' "$bool_out"
assert_contains "build_webhook_json false unquoted" '"paused": false' "$bool_out"
assert_contains "build_webhook_json null unquoted" '"placeholder": null' "$bool_out"

# Float coercion
float_out="$(build_webhook_json "evt" "run-1" confidence 0.85)"
assert_contains "build_webhook_json float unquoted" '"confidence": 0.85' "$float_out"

# Reasoning with quotes / newlines / backslashes round-trips correctly
NASTY=$'It\'s a "great" fix\nwith newlines and a backslash \\'
nasty_out="$(build_webhook_json "verdict_execute" "run-1" reasoning "$NASTY")"
assert_json_valid "build_webhook_json nasty string is valid JSON" "$nasty_out"
roundtrip="$(printf '%s' "$nasty_out" | python3 -c "import json,sys; print(json.load(sys.stdin)['reasoning'])")"
assert_eq "build_webhook_json reasoning round-trips" "$NASTY" "$roundtrip"

# Empty string allowed
empty_out="$(build_webhook_json "evt" "run-1" branch "")"
assert_contains "build_webhook_json empty string preserved" '"branch": ""' "$empty_out"

# Negative integer
neg_out="$(build_webhook_json "evt" "run-1" exit_code -1)"
assert_contains "build_webhook_json negative int unquoted" '"exit_code": -1' "$neg_out"

# Number-looking string with leading zero stays numeric only if isdigit
# (Plain integer literals — '0' should stay int(0); '007' is also int(7).)
zero_out="$(build_webhook_json "evt" "run-1" exit_code 0)"
assert_contains "build_webhook_json zero int unquoted" '"exit_code": 0' "$zero_out"

# Timestamp is auto-injected
ts_out="$(build_webhook_json "evt" "run-1")"
assert_contains "build_webhook_json adds timestamp" '"timestamp"' "$ts_out"

# Odd number of kv args: trailing key without value is ignored, no crash
trail_out="$(build_webhook_json "evt" "run-1" key1 v1 dangling)"
assert_json_valid "build_webhook_json odd-kv tail is still valid JSON" "$trail_out"
assert_contains "build_webhook_json odd-kv keeps prior pair" '"key1": "v1"' "$trail_out"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo
echo "============================================"
echo "Tests passed: $PASS"
echo "Tests failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
    echo
    echo "Failures:"
    for f in "${FAILURES[@]}"; do
        echo "  - $f"
    done
    exit 1
fi
exit 0
