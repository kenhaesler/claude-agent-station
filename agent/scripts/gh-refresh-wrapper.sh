#!/usr/bin/env bash
# gh-refresh-wrapper.sh — drop-in replacement for /usr/bin/gh that
# auto-refreshes GH_TOKEN on 401 / bad-credentials by fetching a fresh
# GitHub App installation token from the dashboard.
#
# Installed at /usr/local/bin/gh in the agent container; PATH order
# resolves us before the real /usr/bin/gh. Every subprocess (including
# Claude SDK Bash tool calls) gets the auto-refresh transparently.
#
# Why: GitHub App installation tokens have ~1 hour TTL. The launcher
# fetches one at run spawn and bakes it into the subprocess env. Long
# multi-teammate runs (especially the manager review at the end) hit
# 401s and waste turns diagnosing instead of executing verdicts. See
# diagnosis of run-20260512T085006Z.

set -uo pipefail

REAL_GH=/usr/bin/gh
CACHE="${GH_TOKEN_CACHE:-/tmp/.gh_token}"
DASHBOARD="${STATION_DASHBOARD_URL:-http://dashboard:8420}"
LAUNCHER_TOKEN="${STATION_LAUNCHER_TOKEN:-}"
# 50 min — App tokens are 1 hour, this leaves a 10 min safety margin.
CACHE_TTL_SECONDS=3000

# If a recent cached token exists, prefer it over the (potentially stale)
# env-provided one. Each process invocation does an independent freshness
# check; the cache is the only shared state across calls.
if [ -f "$CACHE" ] && [ -r "$CACHE" ]; then
    age=$(($(date +%s) - $(stat -c %Y "$CACHE")))
    if [ "$age" -lt "$CACHE_TTL_SECONDS" ]; then
        GH_TOKEN="$(cat "$CACHE")"
        export GH_TOKEN
    fi
fi

# Run the call, capturing both streams to temp files so we can preserve
# their separation when we re-emit.
out_file=$(mktemp)
err_file=$(mktemp)
trap 'rm -f "$out_file" "$err_file"' EXIT

"$REAL_GH" "$@" >"$out_file" 2>"$err_file"
rc=$?

if [ $rc -eq 0 ]; then
    cat "$out_file"
    cat "$err_file" >&2
    exit 0
fi

# Auth-failure heuristic: gh's actual auth-failure phrasing.
if grep -qiE "bad credentials|HTTP 401|token in GH_TOKEN is invalid|token in default is invalid|gh auth login" "$out_file" "$err_file"; then
    # Fetch a fresh token from the dashboard.
    refresh_args=(-sS --max-time 5)
    [ -n "$LAUNCHER_TOKEN" ] && refresh_args+=(-H "X-Launcher-Token: $LAUNCHER_TOKEN")
    refresh=$(curl "${refresh_args[@]}" "$DASHBOARD/api/github/app/token" 2>/dev/null || echo "")
    new_token=$(echo "$refresh" | python3 -c "
import json, sys
try:
    print(json.load(sys.stdin).get('token', ''))
except Exception:
    pass
" 2>/dev/null || echo "")
    if [ -n "$new_token" ]; then
        # Atomic-ish cache write (mv from temp; the cache lives in /tmp so
        # this can't fail for permissions reasons in normal operation).
        echo -n "$new_token" > "${CACHE}.tmp" && mv "${CACHE}.tmp" "$CACHE"
        chmod 600 "$CACHE" 2>/dev/null || true
        GH_TOKEN="$new_token"
        export GH_TOKEN
        # Retry once with the fresh token.
        exec "$REAL_GH" "$@"
    fi
fi

# Refresh failed or wasn't applicable — surface the original output / exit.
cat "$out_file"
cat "$err_file" >&2
exit $rc
