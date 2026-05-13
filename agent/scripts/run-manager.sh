#!/usr/bin/env bash
# run-manager.sh - Manager/Employee Autonomous Agent Orchestrator
# Manages multiple projects with a manager-reviews-employees pattern
# Part of Claude Agent Station

set -euo pipefail

# ============================================================================
# CONFIGURATION
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/integration-branch.sh"
CONFIG_FILE="${STATION_CONFIG:-/home/claude-agent/.claude/autonomous/manager-config.json}"

# Resolve prompt file: prefer custom override if it exists, else use default
resolve_prompt() {
    local role="$1"
    local custom="$SCRIPT_DIR/../prompts/custom/${role}.md"
    local default_prompt="$SCRIPT_DIR/../prompts/${role}.md"
    if [ -f "$custom" ]; then
        echo "$custom"
    else
        echo "$default_prompt"
    fi
}
if [ -n "${STATION_RUN_ID_OVERRIDE:-}" ]; then
    _override="${STATION_RUN_ID_OVERRIDE#run-}"
    RUN_ID="$_override"
else
    RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
fi
_RUN_COMPLETE_SENT=0  # Flag: set to 1 once run_complete webhook has been sent
LOG_DIR=""
DIGEST_DIR=""
WORKSPACES_DIR="${STATION_WORKSPACES:-/home/claude-agent/workspaces}"
DRY_RUN=false
# --internal-iterate is set by agent/project_loop.py (issue #349 migration).
# When true, run_start/run_complete lifecycle events are owned by RunDriver
# (Python try/finally) and must NOT be emitted from bash to avoid duplication.
INTERNAL_ITERATE=false

# Token accumulation across all employees + manager
_TOTAL_TOKENS_IN=0
_TOTAL_TOKENS_OUT=0
_TOTAL_TURNS=0
_RUN_START_EPOCH=0

# Tool permissions - UNRESTRICTED
# This agent runs on a dedicated disposable VM. No tool restrictions needed.
# Behavioral guardrails (no push, no source edits in analyze mode) are enforced
# via prompts, not tool flags. Restricting tools causes false-positive permission
# errors (e.g. compound bash commands rejected by pattern mismatch) and prevents
# the agent from leveraging the full VM environment.

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

log_info()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [INFO]  $1"; }
log_warn()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [WARN]  $1"; }
log_error() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [ERROR] $1" >&2; }
log_ok()    { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [OK]    $1"; }

json_get() {
    # Read a value from a JSON file by dotted path. Args (file, path) are
    # passed via argv to the embedded Python — never interpolated into the
    # source — so quotes/newlines/code-like characters in either are inert.
    # See issue #185.
    local file="$1" path="$2"
    python3 - "$file" "$path" 2>/dev/null <<'PYEOF'
import json, sys
file_path, dotted = sys.argv[1], sys.argv[2]
with open(file_path) as f:
    data = json.load(f)
keys = dotted.split('.')
for k in keys:
    if isinstance(data, list):
        data = data[int(k)]
    else:
        data = data.get(k)
    if data is None:
        sys.exit(1)
if isinstance(data, (dict, list)):
    print(json.dumps(data))
elif isinstance(data, bool):
    print('true' if data else 'false')
else:
    print(data)
PYEOF
}

format_close_keywords() {
    # Emit a GitHub closing-keyword line for a PR body so merging the PR
    # auto-closes the linked issue(s). Returns nothing when there is no
    # issue context (SKIP path, no-issue run) so the body has no dangling
    # "Closes " line.
    #
    # Args:
    #   $1 — singular issue_number (e.g. "15") or empty/None/null
    #   $2 — JSON array of issue_numbers (e.g. "[15,16]") or empty
    #
    # Multi-issue runs use "Closes #15, closes #16, closes #17" — GitHub
    # accepts a single keyword followed by comma-separated references and
    # auto-closes all of them. The plural list takes precedence when both
    # are supplied; falls back to the singular for back-compat with
    # verdicts that only carry one issue.
    local single="${1:-}" plural="${2:-}"
    python3 - "$single" "$plural" 2>/dev/null <<'PYEOF'
import json, sys
single = sys.argv[1] if len(sys.argv) > 1 else ""
plural_raw = sys.argv[2] if len(sys.argv) > 2 else ""

nums: list[int] = []
if plural_raw and plural_raw not in ("", "None", "null"):
    try:
        parsed = json.loads(plural_raw)
        if isinstance(parsed, list):
            for n in parsed:
                try:
                    nums.append(int(n))
                except (TypeError, ValueError):
                    pass
    except json.JSONDecodeError:
        pass

if not nums and single and single not in ("None", "null", ""):
    try:
        nums.append(int(single))
    except (TypeError, ValueError):
        pass

# Dedupe while preserving order.
seen: set[int] = set()
ordered: list[int] = []
for n in nums:
    if n not in seen:
        seen.add(n)
        ordered.append(n)

if not ordered:
    sys.exit(0)

parts = [f"Closes #{ordered[0]}"]
parts.extend(f"closes #{n}" for n in ordered[1:])
print(", ".join(parts))
PYEOF
}

rebase_against_base() {
    # Calls resolve-conflicts.sh in pre-PR or at-merge mode. Logs the
    # outcome but never fails the caller — the existing manual-review
    # path is the safety net.
    #
    # Args:
    #   $1 — workspace path (worktree)
    #   $2 — head branch name
    #   $3 — base branch name
    #   $4 — repo (owner/name)
    #   $5 — pr_number ("" if pre-PR)
    #   $6 — run_id ("" if standalone)
    #   $7 — triggered_by ("pre_pr" or "at_merge"; default "pre_pr")
    local workspace="$1" branch="$2" base="$3" repo="$4"
    local pr_num="${5:-}" run_id="${6:-}" triggered_by="${7:-pre_pr}"
    local script_dir
    script_dir="$(dirname "${BASH_SOURCE[0]}")"
    local args=(
        --workspace "$workspace"
        --branch "$branch"
        --base "$base"
        --repo "$repo"
        --triggered-by "$triggered_by"
    )
    [ -n "$pr_num" ] && args+=(--pr "$pr_num")
    [ -n "$run_id" ] && args+=(--run-id "$run_id")
    set +e
    "$script_dir/resolve-conflicts.sh" "${args[@]}"
    local rc=$?
    set -e
    log_info "rebase_against_base returned $rc for $branch"
    return "$rc"
}

ensure_gh_token() {
    # Load GH_TOKEN from dashboard-managed file if not already set.
    # Falls back to existing GH_TOKEN env var for backward compatibility.
    # The token file path is passed via argv to the embedded Python (issue #185).
    local token_file="$HOME/.claude-agent-station/github_token"
    if [ -f "$token_file" ]; then
        local file_token
        file_token=$(python3 - "$token_file" <<'PYEOF' 2>/dev/null
import json, sys
try:
    with open(sys.argv[1]) as f:
        print(json.load(f).get('access_token', ''))
except Exception:
    pass
PYEOF
) || file_token=""
        if [ -n "$file_token" ]; then
            export GH_TOKEN="$file_token"
            return 0
        fi
    fi
    # Keep existing GH_TOKEN if set (backward compatibility)
    return 0
}

notify() {
    local status="$1" message="$2"
    local enabled
    enabled=$(json_get "$CONFIG_FILE" "notifications.enabled" 2>/dev/null || echo "false")
    [ "$enabled" != "true" ] && return 0

    local method
    method=$(json_get "$CONFIG_FILE" "notifications.method" 2>/dev/null || echo "file")
    case "$method" in
        file)
            local nfile
            nfile=$(json_get "$CONFIG_FILE" "notifications.notification_file" 2>/dev/null || echo "/var/log/claude-agent/notifications.log")
            echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [$status] $message" >> "$nfile" 2>/dev/null || true
            ;;
        webhook)
            local url
            url=$(json_get "$CONFIG_FILE" "notifications.webhook_url" 2>/dev/null || echo "")
            if [ -n "$url" ]; then
                # Build payload via python json.dumps so values containing
                # quotes/newlines/etc. are escaped correctly (issue #180).
                local notify_payload
                notify_payload=$(python3 - "$status" "$message" "$RUN_ID" 2>/dev/null <<'PYEOF'
import json, sys
print(json.dumps({
    "status": sys.argv[1],
    "message": sys.argv[2],
    "run_id": sys.argv[3],
}))
PYEOF
)
                curl -s -X POST "$url" \
                    -H "Content-Type: application/json" \
                    -d "$notify_payload" 2>/dev/null || true
            fi
            ;;
    esac
}

# ============================================================================
# DASHBOARD WEBHOOK (best-effort, never fails the agent run)
# ============================================================================

build_webhook_json() {
    # Build a JSON payload using python's json.dumps so that string values
    # with quotes/newlines/backslashes are escaped correctly. Issue #180.
    #
    # Usage: build_webhook_json EVENT RUN_ID [key value]...
    #   - EVENT and RUN_ID become top-level fields.
    #   - A "timestamp" field is auto-added (UTC, RFC3339-ish).
    #   - Remaining argv pairs are interpreted as key/value. Values matching
    #     "true"/"false"/"null" or numeric (int/float) literals are coerced
    #     to JSON booleans/null/numbers; everything else stays a string.
    #   - An odd trailing key with no value is silently skipped.
    python3 - "$@" 2>/dev/null <<'PYEOF'
import json, sys, datetime
argv = sys.argv[1:]
event = argv[0] if len(argv) > 0 else ""
run_id = argv[1] if len(argv) > 1 else ""
out = {
    "event": event,
    "run_id": run_id,
    "timestamp": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
}
pairs = argv[2:]
for i in range(0, len(pairs) - 1, 2):
    k = pairs[i]
    v = pairs[i + 1]
    if v == "true":
        out[k] = True
    elif v == "false":
        out[k] = False
    elif v == "null":
        out[k] = None
    else:
        # Numeric coercion: int first, then float; otherwise keep as string.
        coerced = v
        if v.lstrip("-").isdigit():
            try:
                coerced = int(v)
            except ValueError:
                pass
        elif v.replace(".", "", 1).lstrip("-").isdigit():
            try:
                coerced = float(v)
            except ValueError:
                pass
        out[k] = coerced
print(json.dumps(out))
PYEOF
}

webhook_event() {
    # Args: EVENT [key value]...
    # All extra args are JSON-encoded into a single payload object and
    # forwarded to the Python emitter, which owns retry semantics and
    # auth. See issue #349 sub-PR 5a.
    local event="$1"
    shift

    local payload="{}"
    if [ $# -gt 0 ]; then
        payload=$(python3 - "$@" <<'PYEOF'
import json, sys
args = sys.argv[1:]
out = {}
it = iter(args)
for k in it:
    try:
        v = next(it)
    except StopIteration:
        break
    out[k] = v
print(json.dumps(out))
PYEOF
)
    fi

    # Preserve the pre-5a operator contract: if env vars aren't set, fall back
    # to the dashboard.* fields in $CONFIG_FILE. Other call sites still consult
    # the config file, so without this the bash-emitted events would diverge.
    local _cfg_url _cfg_secret
    _cfg_url=$(json_get "$CONFIG_FILE" "dashboard.webhook_url" 2>/dev/null || echo "")
    _cfg_secret=$(json_get "$CONFIG_FILE" "dashboard.webhook_secret" 2>/dev/null || echo "")
    [ -z "${STATION_WEBHOOK_URL:-}" ] && [ -n "$_cfg_url" ] && export STATION_WEBHOOK_URL="$_cfg_url"
    [ -z "${STATION_WEBHOOK_SECRET:-}" ] && [ -n "$_cfg_secret" ] && export STATION_WEBHOOK_SECRET="$_cfg_secret"

    PYTHONPATH="$SCRIPT_DIR/../.." \
        python3 -m agent.webhook_emitter "$event" \
            --run-id "run-$RUN_ID" \
            --json "$payload" 2>&1 | while IFS= read -r line; do
                log_info "  webhook[$event]: $line"
            done || true
}

queue_api() {
    local method="$1" path="$2" body="${3:-}"
    local url="http://127.0.0.1:8420${path}"
    if [ -n "$body" ]; then
        curl -s --max-time 5 -X "$method" "$url" -H "Content-Type: application/json" -d "$body" 2>/dev/null || echo ""
    else
        curl -s --max-time 5 -X "$method" "$url" 2>/dev/null || echo ""
    fi
}

queue_find_item() {
    # Find queue item by project_repo and assigned_to for the current run
    local project="$1" employee_idx="$2"
    local result
    result=$(queue_api GET "/api/queue?project_repo=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$project'))")&run_id=run-$RUN_ID&limit=100")
    echo "$result" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    for item in data.get('items', []):
        if item.get('assigned_to') == $employee_idx:
            print(item['id'])
            break
except: pass
" 2>/dev/null || echo ""
}

queue_complete_item() {
    # Walk a queue item through valid state transitions to reach 'completed'.
    # Handles items in any active state (assigned, in_progress, review, approved).
    local qid="$1"
    local current_state
    current_state=$(queue_api GET "/api/queue/$qid" | python3 -c "import json,sys; print(json.load(sys.stdin).get('state',''))" 2>/dev/null || echo "")
    case "$current_state" in
        assigned)
            queue_api PUT "/api/queue/$qid" '{"state":"in_progress"}' >/dev/null 2>&1
            queue_api PUT "/api/queue/$qid" '{"state":"review"}' >/dev/null 2>&1
            queue_api PUT "/api/queue/$qid" '{"state":"approved"}' >/dev/null 2>&1
            queue_api PUT "/api/queue/$qid" '{"state":"completed"}' >/dev/null 2>&1
            ;;
        in_progress)
            queue_api PUT "/api/queue/$qid" '{"state":"review"}' >/dev/null 2>&1
            queue_api PUT "/api/queue/$qid" '{"state":"approved"}' >/dev/null 2>&1
            queue_api PUT "/api/queue/$qid" '{"state":"completed"}' >/dev/null 2>&1
            ;;
        review)
            queue_api PUT "/api/queue/$qid" '{"state":"approved"}' >/dev/null 2>&1
            queue_api PUT "/api/queue/$qid" '{"state":"completed"}' >/dev/null 2>&1
            ;;
        approved)
            queue_api PUT "/api/queue/$qid" '{"state":"completed"}' >/dev/null 2>&1
            ;;
        completed) ;;  # Already done
        *) log_warn "queue_complete_item: unexpected state '$current_state' for item $qid" ;;
    esac
}

queue_reject_item() {
    # Walk a queue item to 'review' if needed, then apply rejection payload.
    local qid="$1" payload="$2"
    local current_state
    current_state=$(queue_api GET "/api/queue/$qid" | python3 -c "import json,sys; print(json.load(sys.stdin).get('state',''))" 2>/dev/null || echo "")
    case "$current_state" in
        assigned)
            queue_api PUT "/api/queue/$qid" '{"state":"in_progress"}' >/dev/null 2>&1
            queue_api PUT "/api/queue/$qid" '{"state":"review"}' >/dev/null 2>&1
            ;;
        in_progress)
            queue_api PUT "/api/queue/$qid" '{"state":"review"}' >/dev/null 2>&1
            ;;
        review) ;;  # Already where we need to be
        *) log_warn "queue_reject_item: unexpected state '$current_state' for item $qid"; return 1 ;;
    esac
    queue_api PUT "/api/queue/$qid" "$payload" >/dev/null 2>&1
}

queue_fail_item() {
    # Walk a queue item to rejected then failed.
    local qid="$1" error_msg="$2"
    queue_reject_item "$qid" "{\"state\":\"rejected\",\"error_message\":\"$error_msg\"}"
    queue_api PUT "/api/queue/$qid" "{\"state\":\"failed\",\"error_message\":\"$error_msg\"}" >/dev/null 2>&1
}

# --- Autonomy gate (ADR-0001) ---------------------------------------------
# Fetches the per-project autonomy level from the dashboard API. Runs fall
# back to 'assisted' when the project isn't registered or the API is
# unreachable. Keeps us from auto-opening a PR against an unknown project.
get_project_autonomy() {
    local project="$1"
    local all_projects
    all_projects=$(queue_api GET "/api/projects")
    if [ -z "$all_projects" ]; then
        echo "assisted"
        return 0
    fi
    python3 - "$project" "$all_projects" 2>/dev/null <<'PY' || echo "assisted"
import json, sys
target, payload = sys.argv[1], sys.argv[2]
try:
    for p in json.loads(payload):
        if p.get("repo") == target:
            level = p.get("autonomy_level") or "assisted"
            print(level)
            sys.exit(0)
except Exception:
    pass
print("assisted")
PY
}

# Per-project rate limit for auto-draft PRs. Writes the timestamp of the
# most recent draft-PR attempt to a lock file; returns 0 (allow) if > 1h
# since the last write, 1 (deny) otherwise.
AUTO_DRAFT_RATE_LIMIT_DIR="${AUTO_DRAFT_RATE_LIMIT_DIR:-/var/lib/claude-agent-station/auto-draft}"
AUTO_DRAFT_RATE_LIMIT_SECONDS="${AUTO_DRAFT_RATE_LIMIT_SECONDS:-3600}"

auto_draft_rate_limit_allowed() {
    local project="$1"
    local slug
    slug=$(echo "$project" | tr '/' '_' | tr -cd 'A-Za-z0-9_.-')
    mkdir -p "$AUTO_DRAFT_RATE_LIMIT_DIR" 2>/dev/null || true
    local lock="$AUTO_DRAFT_RATE_LIMIT_DIR/$slug.lock"
    if [ ! -f "$lock" ]; then
        return 0
    fi
    local last_epoch now_epoch
    last_epoch=$(cat "$lock" 2>/dev/null || echo 0)
    now_epoch=$(date +%s)
    if [ $((now_epoch - last_epoch)) -ge "$AUTO_DRAFT_RATE_LIMIT_SECONDS" ]; then
        return 0
    fi
    return 1
}

auto_draft_rate_limit_record() {
    local project="$1"
    local slug
    slug=$(echo "$project" | tr '/' '_' | tr -cd 'A-Za-z0-9_.-')
    mkdir -p "$AUTO_DRAFT_RATE_LIMIT_DIR" 2>/dev/null || true
    date +%s > "$AUTO_DRAFT_RATE_LIMIT_DIR/$slug.lock" 2>/dev/null || true
}

usage() {
    cat << 'EOF'
Manager/Employee Autonomous Agent Orchestrator

Usage: run-manager.sh [OPTIONS]

Options:
  --config FILE    Path to station config (default: ../config/station-config.json)
  --dry-run        Show what would be executed without running
  --list-projects  List configured projects
  --help           Show this help message

The manager orchestrates employee agents across multiple projects:
  1. Pulls latest code for each project
  2. Spawns an employee agent per project (works on issues, commits locally)
  3. Reviews employee work (code quality, completeness, tests)
  4. Approves (push+merge), creates PR, or rejects changes

Configure projects in station-config.json.
EOF
    exit 0
}

# ============================================================================
# RATE LIMIT TRACKING
# ============================================================================

RATE_TRACKING_FILE="/var/log/claude-agent/usage-tracking.json"

CONCURRENT_GROUP_ID=""

trap 'queue_api POST "/api/queue/batch-pause" "{\"run_id\":\"run-$RUN_ID\"}" 2>/dev/null; exit 130' SIGTERM
trap 'queue_api POST "/api/queue/batch-pause" "{\"run_id\":\"run-$RUN_ID\"}" 2>/dev/null; exit 130' SIGINT

# EXIT trap: guarantee run_complete webhook fires on ALL exit paths
# (normal exit, set -e crash, signals). Runs AFTER SIGTERM/SIGINT traps.
# When --internal-iterate is set, RunDriver (Python) owns run_complete; skip.
_send_run_complete_on_exit() {
    local exit_code=$?
    if [ "$INTERNAL_ITERATE" = "true" ]; then
        # RunDriver owns run_complete emission, but bash holds the
        # accumulated token/turn counters and the run-start epoch.
        # Dump them to a known path so Python can fold them into the
        # run_complete payload. Best-effort: the file is read-or-skip
        # on the Python side. See #361.
        if [ -n "${RUN_ID:-}" ]; then
            local _duration_ms=0
            [ "$_RUN_START_EPOCH" -gt 0 ] && _duration_ms=$(( ($(date +%s) - _RUN_START_EPOCH) * 1000 ))
            local _tt=$((_TOTAL_TOKENS_IN + _TOTAL_TOKENS_OUT))
            local _telemetry_file="${LOG_DIR:-/var/log/claude-agent}/run-${RUN_ID}-telemetry.json"
            printf '{"exit_code":%d,"tokens_input":%d,"tokens_output":%d,"tokens_total":%d,"turns":%d,"duration_ms":%d}\n' \
                "$exit_code" "$_TOTAL_TOKENS_IN" "$_TOTAL_TOKENS_OUT" "$_tt" "$_TOTAL_TURNS" "$_duration_ms" \
                > "$_telemetry_file" 2>/dev/null || true
        fi
        return
    fi
    if [ "$_RUN_COMPLETE_SENT" -eq 0 ] && [ -n "${RUN_ID:-}" ]; then
        local status="error"
        [ $exit_code -eq 0 ] && status="completed"
        [ $exit_code -eq 130 ] && status="interrupted"
        local _duration_ms=0
        [ "$_RUN_START_EPOCH" -gt 0 ] && _duration_ms=$(( ($(date +%s) - _RUN_START_EPOCH) * 1000 ))
        webhook_event "run_complete" \
            status "$status" \
            exit_code "$exit_code" \
            tokens_input "$_TOTAL_TOKENS_IN" \
            tokens_output "$_TOTAL_TOKENS_OUT" \
            tokens_total "$((_TOTAL_TOKENS_IN + _TOTAL_TOKENS_OUT))" \
            turns "$_TOTAL_TURNS" \
            duration_ms "$_duration_ms" || true
        _RUN_COMPLETE_SENT=1
    fi
}
trap _send_run_complete_on_exit EXIT

get_max_concurrent() {
    json_get "$CONFIG_FILE" "limits.max_concurrent_employees" 2>/dev/null || echo "1"
}

get_max_per_project() {
    json_get "$CONFIG_FILE" "limits.max_employees_per_project" 2>/dev/null || echo "1"
}

get_budget_strategy() {
    json_get "$CONFIG_FILE" "limits.token_budget_strategy" 2>/dev/null || echo "equal_split"
}

# Calculate per-employee turn budget based on strategy
calculate_employee_budget() {
    local total_employees="$1" project_priority="$2"
    local strategy base_turns

    strategy=$(get_budget_strategy)
    base_turns=$(json_get "$CONFIG_FILE" "limits.max_employee_turns" 2>/dev/null || echo "200")

    python3 -c "
import json, sys

strategy = '$strategy'
base_turns = int($base_turns)
total_employees = int($total_employees)
priority = '$project_priority'

if total_employees <= 1:
    print(base_turns)
    sys.exit(0)

if strategy == 'priority_weighted':
    # Priority-weighted: high gets 40% more, low gets 40% less
    weights = {'critical': 1.6, 'high': 1.3, 'medium': 1.0, 'low': 0.7}
    weight = weights.get(priority, 1.0)
    # Calculate proportional share then apply weight
    share = base_turns / total_employees
    adjusted = int(share * weight)
    # Clamp to reasonable range
    print(max(50, min(adjusted, base_turns)))
elif strategy == 'equal_split':
    # Equal split: each employee gets base_turns / total_employees
    # But floor at 50 turns minimum to be useful
    share = max(50, base_turns // total_employees)
    print(share)
else:
    # Unknown strategy, fall back to equal split
    print(max(50, base_turns // total_employees))
" 2>/dev/null || echo "$base_turns"
}

check_rate_limit() {
    # Single source of truth: query the dashboard's plan-usage API which uses
    # the configured max_usage_percent (cap) and reserve_percent from manager-config.json.
    # This replaces the old session_limit_24h / max_session_percent dual-config system.
    local max_usage_pct
    max_usage_pct=$(json_get "$CONFIG_FILE" "limits.max_usage_percent" 2>/dev/null || echo "80")

    local plan_usage
    plan_usage=$(curl -s --max-time 5 "http://127.0.0.1:8420/api/plan-usage?max_usage_percent=$max_usage_pct" 2>/dev/null || echo "")

    if [ -z "$plan_usage" ]; then
        log_warn "Could not reach dashboard API for rate limit check, proceeding anyway"
        return 0
    fi

    local should_throttle weekly_pct reason
    should_throttle=$(echo "$plan_usage" | python3 -c "import json,sys; print(json.load(sys.stdin).get('should_throttle', False))" 2>/dev/null || echo "False")
    weekly_pct=$(echo "$plan_usage" | python3 -c "import json,sys; print(json.load(sys.stdin).get('weekly_usage_percent', 0))" 2>/dev/null || echo "0")
    reason=$(echo "$plan_usage" | python3 -c "import json,sys; print(json.load(sys.stdin).get('throttle_reason', ''))" 2>/dev/null || echo "")

    if [ "$should_throttle" = "True" ]; then
        log_warn "Plan usage cap reached (${weekly_pct}% of weekly limit, cap ${max_usage_pct}%): $reason"
        return 1
    fi

    log_info "Plan usage: ${weekly_pct}% of weekly limit (cap: ${max_usage_pct}%)"
    return 0
}

record_session() {
    python3 -c "
import json, time, os
tracking_file = '$RATE_TRACKING_FILE'
now = time.time()
state = {'window_start': now, 'sessions_used': 0, 'last_run': now}
if os.path.exists(tracking_file):
    try:
        with open(tracking_file) as f:
            state = json.load(f)
        if now - state.get('window_start', now) >= 86400:
            state = {'window_start': now, 'sessions_used': 0, 'last_run': now}
    except (json.JSONDecodeError, KeyError):
        pass
state['sessions_used'] = state.get('sessions_used', 0) + 1
state['last_run'] = now
os.makedirs(os.path.dirname(tracking_file), exist_ok=True)
with open(tracking_file, 'w') as f:
    json.dump(state, f, indent=2)
" 2>/dev/null || true
}

# Extract token usage from a Claude stream JSONL file
# Outputs: tokens_in tokens_out tokens_total turns
extract_stream_tokens() {
    local stream_file="$1"
    if [ ! -f "$stream_file" ]; then
        echo "0 0 0 0"
        return
    fi
    python3 -c "
import json
ti=to=turns=0
for line in open('$stream_file'):
    try:
        d=json.loads(line)
        if d.get('type')=='result':
            for u in d.get('modelUsage',{}).values():
                ti+=u.get('inputTokens',0); to+=u.get('outputTokens',0)
            turns=d.get('num_turns',0)
    except: pass
print(f'{ti} {to} {ti+to} {turns}')
" 2>/dev/null || echo "0 0 0 0"
}

# check_rate_limit() removed — unified into check_rate_limit() which queries
# the dashboard's /api/plan-usage endpoint using max_usage_percent (cap/reserve).

# ============================================================================
# PREFLIGHT CHECKS
# ============================================================================

preflight() {
    log_info "Running preflight checks..."

    if [ ! -f "$CONFIG_FILE" ]; then
        log_error "Config file not found: $CONFIG_FILE"
        exit 1
    fi
    if [ ! -r "$CONFIG_FILE" ]; then
        log_error "Config file not readable (check permissions): $CONFIG_FILE"
        exit 1
    fi

    for cmd in python3 claude git gh; do
        if ! command -v "$cmd" &>/dev/null; then
            log_error "$cmd is required but not found"
            exit 1
        fi
    done

    # Check authentication (with token expiry validation + auto-refresh)
    if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
        local creds_file="$HOME/.claude/.credentials.json"
        if [ -f "$creds_file" ]; then
            local token_status
            token_status=$(python3 -c "
import json, time
try:
    with open('$creds_file') as f:
        data = json.load(f)
    oauth = data.get('claudeAiOauth', {})
    expires = oauth.get('expiresAt', 0) / 1000
    remaining = expires - time.time()
    if remaining > 600:
        print('valid')
    elif remaining > 0:
        print('expiring_soon')
    else:
        print('expired')
except:
    print('unknown')
" 2>/dev/null || echo "unknown")
            if [ "$token_status" = "expired" ] || [ "$token_status" = "expiring_soon" ]; then
                log_warn "OAuth token ${token_status}. Attempting auto-refresh..."
                local refresh_script="$SCRIPT_DIR/refresh-token.py"
                if [ -f "$refresh_script" ]; then
                    # Use 10-minute threshold: refresh if <600s remaining
                    if REFRESH_THRESHOLD=600 python3 "$refresh_script" 2>&1 | while IFS= read -r line; do log_info "[refresh] $line"; done; then
                        log_ok "OAuth token refreshed successfully"
                    else
                        log_error "OAuth token refresh failed. Re-authenticate or provide ANTHROPIC_API_KEY."
                        notify "auth_failure" "OAuth token refresh failed in run $RUN_ID"
                        exit 1
                    fi
                else
                    log_error "OAuth token ${token_status} and refresh script not found at $refresh_script"
                    notify "auth_failure" "OAuth token ${token_status} in run $RUN_ID"
                    exit 1
                fi
            fi
            log_info "Authentication: OAuth token valid"
        else
            log_error "No authentication found (no credentials file, no ANTHROPIC_API_KEY)"
            exit 1
        fi
    else
        log_info "Authentication: ANTHROPIC_API_KEY"
    fi

    # Setup directories
    LOG_DIR=$(json_get "$CONFIG_FILE" "logging.log_dir" 2>/dev/null || echo "/var/log/claude-agent")
    DIGEST_DIR=$(json_get "$CONFIG_FILE" "logging.digest_dir" 2>/dev/null || echo "$LOG_DIR/digests")
    mkdir -p "$LOG_DIR" "$DIGEST_DIR" "$WORKSPACES_DIR" 2>/dev/null || {
        LOG_DIR="/tmp/claude-agent-logs"
        DIGEST_DIR="$LOG_DIR/digests"
        mkdir -p "$LOG_DIR" "$DIGEST_DIR" "$WORKSPACES_DIR"
        log_warn "Using fallback log dir: $LOG_DIR"
    }

    if ! check_rate_limit; then
        log_error "Rate limit reached. Exiting."
        exit 0
    fi

    log_ok "Preflight checks passed"
}

# ============================================================================
# PROJECT MANAGEMENT
# ============================================================================

get_project_count() {
    python3 -c "
import json
with open('$CONFIG_FILE') as f:
    data = json.load(f)
print(len(data.get('projects', [])))
"
}

get_project_field() {
    local index="$1" field="$2"
    json_get "$CONFIG_FILE" "projects.$index.$field"
}

# Resolve the *effective* mode for a project's run.
#
# The project's static config mode (manager-config.json:projects[i].mode) is
# only authoritative when the orchestrator hasn't drained queue items that
# override it. An approved-plan follow-up run drains queue items with
# mode=full, but the project's configured mode may still be plan_only.
# Without resolving this, the manager review is built against PLAN_REVIEW
# criteria and auto-rejects every implementing teammate as "MODE MISMATCH"
# (run-20260509T183351Z incident).
#
# The orchestrator drops a workspace-local marker file (.claude-run-mode)
# with the run's effective mode. Prefer it; fall back to the static config.
# Garbage in the marker silently falls back too.
#
# Args: $1 = workspace path, $2 = project index in CONFIG_FILE.
resolve_run_mode() {
    local workspace="$1" index="$2"
    local mode=""
    local marker="$workspace/.claude-run-mode"
    if [ -f "$marker" ]; then
        mode=$(head -c 32 "$marker" 2>/dev/null | tr -d '[:space:]')
        case "$mode" in
            full|analyze|plan|plan_only) ;;
            *) mode="" ;;
        esac
    fi
    if [ -z "$mode" ]; then
        mode=$(get_project_field "$index" "mode" 2>/dev/null || echo "full")
    fi
    [ -z "$mode" ] && mode="full"
    printf '%s' "$mode"
}

# Extract repo name from "owner/repo" -> "repo"
repo_name() {
    echo "$1" | cut -d'/' -f2
}

# Ensure workspace exists and is up to date
setup_workspace() {
    local repo="$1"
    local target_branch="${2:-}"  # Optional: project's configured branch
    local name
    name=$(repo_name "$repo")
    local workspace="$WORKSPACES_DIR/$name"

    if [ -d "$workspace/.git" ]; then
        log_info "Resetting workspace for $repo (target branch: ${target_branch:-auto})..." >&2
        cd "$workspace"

        # 1. Remove any lingering worktrees first (they block branch operations)
        git worktree prune 2>/dev/null >&2 || true
        local worktree_pattern="$WORKSPACES_DIR/${name}-e"
        for wt in "$worktree_pattern"*; do
            [ -d "$wt" ] && git worktree remove "$wt" --force 2>/dev/null >&2 || rm -rf "$wt" 2>/dev/null
        done
        git worktree prune 2>/dev/null >&2 || true

        # 2. Discard any dirty state left by previous employees
        git reset --hard HEAD 2>/dev/null >&2 || true
        git clean -fd 2>/dev/null >&2 || true

        # 3. Switch to configured branch, or detect default (main/master)
        local default_branch="${target_branch:-main}"
        if [ -z "$target_branch" ]; then
            if ! git rev-parse --verify main >/dev/null 2>&1; then
                default_branch="master"
            fi
        fi
        git checkout -f "$default_branch" 2>/dev/null >&2 || {
            log_warn "Could not checkout $default_branch for $repo, trying to recover..." >&2
            git checkout -f "$(git remote show origin 2>/dev/null | sed -n 's/.*HEAD branch: //p')" 2>/dev/null >&2 || true
        }

        # 4. Clean up stale branches from previous runs
        git branch --list 'autonomous/*' 'employee-*' 2>/dev/null | tr -d ' *' | while IFS= read -r b; do
            [ -n "$b" ] && git branch -D "$b" 2>/dev/null >&2 || true
        done

        # 5. Pull latest
        git pull origin "$(git branch --show-current)" 2>/dev/null >&2 || {
            log_warn "Pull failed for $repo, continuing with existing state" >&2
        }
    else
        log_info "Cloning $repo..." >&2
        mkdir -p "$WORKSPACES_DIR"
        cd "$WORKSPACES_DIR"
        gh repo clone "$repo" "$name" 2>/dev/null >&2 || {
            log_error "Failed to clone $repo" >&2
            return 1
        }
    fi

    echo "$workspace"
}

# ============================================================================
# PRE-FILTER: Exclude refined/in-progress issues for analyze mode
# ============================================================================

get_analyzable_issues() {
    local repo="$1" workspace="$2"
    cd "$workspace" && gh issue list --repo "$repo" --state open --limit 100 \
        --json number,title,labels \
        | python3 -c "
import json, sys
SKIP = {'autonomous-agent/refined', 'autonomous-agent/in-progress', 'autonomous-agent/needs-help', 'NO AI', 'backlog', 'wontfix'}
issues = json.load(sys.stdin)
filtered = [i for i in issues if not SKIP & {l['name'] for l in i.get('labels', [])}]
print(json.dumps(filtered))
" 2>/dev/null
}

# ============================================================================
# ISSUE ASSIGNMENT (pre-assignment for concurrent employees)
# ============================================================================

assign_work() {
    local repo="$1" project_index="$2" employee_count="$3"
    local name
    name=$(repo_name "$repo")
    local workspace="$WORKSPACES_DIR/$name"

    log_info "Pre-assigning issues for $repo ($employee_count employees)..."

    # Try smart router first if auto_mode_selection is enabled
    local auto_mode
    auto_mode=$(json_get "$CONFIG_FILE" "intelligence.auto_mode_selection" 2>/dev/null || echo "false")
    if [ "$auto_mode" = "true" ]; then
        python3 -m agent.coordinator.smart_router \
            --repo "$repo" --workspace "$workspace" \
            --employee-count "$employee_count" \
            --config "$CONFIG_FILE" --run-id "$RUN_ID" 2>/dev/null && {
            log_info "Smart router produced assignments for $repo"
            return 0
        }
        log_warn "Smart router failed, falling back to Haiku assigner"
    fi

    # Fetch open issues
    local issues_json
    issues_json=$(cd "$workspace" && GITHUB_REPO="$repo" gh issue list --repo "$repo" --state open --limit 30 --json number,title,body,labels,assignees 2>/dev/null) || {
        log_warn "Failed to fetch issues for $repo, employees will self-select"
        return 1
    }

    # Fetch open PRs to avoid duplicating work
    local prs_json
    prs_json=$(cd "$workspace" && GITHUB_REPO="$repo" gh pr list --repo "$repo" --state all --json number,title,headRefName,state 2>/dev/null) || prs_json="[]"

    # Build assignment prompt
    local assignment_prompt="Assign issues from this repository to $employee_count employees.

## Open Issues:
$issues_json

## Open PRs (avoid duplicating these):
$prs_json

## Employee Count: $employee_count

Return ONLY the JSON assignment object, no other text."

    # Run assigner with Haiku (fast + cheap)
    webhook_event "assigner_start" \
        project "$repo" \
        employee_count "$employee_count"
    local assigner_prompt_file="$(resolve_prompt assigner)"
    local assignment_output
    assignment_output=$(echo "$assignment_prompt" | claude -p \
        --system-prompt "$(cat "$assigner_prompt_file")" \
        --model "claude-haiku-4-5-20251001" \
        --max-turns 1 \
        --no-session-persistence \
        --dangerously-skip-permissions \
        --output-format text 2>/dev/null) || {
        webhook_event "assigner_complete" \
            project "$repo" \
            status "failed"
        log_warn "Assignment agent failed for $repo, employees will self-select"
        return 1
    }
    webhook_event "assigner_complete" \
        project "$repo" \
        status "success" \
        employee_count "$employee_count"

    # Extract JSON from output (handle potential markdown wrapping)
    local clean_json
    clean_json=$(echo "$assignment_output" | python3 -c "
import sys, json, re
text = sys.stdin.read()
# Try to extract JSON from markdown code blocks if present
match = re.search(r'\`\`\`(?:json)?\s*(\{.*?\})\s*\`\`\`', text, re.DOTALL)
if match:
    text = match.group(1)
# Validate it's valid JSON
data = json.loads(text.strip())
print(json.dumps(data))
" 2>/dev/null) || {
        log_warn "Failed to parse assignment output for $repo, employees will self-select"
        return 1
    }

    # Write individual assignment files
    local assignments_written=0
    for ((ei = 0; ei < employee_count; ei++)); do
        local assignment_file="$workspace/.claude-assignment-${ei}.json"
        local employee_assignment
        employee_assignment=$(echo "$clean_json" | python3 -c "
import sys, json
data = json.load(sys.stdin)
assignments = data.get('assignments', [])
for a in assignments:
    if a.get('employee_index') == $ei:
        print(json.dumps(a))
        break
" 2>/dev/null) || continue

        if [ -n "$employee_assignment" ] && [ "$employee_assignment" != "null" ]; then
            echo "$employee_assignment" > "$assignment_file"
            local assigned_issue
            assigned_issue=$(echo "$employee_assignment" | python3 -c "import sys,json; print(json.load(sys.stdin).get('issue_number',''))" 2>/dev/null || echo "?")
            log_info "  Employee $ei assigned issue #$assigned_issue"

            # Create queue item for tracking
            local issue_title_q
            issue_title_q=$(echo "$employee_assignment" | python3 -c "import sys,json; t=json.load(sys.stdin).get('issue_title',''); print(t.replace('\"','\\\\\"')[:200])" 2>/dev/null || echo "")
            local ctx_json
            ctx_json=$(echo "$employee_assignment" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)))" 2>/dev/null || echo "{}")
            queue_api POST "/api/queue" "{\"project_repo\":\"$repo\",\"issue_number\":$( [ -n "$assigned_issue" ] && [ "$assigned_issue" != "?" ] && echo "$assigned_issue" || echo "null"),\"issue_title\":\"$issue_title_q\",\"state\":\"assigned\",\"assigned_to\":$ei,\"run_id\":\"run-$RUN_ID\",\"context\":$(echo "$ctx_json" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" 2>/dev/null || echo "\"{}\"")}" >/dev/null 2>&1 &

            # Label issue as in-progress atomically
            (cd "$workspace" && GITHUB_REPO="$repo" gh label create "autonomous-agent/in-progress" --repo "$repo" --color D4C5F9 --description "Being worked on by autonomous agent" --force 2>/dev/null || true)
            (cd "$workspace" && GITHUB_REPO="$repo" gh issue edit "$assigned_issue" --repo "$repo" --add-label "autonomous-agent/in-progress" 2>/dev/null || true)

            assignments_written=$((assignments_written + 1))
        fi
    done

    if [ "$assignments_written" -eq 0 ]; then
        log_warn "No assignments produced for $repo, employees will self-select"
        return 1
    fi

    log_ok "Assigned $assignments_written issues for $repo"
    return 0
}

list_projects() {
    local count
    count=$(get_project_count)
    echo "Configured Projects ($count):"
    echo "============================="
    for ((i = 0; i < count; i++)); do
        local repo priority
        repo=$(get_project_field "$i" "repo")
        priority=$(get_project_field "$i" "priority" 2>/dev/null || echo "medium")
        printf "  %-40s [%s]\n" "$repo" "$priority"
    done
}

# ============================================================================
# EMPLOYEE EXECUTION
# ============================================================================

run_employee() {
    local repo="$1" workspace="$2" project_index="$3"
    local employee_index="${4:-0}"
    local max_turns_override="${5:-}"
    local mode_override="${6:-}"
    local model_override="${7:-}"
    local turns_override="${8:-}"
    local escalation_context_file="${9:-}"
    local name
    name=$(repo_name "$repo")
    local mode
    mode=$(get_project_field "$project_index" "mode" 2>/dev/null || echo "full")
    # Intelligence: apply mode override from decide.py
    if [ -n "$mode_override" ]; then
        log_info "  Intelligence override: mode=$mode_override (was $mode)"
        mode="$mode_override"
    fi
    local custom_instructions
    custom_instructions=$(get_project_field "$project_index" "custom_instructions" 2>/dev/null || echo "")

    # Each employee gets a unique run_id so the dashboard can track them individually.
    # Employee 0 uses the master run_id for backward compatibility; others get a suffix.
    local employee_run_id="run-$RUN_ID"
    if [ "$employee_index" -gt 0 ]; then
        employee_run_id="run-${RUN_ID}-e${employee_index}"
    fi

    log_info "=========================================="
    log_info "EMPLOYEE: $repo (mode: $mode, index: $employee_index, run_id: $employee_run_id)"
    log_info "Workspace: $workspace"
    log_info "=========================================="

    # Use employee-specific webhook to create a distinct Run record per employee
    local _ewh_url
    _ewh_url=$(json_get "$CONFIG_FILE" "dashboard.webhook_url" 2>/dev/null || echo "")
    [ -z "$_ewh_url" ] && _ewh_url="http://127.0.0.1:8420/api/webhook/run-event"
    local _ewh_secret
    _ewh_secret="${STATION_WEBHOOK_SECRET:-$(json_get "$CONFIG_FILE" "dashboard.webhook_secret" 2>/dev/null || echo "")}"
    local -a _ewh_auth=()
    [ -n "$_ewh_secret" ] && _ewh_auth=(-H "X-Webhook-Token: $_ewh_secret")
    local _emp_start_payload
    _emp_start_payload=$(build_webhook_json "employee_start" "$employee_run_id" \
        project "$repo" \
        mode "$mode" \
        employee_index "$employee_index" \
        concurrent_group_id "${CONCURRENT_GROUP_ID:-run-$RUN_ID}")
    curl -s --max-time 3 -X POST "$_ewh_url" \
        -H "Content-Type: application/json" \
        "${_ewh_auth[@]}" \
        -d "$_emp_start_payload" \
        2>/dev/null || true
    # Emit role-specific presence event for planner mode
    if [ "$mode" = "plan" ]; then
        webhook_event "planner_start" \
            project "$repo" \
            employee_index "$employee_index"
    fi

    # Transition queue item to in_progress
    local _qid
    _qid=$(queue_find_item "$repo" "$employee_index")
    if [ -n "$_qid" ]; then
        queue_api PUT "/api/queue/$_qid" "{\"state\":\"in_progress\"}" >/dev/null 2>&1
    fi

    # Run setup script if configured for this project (install dependencies, etc.)
    # Validator + runner live in lib/setup_script.sh (sourced via
    # integration-branch.sh) — see issue #179. Announce the script content
    # only after it passes validation, so a rejected payload never reaches
    # the agent log verbatim.
    local setup_script
    setup_script=$(get_project_field "$project_index" "setup_script" 2>/dev/null || echo "")
    if [ -n "$setup_script" ]; then
        if validate_setup_script "$setup_script"; then
            log_info "Running setup script for $repo: $setup_script"
            cd "$workspace"
            if run_setup_script "$setup_script" "setup($repo)" 2>&1 | tail -20; then
                log_ok "Setup script completed"
            else
                log_warn "Setup script failed (exit $?), continuing anyway"
            fi
        else
            log_warn "setup_script for $repo rejected by validator, skipping"
        fi
    fi

    local model max_turns
    model=$(json_get "$CONFIG_FILE" "models.employee" 2>/dev/null || echo "claude-opus-4-7")
    max_turns=$(json_get "$CONFIG_FILE" "limits.max_employee_turns" 2>/dev/null || echo "200")

    # Apply budget override if provided (from parallel budget calculation)
    if [ -n "$max_turns_override" ]; then
        max_turns="$max_turns_override"
        log_info "  Budget-adjusted turns: $max_turns"
    fi

    # Intelligence: apply model/turns overrides from decide.py
    if [ -n "$model_override" ]; then
        log_info "  Intelligence override: model=$model_override (was $model)"
        model="$model_override"
    fi
    if [ -n "$turns_override" ]; then
        log_info "  Intelligence override: turns=$turns_override (was $max_turns)"
        max_turns="$turns_override"
    fi

    # Mode-specific model and turn overrides from config (with sensible defaults)
    if [ "$mode" = "analyze" ]; then
        model=$(json_get "$CONFIG_FILE" "models.analyst" 2>/dev/null || echo "claude-sonnet-4-6")
        max_turns=$(json_get "$CONFIG_FILE" "limits.max_analyst_turns" 2>/dev/null || echo "50")
    elif [ "$mode" = "plan" ]; then
        model=$(json_get "$CONFIG_FILE" "models.planner" 2>/dev/null || echo "claude-sonnet-4-6")
        max_turns=$(json_get "$CONFIG_FILE" "limits.max_planner_turns" 2>/dev/null || echo "50")
    elif [ "$mode" = "fix" ]; then
        model=$(json_get "$CONFIG_FILE" "models.employee" 2>/dev/null || echo "claude-sonnet-4-6")
        max_turns=$(json_get "$CONFIG_FILE" "limits.max_fix_turns" 2>/dev/null || echo "75")
    elif [ "$mode" = "triage" ]; then
        model=$(json_get "$CONFIG_FILE" "models.analyst" 2>/dev/null || echo "claude-sonnet-4-6")
        max_turns=$(json_get "$CONFIG_FILE" "limits.max_triage_turns" 2>/dev/null || echo "30")
    elif [ "$mode" = "review" ]; then
        model=$(json_get "$CONFIG_FILE" "models.analyst" 2>/dev/null || echo "claude-sonnet-4-6")
        max_turns=$(json_get "$CONFIG_FILE" "limits.max_review_turns" 2>/dev/null || echo "30")
    fi

    # Determine fallback model (must differ from primary)
    local fallback_model="claude-sonnet-4-6"
    if [ "$model" = "claude-sonnet-4-6" ]; then
        fallback_model="claude-haiku-4-5-20251001"
    fi

    # Clean up any previous report
    # Indexed employees use .claude-employee-report-{index}.json
    local report_suffix=""
    if [ "$employee_index" -gt 0 ]; then
        report_suffix="-${employee_index}"
    fi
    rm -f "$workspace/.claude-employee-report${report_suffix}.json"

    # Select prompt based on mode
    local system_prompt employee_prompt
    if [ "$mode" = "plan" ]; then
        system_prompt="$(resolve_prompt planner)"
        employee_prompt="Create implementation plans for the repository: $repo

Environment variables available:
- GITHUB_REPO=$repo
- GH_TOKEN is set

Your workspace is: $workspace

Analyze open issues and create detailed implementation plans. Write plans to the dashboard API at http://127.0.0.1:8420/api/plans. Each plan should include step-by-step instructions, files to change, code snippets, and acceptance criteria.

Write your report to $workspace/.claude-employee-report${report_suffix}.json

Remember: You are in PLAN mode. Read and analyze only — do NOT modify any source files."
    elif [ "$mode" = "analyze" ]; then
        # Programmatic pre-filter: exclude refined/in-progress issues
        local analyzable_issues
        analyzable_issues=$(get_analyzable_issues "$repo" "$workspace" 2>/dev/null || echo "[]")
        local analyzable_count
        analyzable_count=$(echo "$analyzable_issues" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
        local analyzable_summary
        analyzable_summary=$(echo "$analyzable_issues" | python3 -c "
import json, sys
for i in json.load(sys.stdin):
    labels = ', '.join(l['name'] for l in i.get('labels', []))
    print(f\"  #{i['number']} — {i['title']}\" + (f' [{labels}]' if labels else ''))
" 2>/dev/null || echo "  (none)")

        system_prompt="$(resolve_prompt analyst)"
        employee_prompt="Analyze the repository: $repo

Environment variables available:
- GITHUB_REPO=$repo
- GH_TOKEN is set

Your workspace is: $workspace

IMPORTANT — PRE-FILTERED ISSUE LIST ($analyzable_count issues eligible for analysis):
$analyzable_summary

These issues have already been filtered to EXCLUDE issues labeled autonomous-agent/refined,
autonomous-agent/in-progress, autonomous-agent/needs-help, NO AI, backlog, or wontfix.
ONLY work on issues from this list when refining existing issues. Do NOT re-analyze issues
not on this list — they have already been refined in a previous run.

If this list is empty, focus on creating new issues from codebase analysis only.

Write your report to $workspace/.claude-employee-report${report_suffix}.json

Remember: You are in ANALYZE mode. Read and analyze only — do NOT modify any source files."
    elif [ "$mode" = "triage" ]; then
        system_prompt="$(resolve_prompt triager)"
        employee_prompt="Triage issues for the repository: $repo

Environment variables available:
- GITHUB_REPO=$repo
- GH_TOKEN is set

Your workspace is: $workspace

Review open issues and classify them (bug/feature/chore). Assign priority labels, estimate scope, check for duplicates, and link related issues.

Write your report to $workspace/.claude-employee-report${report_suffix}.json

Remember: You are in TRIAGE mode. Read and analyze only — do NOT modify any source files."
    elif [ "$mode" = "review" ]; then
        system_prompt="$(resolve_prompt reviewer)"
        employee_prompt="Review open pull requests for the repository: $repo

Environment variables available:
- GITHUB_REPO=$repo
- GH_TOKEN is set

Your workspace is: $workspace

Review open PRs. For each PR, read the diff and test results ONLY. Post structured review feedback via gh pr review. Never approve or merge — only comment.

Write your report to $workspace/.claude-employee-report${report_suffix}.json

Remember: You are in REVIEW mode. Read and analyze only — do NOT modify any source files."
    else
        system_prompt="$(resolve_prompt employee)"

        # Check if there's an approved plan from the plan-review phase
        local approved_plan_file="$workspace/.claude-approved-plan-${employee_index}.json"
        if [ -f "$approved_plan_file" ]; then
            local aplan_json aplan_issue
            aplan_json=$(cat "$approved_plan_file")
            aplan_issue=$(python3 -c "import json; print(json.load(open('$approved_plan_file')).get('issue_number',''))" 2>/dev/null || echo "")

            log_info "Found approved plan for employee $employee_index (issue #$aplan_issue)"
            employee_prompt="Work on the repository: $repo

Environment variables available:
- GITHUB_REPO=$repo
- GH_TOKEN is set

Your workspace is: $workspace

## APPROVED_PLAN: Implementation Plan (Manager-Approved)

You have a pre-approved implementation plan. Follow it as your guide, but use your judgment
if you discover the plan needs adjustment during implementation.

\`\`\`json
$aplan_json
\`\`\`

Implement each step, write tests, run the full pipeline, and verify everything works.

Write your report to $workspace/.claude-employee-report${report_suffix}.json

Remember: commit locally but NEVER push. The manager will review and push if approved."

            # Keep the approved plan file for cross-reference during manager review
            # It will be cleaned up at the end of the run

        # Check if there's a plan file to implement (legacy dashboard-approved plans)
        elif [ -f "$workspace/.claude-plan-to-implement.json" ]; then
            local plan_file="$workspace/.claude-plan-to-implement.json"
            local plan_title plan_issue plan_description plan_steps
            plan_title=$(python3 -c "import json; print(json.load(open('$plan_file')).get('title',''))" 2>/dev/null || echo "")
            plan_issue=$(python3 -c "import json; print(json.load(open('$plan_file')).get('issue_number',''))" 2>/dev/null || echo "")
            plan_description=$(python3 -c "import json; print(json.load(open('$plan_file')).get('description','')[:2000])" 2>/dev/null || echo "")
            plan_steps=$(python3 -c "import json; print(json.load(open('$plan_file')).get('steps',''))" 2>/dev/null || echo "")

            log_info "Found plan file: $plan_title (issue #$plan_issue)"
            employee_prompt="Work on the repository: $repo

Environment variables available:
- GITHUB_REPO=$repo
- GH_TOKEN is set

Your workspace is: $workspace

## Implementation Plan to Execute

You have a pre-approved implementation plan to follow. Read the plan file at $plan_file for full details.

**Plan**: $plan_title
**Issue**: #$plan_issue

### Plan Description:
$plan_description

### Steps:
$plan_steps

Follow this plan carefully. Implement each step, write tests, and verify everything works.

Write your report to $workspace/.claude-employee-report${report_suffix}.json

Remember: commit locally but NEVER push. The manager will review and push if approved."

            # Clean up plan file after reading (it's been embedded in the prompt)
            rm -f "$plan_file"
        else
            # Check if there's a pre-assignment for this employee
            local assignment_file="$workspace/.claude-assignment-${employee_index}.json"
            if [ -f "$assignment_file" ]; then
                local assign_issue assign_title assign_instructions
                assign_issue=$(python3 -c "import json; print(json.load(open('$assignment_file')).get('issue_number',''))" 2>/dev/null || echo "")
                assign_title=$(python3 -c "import json; print(json.load(open('$assignment_file')).get('issue_title',''))" 2>/dev/null || echo "")
                assign_instructions=$(python3 -c "import json; print(json.load(open('$assignment_file')).get('instructions',''))" 2>/dev/null || echo "")

                log_info "Employee $employee_index has pre-assignment: issue #$assign_issue"

                employee_prompt="Work on the repository: $repo

Environment variables available:
- GITHUB_REPO=$repo
- GH_TOKEN is set

Your workspace is: $workspace

## DIRECTED MODE: Pre-Assigned Issue

You have been assigned a specific issue by the manager. **Skip Step 1 (Find Work)** — go directly to Step 1b (Signal Work) and then Step 2 (Understand the FULL Issue).

**Assigned Issue**: #$assign_issue — $assign_title

**Manager Instructions**: $assign_instructions

Work on issue #$assign_issue. Read it fully (including all comments), implement the solution, run tests, and write your report to $workspace/.claude-employee-report${report_suffix}.json

Remember: commit locally but NEVER push. The manager will review and push if approved."

                # Clean up assignment file after embedding
                rm -f "$assignment_file"
            else
            local multi_employee_note=""
            if [ "$employee_index" -gt 0 ]; then
                multi_employee_note="

IMPORTANT: You are employee #$employee_index working in parallel on this project. Other employees are working on different issues simultaneously. Make sure to pick an issue that is NOT labeled 'autonomous-agent/in-progress'. Each employee must work on a DIFFERENT issue."
            fi
            # Check if there's manager rejection feedback to address
            local feedback_file="$workspace/.claude-manager-feedback.json"
            if [ -f "$feedback_file" ]; then
                local fb_issue fb_branch fb_reasoning fb_feedback fb_missing fb_retry
                fb_issue=$(python3 -c "import json; print(json.load(open('$feedback_file')).get('issue_number',''))" 2>/dev/null || echo "")
                fb_branch=$(python3 -c "import json; print(json.load(open('$feedback_file')).get('branch',''))" 2>/dev/null || echo "")
                fb_reasoning=$(python3 -c "import json; print(json.load(open('$feedback_file')).get('reasoning',''))" 2>/dev/null || echo "")
                fb_feedback=$(python3 -c "import json; print(json.load(open('$feedback_file')).get('feedback_to_employee',''))" 2>/dev/null || echo "")
                fb_missing=$(python3 -c "import json; print(json.dumps(json.load(open('$feedback_file')).get('requirements_missing',[]), indent=2))" 2>/dev/null || echo "[]")
                fb_retry=$(python3 -c "import json; print(json.load(open('$feedback_file')).get('retry_count',1))" 2>/dev/null || echo "1")

                log_info "Employee retry: issue #$fb_issue on branch $fb_branch (attempt $fb_retry)"

                employee_prompt="Work on the repository: $repo

Environment variables available:
- GITHUB_REPO=$repo
- GH_TOKEN is set

Your workspace is: $workspace

## IMPORTANT: Manager Rejected Your Previous Work — Fix Required

Your previous implementation was reviewed by the manager and **REJECTED**. You must fix the issues identified below.

**Issue**: #$fb_issue
**Branch**: $fb_branch (your previous work is still on this branch)
**Retry attempt**: $fb_retry

### Manager's Rejection Reasoning:
$fb_reasoning

### Manager's Feedback to You:
$fb_feedback

### Requirements Still Missing:
$fb_missing

## Instructions:
1. **Checkout your existing branch**: \`git checkout $fb_branch\` — your previous commits are still there.
2. **Read the original issue**: \`gh issue view $fb_issue --repo \$GITHUB_REPO --comments\`
3. **Read the manager's feedback above carefully** — understand exactly what needs to change.
4. **Fix the identified issues** — address every point in the feedback and missing requirements.
5. **Run tests** to verify your fixes work.
6. **Commit your fixes** with a message like: \`fix #$fb_issue: address manager feedback\`
7. **Write your report** to $workspace/.claude-employee-report.json

Remember: commit locally but NEVER push. The manager will review and push if approved."

                # Clean up feedback file after embedding in prompt
                rm -f "$feedback_file"
            else
                employee_prompt="Work on the repository: $repo

Environment variables available:
- GITHUB_REPO=$repo
- GH_TOKEN is set

Your workspace is: $workspace
${multi_employee_note}
Find the most actionable open issue, implement it fully, run tests, and write your report to $workspace/.claude-employee-report${report_suffix}.json

Remember: commit locally but NEVER push. The manager will review and push if approved."
            fi
        fi
        fi  # end plan_file check
    fi

    # Intelligence: append escalation context if this is an escalated run
    if [ -n "$escalation_context_file" ] && [ -f "$escalation_context_file" ]; then
        local esc_ctx
        esc_ctx=$(cat "$escalation_context_file")
        log_info "  Injecting escalation context from $escalation_context_file"
        employee_prompt="$employee_prompt

## ESCALATION_CONTEXT: Previous Attempt Failed — Building On Prior Work

A previous employee attempted this task at a lower capability level and did not succeed.
Use the context below to understand what was tried and what went wrong. Build on the
previous work rather than starting from scratch.

\`\`\`json
$esc_ctx
\`\`\`

Focus on the areas that the previous attempt struggled with. The branch from the prior
attempt may still exist — check if you can continue from it."
        rm -f "$escalation_context_file"
    fi

    # Append custom instructions if configured for this project
    if [ -n "$custom_instructions" ]; then
        employee_prompt="$employee_prompt

## Project-Specific Custom Instructions
$custom_instructions"
    fi

    # Build employee command
    local -a cmd=(claude -p --verbose --output-format stream-json --no-session-persistence --dangerously-skip-permissions)
    cmd+=(--model "$model")
    cmd+=(--fallback-model "$fallback_model")
    cmd+=(--max-turns "$max_turns")
    cmd+=(--system-prompt-file "$system_prompt")
    # Read-only modes: block file-mutation tools at CLI level (defense in depth)
    if [ "$mode" = "analyze" ] || [ "$mode" = "plan" ] || [ "$mode" = "triage" ] || [ "$mode" = "review" ]; then
        cmd+=(--disallowedTools "Edit" "Write" "NotebookEdit")
    fi
    # Full mode: unrestricted tools (dedicated VM, prompt-enforced guardrails)

    log_info "Employee command: ${cmd[*]} '<prompt>'"

    if [ "$DRY_RUN" = true ]; then
        log_info "[DRY RUN] Would run employee for: $repo"
        return 0
    fi

    local idx_suffix=""
    [ "$employee_index" -gt 0 ] && idx_suffix="-${employee_index}"
    local stream_file="$LOG_DIR/run-${RUN_ID}-employee-${name}${idx_suffix}.stream.jsonl"
    local stderr_file="$LOG_DIR/run-${RUN_ID}-employee-${name}${idx_suffix}.stderr.log"
    local exit_code=0

    cd "$workspace"

    # Safety: if employee prompt exceeds 1MB, write to file to avoid ARG_MAX crash
    local prompt_len=${#employee_prompt}
    if [ "$prompt_len" -gt 1048576 ]; then
        local prompt_file="$LOG_DIR/run-${RUN_ID}-employee-${name}${idx_suffix}-prompt.md"
        echo "$employee_prompt" > "$prompt_file"
        log_warn "Employee prompt too long ($prompt_len bytes), wrote to file: $prompt_file"
        employee_prompt="Your full task instructions are in: $prompt_file

Read that file first, then execute the instructions. Your workspace is: $workspace"
    fi

    # Run employee with GITHUB_REPO set for this project
    GITHUB_REPO="$repo" "${cmd[@]}" -- "$employee_prompt" 2>>"$stderr_file" | \
    while IFS= read -r line; do
        echo "$line" >> "$stream_file"
        local etype
        etype=$(echo "$line" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('type',''))" 2>/dev/null || echo "")
        case "$etype" in
            assistant)
                local tool_name
                tool_name=$(echo "$line" | python3 -c "
import json,sys
d=json.load(sys.stdin)
for b in d.get('message',{}).get('content',[]):
    if b.get('type')=='tool_use':
        print(b.get('name',''))
        break
" 2>/dev/null || echo "")
                [ -n "$tool_name" ] && log_info "  Employee -> $tool_name"

                local text_snippet
                text_snippet=$(echo "$line" | python3 -c "
import json,sys
d=json.load(sys.stdin)
for b in d.get('message',{}).get('content',[]):
    if b.get('type')=='text' and b.get('text','').strip():
        t=b['text'].strip().replace('\n',' ')[:120]
        print(t)
        break
" 2>/dev/null || echo "")
                [ -n "$text_snippet" ] && log_info "  Employee: $text_snippet"
                ;;
            result)
                local result_tokens result_turns
                result_tokens=$(echo "$line" | python3 -c "
import json,sys
d=json.load(sys.stdin)
mu=d.get('modelUsage',{})
t=sum(u.get('inputTokens',0)+u.get('outputTokens',0) for u in mu.values())
print(f'{t:,}')
" 2>/dev/null || echo "?")
                result_turns=$(echo "$line" | python3 -c "import json,sys; print(json.load(sys.stdin).get('num_turns',0))" 2>/dev/null || echo "?")
                log_info "  Employee result: Turns=$result_turns Tokens=$result_tokens"
                ;;
        esac
    done

    exit_code=${PIPESTATUS[0]}
    record_session

    # Extract and accumulate token data from this employee's stream
    local _emp_tokens
    _emp_tokens=$(extract_stream_tokens "$stream_file")
    local _et_in _et_out _et_total _et_turns
    read -r _et_in _et_out _et_total _et_turns <<< "$_emp_tokens"
    _TOTAL_TOKENS_IN=$((_TOTAL_TOKENS_IN + _et_in))
    _TOTAL_TOKENS_OUT=$((_TOTAL_TOKENS_OUT + _et_out))
    _TOTAL_TURNS=$((_TOTAL_TURNS + _et_turns))

    if [ $exit_code -eq 0 ]; then
        log_ok "Employee finished: $repo (tokens: $_et_total, turns: $_et_turns)"
    else
        log_warn "Employee exited with code $exit_code: $repo (tokens: $_et_total, turns: $_et_turns)"
    fi

    # Emit planner_complete if this was a plan-mode employee
    if [ "$mode" = "plan" ]; then
        webhook_event "planner_complete" \
            project "$repo" \
            employee_index "$employee_index" \
            exit_code "$exit_code"
    fi

    # Use employee-specific run_id to complete the correct Run record
    local _emp_complete_payload
    _emp_complete_payload=$(build_webhook_json "employee_complete" "$employee_run_id" \
        project "$repo" \
        exit_code "$exit_code" \
        employee_index "$employee_index" \
        concurrent_group_id "${CONCURRENT_GROUP_ID:-run-$RUN_ID}" \
        tokens_input "$_et_in" \
        tokens_output "$_et_out" \
        tokens_total "$_et_total" \
        turns "$_et_turns")
    curl -s --max-time 3 -X POST "$_ewh_url" \
        -H "Content-Type: application/json" \
        "${_ewh_auth[@]}" \
        -d "$_emp_complete_payload" \
        2>/dev/null || true

    # Transition queue item to review
    local _qid2
    _qid2=$(queue_find_item "$repo" "$employee_index")
    if [ -n "$_qid2" ]; then
        # Attach employee report if available
        local report_file="$workspace/.claude-employee-report${report_suffix}.json"
        if [ -f "$report_file" ]; then
            local report_escaped
            report_escaped=$(python3 -c "import sys,json; print(json.dumps(open('$report_file').read()))" 2>/dev/null || echo "null")
            queue_api PUT "/api/queue/$_qid2" "{\"state\":\"review\",\"employee_report\":$report_escaped}" >/dev/null 2>&1
        else
            queue_api PUT "/api/queue/$_qid2" "{\"state\":\"review\"}" >/dev/null 2>&1
        fi
    fi

    return 0  # Don't fail the whole run if one employee errors
}

# ============================================================================
# MANAGER REVIEW
# ============================================================================

collect_employee_reports() {
    local count="$1"
    local review_package="$LOG_DIR/run-${RUN_ID}-review-package.md"

    echo "# Manager Review Package - Run $RUN_ID" > "$review_package"
    echo "" >> "$review_package"
    echo "Review each project below. Write your verdicts to: $LOG_DIR/run-${RUN_ID}-verdicts.json" >> "$review_package"
    echo "" >> "$review_package"

    for ((i = 0; i < count; i++)); do
        local repo
        repo=$(get_project_field "$i" "repo")

        # Skip disabled projects — they weren't worked on by employees
        local enabled_check
        enabled_check=$(get_project_field "$i" "enabled" 2>/dev/null || echo "true")
        if [ "$enabled_check" = "false" ]; then
            continue
        fi

        # Skip projects that passed the confidence gate (auto-PR'd)
        if [ -f "/tmp/claude-agent-auto-pr-${RUN_ID}.list" ]; then
            if grep -qF "$repo" "/tmp/claude-agent-auto-pr-${RUN_ID}.list" 2>/dev/null; then
                continue
            fi
        fi

        local name
        name=$(repo_name "$repo")
        local workspace="$WORKSPACES_DIR/$name"

        echo "---" >> "$review_package"
        echo "## Project: $repo" >> "$review_package"
        echo "" >> "$review_package"

        # Detect the run's effective mode and emit the matching MODE header.
        # The manager prompt (agent/prompts/manager.md:23-33) keys its
        # review-criteria branching off these exact headers — keep them in
        # lockstep with that contract. Issue #266: cover all four modes.
        # See resolve_run_mode for why this prefers a per-run marker over
        # the static project config.
        local project_mode
        project_mode=$(resolve_run_mode "$workspace" "$i")
        if [ "$project_mode" = "analyze" ]; then
            echo "MODE: ANALYZE" >> "$review_package"
            echo "" >> "$review_package"
            echo "### ⚠️ MODE: ANALYZE — No code changes expected" >> "$review_package"
            echo "" >> "$review_package"
            echo "This project is running in **analyze mode**. The employee was instructed to read code and create/refine GitHub issues ONLY — not to make any code changes. **Do NOT reject for absence of code changes.** Review the quality of created/refined issues instead." >> "$review_package"
            echo "" >> "$review_package"
        elif [ "$project_mode" = "plan" ]; then
            echo "MODE: PLAN" >> "$review_package"
            echo "" >> "$review_package"
            echo "### ⚠️ MODE: PLAN — Plan-quality output expected, source must be untouched" >> "$review_package"
            echo "" >> "$review_package"
            echo "This project is running in **plan mode**. The employee was instructed to produce plan-quality output (rich, file:line-referenced plans). Apply **Plan Mode Review** criteria. **Reject** if any source file was modified." >> "$review_package"
            echo "" >> "$review_package"
        elif [ "$project_mode" = "plan_only" ]; then
            echo "MODE: PLAN_REVIEW" >> "$review_package"
            echo "" >> "$review_package"
            echo "### ⚠️ MODE: PLAN_REVIEW — Pre-implementation plan gate" >> "$review_package"
            echo "" >> "$review_package"
            echo "This project is running in **plan_only mode**. The employee wrote an implementation plan and stopped — no code, no branch, no commits. Apply **Plan Review Mode** criteria and verdict APPROVE_PLAN / REVISE_PLAN / REJECT_PLAN. Approve only if the plan covers all issue requirements and the approach is sound." >> "$review_package"
            echo "" >> "$review_package"
        fi

        # Verify no source files were modified in analyze / plan / plan_only modes
        # (defense in depth). plan_only stops before Step 4 — any non-plan-file
        # change is a violation.
        if [ "$project_mode" = "analyze" ] || [ "$project_mode" = "plan" ] || [ "$project_mode" = "plan_only" ]; then
            local dirty_files
            dirty_files=$(cd "$workspace" && { git diff --name-only 2>/dev/null; git ls-files --others --exclude-standard 2>/dev/null; } | grep -v '\.claude-' | grep -v 'node_modules')
            if [ -n "$dirty_files" ]; then
                echo "### ⚠️ READ-ONLY VIOLATION DETECTED" >> "$review_package"
                echo "The following files were modified or created despite analyze/plan mode:" >> "$review_package"
                echo '```' >> "$review_package"
                echo "$dirty_files" >> "$review_package"
                echo '```' >> "$review_package"
                echo "**This employee violated read-only mode. REJECT immediately.**" >> "$review_package"
                echo "" >> "$review_package"
            fi
        fi

        # Find all employee reports (main workspace + worktree paths)
        local report_files=()
        if [ -f "$workspace/.claude-employee-report.json" ]; then
            report_files+=("$workspace/.claude-employee-report.json")
        fi
        for indexed_report in "$workspace"/.claude-employee-report-*.json; do
            [ -f "$indexed_report" ] && report_files+=("$indexed_report")
        done
        # Also check worktree paths (reports may not have been copied yet)
        for wt_dir in "$WORKSPACES_DIR/${name}-e"*; do
            if [ -d "$wt_dir" ]; then
                for wt_report in "$wt_dir"/.claude-employee-report*.json; do
                    if [ -f "$wt_report" ]; then
                        # Avoid duplicates - only add if not already in main workspace
                        local wt_basename
                        wt_basename=$(basename "$wt_report")
                        if [ ! -f "$workspace/$wt_basename" ]; then
                            report_files+=("$wt_report")
                        fi
                    fi
                done
            fi
        done

        if [ ${#report_files[@]} -eq 0 ]; then
            echo "### No employee report found" >> "$review_package"
            echo "Employee did not produce a report file. Check logs." >> "$review_package"
            echo "" >> "$review_package"
            continue
        fi

        local report_idx=0
        for report_file in "${report_files[@]}"; do
            local employee_label="Employee"
            if [ ${#report_files[@]} -gt 1 ]; then
                employee_label="Employee #$report_idx"
            fi

            echo "### ${employee_label} Report" >> "$review_package"
            echo '```json' >> "$review_package"
            cat "$report_file" >> "$review_package"
            echo '```' >> "$review_package"
            echo "" >> "$review_package"

            # Include approved plan if it exists (for cross-reference during review)
            local approved_plan_f="$workspace/.claude-approved-plan-${report_idx}.json"
            if [ -f "$approved_plan_f" ]; then
                echo "### ${employee_label} Approved Plan (for cross-reference)" >> "$review_package"
                echo '```json' >> "$review_package"
                cat "$approved_plan_f" >> "$review_package"
                echo '```' >> "$review_package"
                echo "" >> "$review_package"
            fi

            # Issue #266: surface the plan_only plan file so the manager can
            # actually evaluate it under Plan Review Mode. Without this the
            # review package contains only the report stub and the manager
            # has nothing to score.
            local plan_only_f="$workspace/.claude-employee-plan-${report_idx}.json"
            if [ -f "$plan_only_f" ] && [ "$project_mode" = "plan_only" ]; then
                echo "### ${employee_label} Plan (plan_only mode — review THIS, not a diff)" >> "$review_package"
                echo '```json' >> "$review_package"
                cat "$plan_only_f" >> "$review_package"
                echo '```' >> "$review_package"
                echo "" >> "$review_package"
            fi

            # Issue #266: surface the analyze report file similarly.
            local analyze_f="$workspace/.claude-analyze-report-${report_idx}.json"
            if [ -f "$analyze_f" ] && [ "$project_mode" = "analyze" ]; then
                echo "### ${employee_label} Analyze Report" >> "$review_package"
                echo '```json' >> "$review_package"
                cat "$analyze_f" >> "$review_package"
                echo '```' >> "$review_package"
                echo "" >> "$review_package"
            fi

            # Git diff (main vs current branch)
            cd "$workspace"

            # Detect branch and base_branch from employee report
            local report_branch report_base_branch
            report_branch=$(python3 -c "
import json
with open('$report_file') as f:
    print(json.load(f).get('branch', ''))
" 2>/dev/null || echo "")
            report_base_branch=$(python3 -c "
import json
with open('$report_file') as f:
    print(json.load(f).get('base_branch', ''))
" 2>/dev/null || echo "")
            [ -z "$report_base_branch" ] && report_base_branch="main"

            # Use report's branch if available, else detect from git
            local diff_branch="$report_branch"
            if [ -z "$diff_branch" ]; then
                diff_branch=$(git branch --show-current 2>/dev/null || echo "main")
            fi

            if [ "$diff_branch" != "$report_base_branch" ] && git rev-parse "$diff_branch" 2>/dev/null >/dev/null; then
                echo "### ${employee_label} Git Diff ($report_base_branch..$diff_branch)" >> "$review_package"
                echo '```diff' >> "$review_package"
                git diff "$report_base_branch".."$diff_branch" 2>/dev/null | head -2000 >> "$review_package"
                echo '```' >> "$review_package"
                echo "" >> "$review_package"

                echo "### ${employee_label} Git Log" >> "$review_package"
                echo '```' >> "$review_package"
                git log "$report_base_branch".."$diff_branch" --oneline 2>/dev/null >> "$review_package"
                echo '```' >> "$review_package"
                echo "" >> "$review_package"

                # Include the original issue for completeness check
                local issue_number
                issue_number=$(python3 -c "
import json
with open('$report_file') as f:
    print(json.load(f).get('issue_number', ''))
" 2>/dev/null || echo "")
                if [ -n "$issue_number" ]; then
                    echo "### Original Issue #$issue_number (with comments)" >> "$review_package"
                    echo '```' >> "$review_package"
                    GITHUB_REPO="$repo" gh issue view "$issue_number" --repo "$repo" --comments 2>/dev/null >> "$review_package" || echo "(could not fetch issue)" >> "$review_package"
                    echo '```' >> "$review_package"
                    echo "" >> "$review_package"
                fi
            else
                # Detect mode from employee report as fallback
                local report_mode
                report_mode=$(python3 -c "
import json
with open('$report_file') as f:
    print(json.load(f).get('mode', ''))
" 2>/dev/null || echo "")

                if [ "$project_mode" = "analyze" ] || [ "$report_mode" = "analyze" ]; then
                    echo "### ${employee_label}: Analyze mode — no code changes expected (this is correct behavior)" >> "$review_package"
                elif [ "$project_mode" = "plan_only" ] || [ "$report_mode" = "plan_only" ]; then
                    echo "### ${employee_label}: Plan-only mode — plan written, no code changes expected (this is correct behavior)" >> "$review_package"
                elif [ "$project_mode" = "plan" ] || [ "$report_mode" = "plan" ]; then
                    echo "### ${employee_label}: Plan mode — no code changes expected (review plan-quality output)" >> "$review_package"
                else
                    echo "### ${employee_label}: No changes (employee stayed on $report_base_branch)" >> "$review_package"
                fi
                echo "" >> "$review_package"
            fi

            report_idx=$((report_idx + 1))
        done
    done

    echo "$review_package"
}

run_manager_review() {
    local review_package="$1"
    local verdicts_file="$LOG_DIR/run-${RUN_ID}-verdicts.json"

    log_info "==========================================" >&2
    log_info "MANAGER: Reviewing employee work" >&2
    log_info "==========================================" >&2

    # Issue #266: if any project is plan_only, also emit plan_review_start so
    # the dashboard banner reflects the plan_reviewing state during this
    # window. The standard manager_review event still fires for full-mode
    # consumers; the two are additive.
    local _has_plan_only="false"
    local _project_count
    _project_count=$(json_get "$CONFIG_FILE" "projects" 2>/dev/null | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
    for ((_pi = 0; _pi < _project_count; _pi++)); do
        local _pm
        _pm=$(get_project_field "$_pi" "mode" 2>/dev/null || echo "full")
        if [ "$_pm" = "plan_only" ]; then
            _has_plan_only="true"
            break
        fi
    done
    if [ "$_has_plan_only" = "true" ]; then
        webhook_event "plan_review_start" review_package "$review_package" >&2
    fi

    webhook_event "manager_review" review_package "$review_package" >&2

    local model max_turns
    model=$(json_get "$CONFIG_FILE" "models.manager" 2>/dev/null || echo "claude-sonnet-4-6")
    max_turns=$(json_get "$CONFIG_FILE" "limits.max_manager_turns" 2>/dev/null || echo "30")

    # Determine fallback model for manager
    local manager_fallback="claude-haiku-4-5-20251001"
    if [ "$model" = "claude-haiku-4-5-20251001" ]; then
        manager_fallback="claude-sonnet-4-6"
    fi

    local -a cmd=(claude -p --verbose --output-format stream-json --no-session-persistence --dangerously-skip-permissions)
    cmd+=(--model "$model")
    cmd+=(--fallback-model "$manager_fallback")
    cmd+=(--max-turns "$max_turns")
    cmd+=(--system-prompt-file "$(resolve_prompt manager)")
    # No --allowedTools: full VM access, prompt-enforced guardrails

    # Reference the review package file instead of inlining it (avoids ARG_MAX crash on large diffs)
    # The turn budget is injected so the system prompt's tool-budget rules
    # can stay generic and stay in sync with limits.max_manager_turns.
    local _half_budget=$(( max_turns / 2 ))
    [ "$_half_budget" -lt 5 ] && _half_budget=5
    local manager_prompt="Review the employee work package at: $review_package

Write your verdicts to: $verdicts_file

Your hard turn budget for this review is $max_turns. Treat turn $_half_budget as your soft deadline to start drafting the verdicts file — see the <tool-budget> section of your system prompt for how to spend the budget.

Read the review package file first, then evaluate each project's work against the criteria in your system prompt. Be strict on completeness — never approve partial implementations."

    log_info "Manager command: ${cmd[*]} '<prompt>'" >&2

    if [ "$DRY_RUN" = true ]; then
        log_info "[DRY RUN] Would run manager review" >&2
        return 0
    fi

    # Heartbeat: the bash does not parse the manager stream (it is streamed
    # to a file). Without a side-channel ping, the launcher's _zombie_reaper
    # (120s timeout) and the dashboard's stale_run_reaper kill an otherwise-
    # healthy manager review around the 2-minute mark. See issue #376.
    local _heartbeat_pid=""
    (
        # Subshell suppresses all output so it cannot pollute the function's
        # captured-output return value (the verdicts_file path echoed below).
        while sleep 30; do
            webhook_event "manager_heartbeat" phase "manager_review" >/dev/null 2>&1 || true
        done
    ) >/dev/null 2>&1 &
    _heartbeat_pid=$!
    # Function-scoped RETURN trap fires on every return path (success,
    # set -e crash, signal). No existing RETURN trap to clobber.
    trap 'kill "$_heartbeat_pid" 2>/dev/null || true; trap - RETURN' RETURN

    local stream_file="$LOG_DIR/run-${RUN_ID}-manager.stream.jsonl"
    local stderr_file="$LOG_DIR/run-${RUN_ID}-manager.stderr.log"

    cd "$WORKSPACES_DIR"

    "${cmd[@]}" -- "$manager_prompt" 2>>"$stderr_file" | \
    while IFS= read -r line; do
        echo "$line" >> "$stream_file"
        local etype
        etype=$(echo "$line" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('type',''))" 2>/dev/null || echo "")
        case "$etype" in
            assistant)
                local text_snippet
                text_snippet=$(echo "$line" | python3 -c "
import json,sys
d=json.load(sys.stdin)
for b in d.get('message',{}).get('content',[]):
    if b.get('type')=='text' and b.get('text','').strip():
        t=b['text'].strip().replace('\n',' ')[:120]
        print(t)
        break
" 2>/dev/null || echo "")
                [ -n "$text_snippet" ] && log_info "  Manager: $text_snippet" >&2
                ;;
            result)
                local result_tokens_mgr
                result_tokens_mgr=$(echo "$line" | python3 -c "
import json,sys
d=json.load(sys.stdin)
mu=d.get('modelUsage',{})
t=sum(u.get('inputTokens',0)+u.get('outputTokens',0) for u in mu.values())
print(f'{t:,}')
" 2>/dev/null || echo "?")
                log_info "  Manager review tokens: $result_tokens_mgr" >&2
                ;;
        esac
    done

    record_session

    # Extract and accumulate manager tokens
    local _mgr_tokens
    _mgr_tokens=$(extract_stream_tokens "$stream_file")
    local _mt_in _mt_out _mt_total _mt_turns
    read -r _mt_in _mt_out _mt_total _mt_turns <<< "$_mgr_tokens"
    _TOTAL_TOKENS_IN=$((_TOTAL_TOKENS_IN + _mt_in))
    _TOTAL_TOKENS_OUT=$((_TOTAL_TOKENS_OUT + _mt_out))
    _TOTAL_TURNS=$((_TOTAL_TURNS + _mt_turns))
    log_info "  Manager tokens accumulated: $_mt_total" >&2

    echo "$verdicts_file"
}

# ============================================================================
# VERDICT EXECUTION
# ============================================================================

execute_verdicts() {
    local verdicts_file="$1"
    local had_integration_merges=false

    if [ ! -f "$verdicts_file" ]; then
        log_error "No verdicts file found: $verdicts_file"
        notify "failure" "Manager did not produce verdicts in run $RUN_ID"
        return 1
    fi

    log_info "=========================================="
    log_info "EXECUTING VERDICTS"
    log_info "=========================================="

    local verdict_count
    verdict_count=$(python3 -c "
import json
with open('$verdicts_file') as f:
    data = json.load(f)
print(len(data.get('verdicts', [])))
" 2>/dev/null || echo "0")

    if [ "$verdict_count" -eq 0 ]; then
        log_warn "No verdicts to execute"
        return 0
    fi

    for ((v = 0; v < verdict_count; v++)); do
        local verdict_json
        verdict_json=$(python3 -c "
import json
with open('$verdicts_file') as f:
    data = json.load(f)
v = data['verdicts'][$v]
print(json.dumps(v))
" 2>/dev/null)

        local project verdict branch issue_number reasoning base_branch verdict_mode
        project=$(echo "$verdict_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('project',''))")
        verdict=$(echo "$verdict_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('verdict','REJECT'))")
        branch=$(echo "$verdict_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('branch',''))")
        issue_number=$(echo "$verdict_json" | python3 -c "import json,sys; v=json.load(sys.stdin).get('issue_number'); print('' if v is None else v)")
        # Plural list — present on synthesized employee reports (PR #332)
        # carrying multi-issue runs. Empty when verdicts only carry the
        # singular field. format_close_keywords prefers plural over singular.
        issue_numbers_json=$(echo "$verdict_json" | python3 -c "import json,sys; v=json.load(sys.stdin).get('issue_numbers'); print('' if v is None else json.dumps(v))" 2>/dev/null || echo "")
        reasoning=$(echo "$verdict_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('reasoning',''))")
        base_branch=$(echo "$verdict_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('base_branch','main'))")
        verdict_mode=$(echo "$verdict_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('mode',''))" 2>/dev/null || echo "")

        # When the integration branch is enabled, every autonomous PR
        # targets the dev branch instead of the project's underlying base
        # (typically main). The dev → main hop is handled by the
        # promotion flow below, so verdict.base_branch stays as the
        # promote-to target and pr_base_branch carries the actual PR target.
        local pr_base_branch="$base_branch"
        if integration_enabled; then
            local _dev
            _dev=$(get_dev_branch)
            if [ -n "$_dev" ]; then
                pr_base_branch="$_dev"
            fi
        fi

        local name
        name=$(repo_name "$project")
        local workspace="$WORKSPACES_DIR/$name"

        log_info "Project: $project | Verdict: $verdict | Issue: #$issue_number | Branch: $branch"
        log_info "Reasoning: $reasoning"

        # build_webhook_json handles string escaping (json.dumps); pass the
        # reasoning verbatim. Use literal "null" for missing issue_number so
        # the helper coerces it to a JSON null.
        local issue_num_json="null"
        [ -n "$issue_number" ] && issue_num_json="$issue_number"
        webhook_event "verdict_execute" \
            project "$project" \
            verdict "$verdict" \
            issue_number "$issue_num_json" \
            branch "$branch" \
            reasoning "$reasoning"

        # Intelligence: record outcome for the learning loop
        local _report_confidence="" _report_tokens="" _report_duration="" _report_complexity="" _report_mode_used="" _report_model_used="" _report_esc_rung="0"
        if [ -f "$workspace/.claude-employee-report.json" ]; then
            _report_confidence=$(python3 -c "import json; print(json.load(open('$workspace/.claude-employee-report.json')).get('confidence',''))" 2>/dev/null || echo "")
            _report_tokens=$(python3 -c "import json; print(json.load(open('$workspace/.claude-employee-report.json')).get('tokens_total',''))" 2>/dev/null || echo "")
            _report_duration=$(python3 -c "import json; print(json.load(open('$workspace/.claude-employee-report.json')).get('duration_seconds',''))" 2>/dev/null || echo "")
            _report_complexity=$(python3 -c "import json; print(json.load(open('$workspace/.claude-employee-report.json')).get('complexity_score',''))" 2>/dev/null || echo "")
            _report_mode_used=$(python3 -c "import json; print(json.load(open('$workspace/.claude-employee-report.json')).get('mode',''))" 2>/dev/null || echo "")
            _report_model_used=$(python3 -c "import json; print(json.load(open('$workspace/.claude-employee-report.json')).get('model',''))" 2>/dev/null || echo "")
            _report_esc_rung=$(python3 -c "import json; print(json.load(open('$workspace/.claude-employee-report.json')).get('escalation_rung',0))" 2>/dev/null || echo "0")
        fi

        # Extract issue_type and subsystem from labels for the learning loop
        local _issue_type="" _subsystem=""
        if [ -f "$workspace/.claude-assignment-0.json" ]; then
            _issue_type=$(python3 -c "
import json, sys
a = json.load(open('$workspace/.claude-assignment-0.json'))
labels = [l.get('name','') if isinstance(l,dict) else str(l) for l in a.get('labels',[])]
for n in labels:
    if n in ('bug','fix','hotfix'): print('bug'); sys.exit()
    elif n in ('enhancement','feature'): print('feature'); sys.exit()
    elif n in ('chore','maintenance','docs'): print('chore'); sys.exit()
    elif n in ('refactor','tech-debt'): print('refactor'); sys.exit()
print('feature')
" 2>/dev/null || echo "")
            _subsystem=$(python3 -c "
import json
a = json.load(open('$workspace/.claude-assignment-0.json'))
body = a.get('body','') or ''
title = a.get('issue_title','') or ''
text = title + ' ' + body
scores = {'frontend':0,'backend':0,'agent':0,'infra':0}
for pat in ['dashboard/frontend/','src/lib/','.svelte','.tsx','.css']:
    if pat in text: scores['frontend'] += 1
for pat in ['dashboard/backend/','app/routers/','app/models','.py']:
    if pat in text: scores['backend'] += 1
for pat in ['agent/scripts/','agent/prompts/','agent/coordinator/','run-manager','run-employee']:
    if pat in text: scores['agent'] += 1
for pat in ['systemd','.service','Dockerfile','docker-compose','nginx','.conf']:
    if pat in text: scores['infra'] += 1
best = max(scores, key=scores.get)
print(best if scores[best] > 0 else 'mixed')
" 2>/dev/null || echo "")
        fi
        python3 -m agent.coordinator.decide --run-id "$RUN_ID" \
            record-outcome \
            --project-repo "$project" \
            --issue-number "${issue_number:-}" \
            --mode "${_report_mode_used:-${verdict_mode:-full}}" \
            --model "${_report_model_used:-}" \
            --verdict "$verdict" \
            --confidence "${_report_confidence:-}" \
            --tokens "${_report_tokens:-}" \
            --duration "${_report_duration:-}" \
            --complexity "${_report_complexity:-}" \
            --escalation-rung "${_report_esc_rung:-0}" \
            --issue-type "${_issue_type:-}" \
            --subsystem "${_subsystem:-}" \
            >/dev/null 2>&1 &

        # Redundant task outcome recording via HTTP API (fallback if decide.py fails)
        local _outcome_success="false"
        [[ "$verdict" == "APPROVE" || "$verdict" == "PR" ]] && _outcome_success="true"
        local _oi="${issue_number:-}"
        [[ -z "$_oi" || "$_oi" == "None" || "$_oi" == "null" ]] && _oi=""
        local _outcome_json
        _outcome_json=$(python3 -c "
import json
def intval(s):
    try: return int(s)
    except: return None
d = {
    'project_repo': '$project',
    'issue_number': intval('$_oi'),
    'mode_used': '${_report_mode_used:-${verdict_mode:-full}}',
    'model_used': '${_report_model_used:-claude-sonnet-4-6}',
    'success': '$_outcome_success' == 'true',
    'verdict': '$verdict',
    'failure_category': '$verdict',
    'employee_index': intval('${employee_idx:-}'),
    'escalation_rung': intval('${_report_esc_rung:-0}') or 0,
    'complexity_score': intval('${_report_complexity:-}'),
    'tokens_consumed': intval('${_report_tokens:-}'),
    'duration_seconds': intval('${_report_duration:-}'),
    'issue_type': '${_issue_type:-}' or None,
    'subsystem': '${_subsystem:-}' or None,
}
print(json.dumps(d))
" 2>/dev/null) && \
        queue_api POST "/api/intelligence/outcomes" "$_outcome_json" >/dev/null 2>&1 &

        if [ ! -d "$workspace/.git" ]; then
            log_error "Workspace not found: $workspace"
            continue
        fi

        cd "$workspace"

        # Detect analyze mode from verdict, employee report, or project config
        local is_analyze_mode=false
        if [ "$verdict_mode" = "analyze" ]; then
            is_analyze_mode=true
        elif [ -f "$workspace/.claude-employee-report.json" ]; then
            local report_mode_check
            report_mode_check=$(python3 -c "import json; print(json.load(open('$workspace/.claude-employee-report.json')).get('mode',''))" 2>/dev/null || echo "")
            [ "$report_mode_check" = "analyze" ] && is_analyze_mode=true
        fi

        # Handle analyze-mode verdicts — no push/merge needed
        if [ "$is_analyze_mode" = true ]; then
            log_info "Analyze mode detected — skipping push/merge operations"

            if [ "$verdict" = "APPROVE" ]; then
                log_ok "APPROVE (analyze mode): Analysis work accepted"

                if [ -n "$issue_number" ] && [ "$issue_number" != "None" ] && [ "$issue_number" != "null" ]; then
                    gh issue comment "$issue_number" --repo "$project" --body "## Analyze Mode Review: APPROVED

$reasoning

Analysis produced useful issues. No code changes expected (analyze mode).

---
Autonomous run: $RUN_ID" 2>/dev/null || log_warn "Failed to comment on issue #$issue_number"

                    gh issue close "$issue_number" --repo "$project" --reason completed 2>/dev/null || true
                    gh issue edit "$issue_number" --repo "$project" --remove-label "autonomous-agent/done" 2>/dev/null || true
                    gh issue edit "$issue_number" --repo "$project" --remove-label "autonomous-agent/in-progress" 2>/dev/null || true
                fi

                notify "approve" "APPROVED (analyze): $project #$issue_number - $reasoning"
            elif [ "$verdict" = "REJECT" ]; then
                log_info "REJECT (analyze mode): Analysis work rejected"

                if [ -n "$issue_number" ] && [ "$issue_number" != "None" ] && [ "$issue_number" != "null" ]; then
                    gh issue comment "$issue_number" --repo "$project" --body "🤖 **Manager verdict: REJECTED (analyze mode)** — $reasoning. Will retry next cycle.

Run: $RUN_ID" 2>/dev/null || true
                    gh issue edit "$issue_number" --repo "$project" --remove-label "autonomous-agent/done" 2>/dev/null || true
                    gh issue edit "$issue_number" --repo "$project" --remove-label "autonomous-agent/in-progress" 2>/dev/null || true
                fi

                notify "reject" "REJECTED (analyze): $project #$issue_number - $reasoning"
            else
                log_info "$verdict (analyze mode): $reasoning"
            fi

            # Queue state update for analyze mode
            local _aqid
            _aqid=$(queue_api GET "/api/queue?project_repo=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$project'))" 2>/dev/null)&run_id=run-$RUN_ID&limit=1" | python3 -c "import json,sys; items=json.load(sys.stdin).get('items',[]); print(items[0]['id'] if items else '')" 2>/dev/null || echo "")
            if [ -n "$_aqid" ]; then
                if [ "$verdict" = "APPROVE" ]; then
                    queue_complete_item "$_aqid"
                else
                    queue_reject_item "$_aqid" '{"state":"rejected"}'
                fi
            fi

            # Return to base branch and continue to next verdict
            git checkout "$pr_base_branch" 2>/dev/null || git checkout "$base_branch" 2>/dev/null || true
            continue
        fi

        case "$verdict" in
            APPROVE)
                log_info "APPROVE: Processing verdict (pr_base: $pr_base_branch, promote_to: $base_branch)"

                if integration_enabled; then
                    # Integration branch mode: merge to dev, don't close issue
                    merge_to_dev "$project" "$branch" "$base_branch" "$issue_number" "$reasoning"
                    local merge_status=$?

                    if [ "$merge_status" -eq 0 ]; then
                        notify "approve" "APPROVED & merged to dev: $project #$issue_number"
                        had_integration_merges=true
                    else
                        notify "error" "APPROVE merge to dev failed: $project #$issue_number"
                    fi

                    # Queue: walk to completed (feature is on dev)
                    local _vqid
                    _vqid=$(queue_api GET "/api/queue?project_repo=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$project'))" 2>/dev/null)&run_id=run-$RUN_ID&limit=1" | python3 -c "import json,sys; items=json.load(sys.stdin).get('items',[]); print(items[0]['id'] if items else '')" 2>/dev/null || echo "")
                    if [ -n "$_vqid" ]; then
                        queue_complete_item "$_vqid"
                    fi
                else
                    # Original behavior: push → PR → merge → close issue
                    local push_merge_ok=false

                    # Ensure we're on the feature branch
                    git checkout "$branch" 2>&1 | while IFS= read -r line; do log_info "  $line"; done || true

                    # Push with retry (2 attempts)
                    local push_ok=false
                    for attempt in 1 2; do
                        if git push -u origin "$branch" 2>&1 | while IFS= read -r line; do log_info "  $line"; done; then
                            push_ok=true
                            break
                        fi
                        [ "$attempt" -lt 2 ] && sleep 3
                    done

                    if [ "$push_ok" = true ]; then
                        log_ok "Pushed $branch"

                        # ADR-0001: under 'auto' autonomy, open a DRAFT PR and
                        # skip the auto-merge attempt. Under manual/assisted,
                        # fall through to the existing open-PR-then-try-merge
                        # path (branch protection on main rejects the merge
                        # anyway; this just preserves existing logging).
                        local project_autonomy
                        project_autonomy=$(get_project_autonomy "$project")
                        local autonomy_auto=false
                        if [ "$project_autonomy" = "auto" ]; then
                            autonomy_auto=true
                        fi

                        if [ "$autonomy_auto" = true ]; then
                            if auto_draft_rate_limit_allowed "$project"; then
                                log_info "Auto-draft PR (autonomy=auto, rate limit OK)"
                                local pr_url close_line
                                close_line=$(format_close_keywords "$issue_number" "$issue_numbers_json")
                                rebase_against_base "$workspace" "$branch" "$pr_base_branch" "$project" "" "$RUN_ID" "pre_pr" || true
                                pr_url=$(gh pr create --repo "$project" --base "$pr_base_branch" --head "$branch" \
                                    --draft \
                                    --title "autonomous (draft): $(git log -1 --format=%s)" \
                                    --body "Draft PR auto-opened under \`autonomy=auto\`.

Run: $RUN_ID
${close_line:+
$close_line
}
**Human review required before merge.** This PR stays as a draft — mark it ready for review when satisfied.

---
Rate limit: max 1 auto-draft PR per project per hour. Regenerated at $(date -u +%Y-%m-%dT%H:%M:%SZ)." 2>&1) || true

                                if [ -n "$pr_url" ]; then
                                    log_ok "Draft PR created: $pr_url"
                                    auto_draft_rate_limit_record "$project"
                                    webhook_event "auto_draft_pr_opened" \
                                        project "$project" \
                                        branch "$branch" \
                                        pr_url "$pr_url" >&2
                                else
                                    log_error "Auto-draft PR creation failed for $branch"
                                fi
                            else
                                log_warn "Auto-draft PR skipped (rate limit: 1/hour/project); branch $branch pushed for manual review"
                            fi
                        else
                            # Create PR and merge via GitHub API (works with protected branches)
                            local pr_url close_line
                            close_line=$(format_close_keywords "$issue_number" "$issue_numbers_json")
                            rebase_against_base "$workspace" "$branch" "$pr_base_branch" "$project" "" "$RUN_ID" "pre_pr" || true
                            pr_url=$(gh pr create --repo "$project" --base "$pr_base_branch" --head "$branch" \
                                --title "autonomous: $(git log -1 --format=%s)" \
                                --body "Approved by autonomous manager.

Run: $RUN_ID${close_line:+

$close_line}" 2>&1) || true

                            if [ -n "$pr_url" ]; then
                                log_info "PR created: $pr_url"
                                # Merge the PR
                                if gh pr merge "$pr_url" --merge --delete-branch 2>&1 | while IFS= read -r line; do log_info "  $line"; done; then
                                    push_merge_ok=true
                                    log_ok "PR merged to $base_branch"
                                else
                                    log_warn "PR merge failed for $pr_url — attempting at-merge resolution"
                                    local pr_num_for_resolve _script_dir
                                    pr_num_for_resolve=$(echo "$pr_url" | grep -oE '[0-9]+$' || echo "")
                                    _script_dir="$(dirname "${BASH_SOURCE[0]}")"
                                    # Source helpers once for both branches below (review finding #9).
                                    # shellcheck source=lib/conflict-helpers.sh
                                    source "$_script_dir/lib/conflict-helpers.sh"
                                    set +e
                                    rebase_against_base "$workspace" "$branch" "$pr_base_branch" "$project" "$pr_num_for_resolve" "$RUN_ID" "at_merge"
                                    local resolve_rc=$?
                                    set -e
                                    if [ "$resolve_rc" = "0" ]; then
                                        log_info "Resolution succeeded; retrying merge"
                                        if gh pr merge "$pr_url" --merge --delete-branch 2>&1 | while IFS= read -r line; do log_info "  $line"; done; then
                                            push_merge_ok=true
                                            log_ok "PR merged to $pr_base_branch (after at-merge resolution)"
                                        else
                                            log_error "PR merge still failed after resolution — left open: $pr_url"
                                            post_resolution_outcome 1 "$project" "$pr_num_for_resolve" "$branch"
                                        fi
                                    else
                                        log_error "Resolution failed (rc=$resolve_rc) — left open for manual review: $pr_url"
                                        post_resolution_outcome "$resolve_rc" "$project" "$pr_num_for_resolve" "$branch"
                                    fi
                                fi
                            else
                                log_error "PR creation failed for $branch"
                            fi
                        fi
                    else
                        log_error "Push failed for $branch after 2 attempts"
                    fi

                    # Close issue with documentation (after push/merge)
                    if [ "$push_merge_ok" = true ] && [ -n "$issue_number" ] && [ "$issue_number" != "None" ] && [ "$issue_number" != "null" ]; then
                        local feedback
                        feedback=$(echo "$verdict_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('feedback_to_employee','Good work.'))")
                        gh issue comment "$issue_number" --repo "$project" --body "## Completed by Autonomous Agent

### Manager Review: APPROVED

$reasoning

### Employee Feedback
$feedback

Branch \`$branch\` merged to \`$base_branch\`.

---
Autonomous run: $RUN_ID" 2>/dev/null || log_warn "Failed to comment on issue #$issue_number"

                        if gh issue close "$issue_number" --repo "$project" --reason completed 2>&1; then
                            log_ok "Issue #$issue_number closed"
                        else
                            log_error "Failed to close issue #$issue_number, retrying..."
                            sleep 2
                            if gh issue close "$issue_number" --repo "$project" --reason completed 2>&1; then
                                log_ok "Issue #$issue_number closed (retry succeeded)"
                            else
                                log_error "Failed to close issue #$issue_number after retry"
                            fi
                        fi

                        # Clean up agent labels
                        gh issue edit "$issue_number" --repo "$project" --remove-label "autonomous-agent/done" 2>/dev/null || true
                        gh issue edit "$issue_number" --repo "$project" --remove-label "autonomous-agent/in-progress" 2>/dev/null || true
                    fi

                    notify "approve" "APPROVED: $project #$issue_number - $reasoning"

                    # Queue: walk to completed
                    local _vqid
                    _vqid=$(queue_api GET "/api/queue?project_repo=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$project'))" 2>/dev/null)&run_id=run-$RUN_ID&limit=1" | python3 -c "import json,sys; items=json.load(sys.stdin).get('items',[]); print(items[0]['id'] if items else '')" 2>/dev/null || echo "")
                    if [ -n "$_vqid" ]; then
                        queue_complete_item "$_vqid"
                    fi
                fi
                ;;

            PR)
                log_info "PR: Pushing branch and creating PR for human review (base: $pr_base_branch)"
                rebase_against_base "$workspace" "$branch" "$pr_base_branch" "$project" "" "$RUN_ID" "pre_pr" || true
                if git push origin "$branch" 2>/dev/null; then
                    log_ok "Pushed $branch"
                    local close_line
                    close_line=$(format_close_keywords "$issue_number" "$issue_numbers_json")
                    gh pr create --repo "$project" --base "$pr_base_branch" \
                        --title "autonomous: $(git log -1 --format=%s)" \
                        --body "## Needs Human Review

**Manager reasoning**: $reasoning${close_line:+

$close_line}

---
Autonomous run: $RUN_ID" 2>/dev/null && log_ok "PR created" || log_warn "PR creation failed"

                    if [ -n "$issue_number" ] && [ "$issue_number" != "None" ] && [ "$issue_number" != "null" ]; then
                        gh issue comment "$issue_number" --repo "$project" --body "PR created for human review. Manager notes: $reasoning

Run: $RUN_ID" 2>/dev/null || true
                        # Clean up agent labels
                        gh issue edit "$issue_number" --repo "$project" --remove-label "autonomous-agent/done" 2>/dev/null || true
                        gh issue edit "$issue_number" --repo "$project" --remove-label "autonomous-agent/in-progress" 2>/dev/null || true
                    fi
                else
                    log_error "Push failed for $branch"
                fi

                notify "pr" "PR: $project #$issue_number - $reasoning"

                # Queue: walk to completed (PR is also a terminal success)
                local _prqid
                _prqid=$(queue_api GET "/api/queue?project_repo=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$project'))" 2>/dev/null)&run_id=run-$RUN_ID&limit=1" | python3 -c "import json,sys; items=json.load(sys.stdin).get('items',[]); print(items[0]['id'] if items else '')" 2>/dev/null || echo "")
                if [ -n "$_prqid" ]; then
                    queue_complete_item "$_prqid"
                fi
                ;;

            REJECT)
                local max_retries
                max_retries=$(json_get "$CONFIG_FILE" "limits.max_rejection_retries" 2>/dev/null || echo "1")

                local feedback
                feedback=$(echo "$verdict_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('feedback_to_employee',''))" 2>/dev/null || echo "")
                local reqs_missing
                reqs_missing=$(echo "$verdict_json" | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin).get('requirements_missing',[])))" 2>/dev/null || echo "[]")

                # Check current retry count from feedback file (if any)
                local current_retry=0
                if [ -f "$workspace/.claude-manager-feedback.json" ]; then
                    current_retry=$(python3 -c "import json; print(json.load(open('$workspace/.claude-manager-feedback.json')).get('retry_count', 0))" 2>/dev/null || echo "0")
                fi

                if [ "$current_retry" -lt "$max_retries" ]; then
                    # Save feedback for employee retry — keep the branch intact
                    log_info "REJECT: Saving feedback for employee retry ($((current_retry + 1))/$max_retries)"
                    echo "$verdict_json" | python3 -c "
import json, sys
v = json.load(sys.stdin)
feedback_data = {
    'verdict': 'REJECT',
    'project': v.get('project', ''),
    'issue_number': v.get('issue_number', ''),
    'branch': v.get('branch', ''),
    'base_branch': v.get('base_branch', 'main'),
    'reasoning': v.get('reasoning', ''),
    'feedback_to_employee': v.get('feedback_to_employee', ''),
    'requirements_missing': v.get('requirements_missing', []),
    'retry_count': $((current_retry + 1)),
    'run_id': '$RUN_ID'
}
with open('$workspace/.claude-manager-feedback.json', 'w') as f:
    json.dump(feedback_data, f, indent=2)
" 2>/dev/null

                    log_ok "Feedback saved to $workspace/.claude-manager-feedback.json (retry $((current_retry + 1))/$max_retries)"

                    if [ -n "$issue_number" ] && [ "$issue_number" != "None" ] && [ "$issue_number" != "null" ]; then
                        gh issue comment "$issue_number" --repo "$project" --body "🤖 **Manager verdict: REJECTED** — $reasoning. Employee will retry with feedback (attempt $((current_retry + 1))/$max_retries).

Run: $RUN_ID" 2>/dev/null || true
                    fi

                    notify "reject_retry" "REJECTED (retry $((current_retry + 1))/$max_retries): $project #$issue_number - $reasoning"

                    # Queue: reject current item and create new pending item with feedback
                    local _rqid
                    _rqid=$(queue_api GET "/api/queue?project_repo=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$project'))" 2>/dev/null)&run_id=run-$RUN_ID&limit=1" | python3 -c "import json,sys; items=json.load(sys.stdin).get('items',[]); print(items[0]['id'] if items else '')" 2>/dev/null || echo "")
                    if [ -n "$_rqid" ]; then
                        local fb_escaped
                        fb_escaped=$(echo "$verdict_json" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" 2>/dev/null || echo "null")
                        queue_reject_item "$_rqid" "{\"state\":\"rejected\",\"manager_feedback\":$fb_escaped}"
                    fi
                else
                    # Max retries exhausted — clean up
                    log_info "REJECT: Max retries ($max_retries) exhausted. Resetting workspace."
                    git checkout "$pr_base_branch" 2>/dev/null || git checkout "$base_branch" 2>/dev/null || true
                    git branch -D "$branch" 2>/dev/null || true
                    rm -f "$workspace/.claude-manager-feedback.json"
                    log_ok "Rejected changes cleaned up (retries exhausted)"

                    if [ -n "$issue_number" ] && [ "$issue_number" != "None" ] && [ "$issue_number" != "null" ]; then
                        gh issue comment "$issue_number" --repo "$project" --body "🤖 **Manager verdict: REJECTED** — $reasoning. Retries exhausted ($max_retries attempts). Will start fresh next cycle.

Run: $RUN_ID" 2>/dev/null || true
                        # Clean up agent labels
                        gh issue edit "$issue_number" --repo "$project" --remove-label "autonomous-agent/done" 2>/dev/null || true
                        gh issue edit "$issue_number" --repo "$project" --remove-label "autonomous-agent/in-progress" 2>/dev/null || true
                    fi

                    notify "reject" "REJECTED (final): $project #$issue_number - $reasoning"

                    # Queue: mark as failed (retries exhausted)
                    local _fqid
                    _fqid=$(queue_api GET "/api/queue?project_repo=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$project'))" 2>/dev/null)&run_id=run-$RUN_ID&limit=1" | python3 -c "import json,sys; items=json.load(sys.stdin).get('items',[]); print(items[0]['id'] if items else '')" 2>/dev/null || echo "")
                    if [ -n "$_fqid" ]; then
                        queue_fail_item "$_fqid" "Max retries exhausted"
                    fi
                fi
                ;;

            SKIP)
                log_info "SKIP: No eligible work for $project — $reasoning"
                notify "skip" "SKIP: $project - $reasoning"

                # Queue: mark as completed (not a failure)
                local _sqid
                _sqid=$(queue_api GET "/api/queue?project_repo=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$project'))" 2>/dev/null)&run_id=run-$RUN_ID&limit=1" | python3 -c "import json,sys; items=json.load(sys.stdin).get('items',[]); print(items[0]['id'] if items else '')" 2>/dev/null || echo "")
                if [ -n "$_sqid" ]; then
                    queue_complete_item "$_sqid"
                fi
                ;;

            *)
                log_warn "Unknown verdict: $verdict for $project"
                ;;
        esac

        # Always return to base branch
        git checkout "$pr_base_branch" 2>/dev/null || git checkout "$base_branch" 2>/dev/null || true
    done

    # Post-verdict: validate integration branch if we had approvals
    if integration_enabled && [ "$had_integration_merges" = true ]; then
        local auto_validate
        auto_validate=$(json_get "$CONFIG_FILE" "integration.auto_validate" 2>/dev/null || echo "true")
        if [ "$auto_validate" = "true" ]; then
            validate_dev "$project" "" || log_warn "Validation failed for $project"

            local auto_promote
            auto_promote=$(json_get "$CONFIG_FILE" "integration.auto_promote" 2>/dev/null || echo "false")
            if [ "$auto_promote" = "true" ]; then
                local strategy
                strategy=$(json_get "$CONFIG_FILE" "integration.promotion_strategy" 2>/dev/null || echo "batch")
                promote_to_main "$project" "${base_branch:-main}" "$strategy" || log_warn "Promotion failed for $project"
            fi
        fi
    fi
}

# ============================================================================
# DIGEST
# ============================================================================

write_digest() {
    local verdicts_file="$1"
    local digest_file="$DIGEST_DIR/digest-${RUN_ID}.md"

    log_info "Writing digest to $digest_file"

    {
        echo "# Autonomous Agent Digest - $RUN_ID"
        echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo ""

        # Extract token usage from stream files
        echo "## Token Usage Summary"
        local total_tokens=0
        for stream in "$LOG_DIR"/run-${RUN_ID}-*.stream.jsonl; do
            [ -f "$stream" ] || continue
            local stream_name tokens
            stream_name=$(basename "$stream" .stream.jsonl | sed "s/run-${RUN_ID}-//")
            tokens=$(tail -1 "$stream" | python3 -c "
import json,sys
d=json.load(sys.stdin)
mu=d.get('modelUsage',{})
t=sum(u.get('inputTokens',0)+u.get('outputTokens',0) for u in mu.values())
print(t)
" 2>/dev/null || echo "0")
            echo "- **$stream_name**: ${tokens} tokens"
            total_tokens=$(python3 -c "print($total_tokens + $tokens)" 2>/dev/null || echo "?")
        done
        echo "- **Total**: ${total_tokens} tokens"
        echo ""

        if [ -f "$verdicts_file" ]; then
            python3 -c "
import json
with open('$verdicts_file') as f:
    data = json.load(f)

print('## Summary')
print(data.get('summary', 'No summary available.'))
print()

print('## Verdicts')
for v in data.get('verdicts', []):
    icon = {'APPROVE': 'APPROVED', 'PR': 'PR CREATED', 'REJECT': 'REJECTED'}.get(v['verdict'], v['verdict'])
    print(f\"### {v['project']} - {icon}\")
    print(f\"Issue: #{v.get('issue_number', '?')}\")
    print(f\"Branch: {v.get('branch', '?')}\")
    print(f\"Reasoning: {v.get('reasoning', '?')}\")
    print(f\"Feedback: {v.get('feedback_to_employee', '')}\")
    print()
" 2>/dev/null || echo "Could not parse verdicts."
        else
            echo "No verdicts produced in this run."
        fi
    } > "$digest_file"

    log_ok "Digest written: $digest_file"
}

# ============================================================================
# MAIN
# ============================================================================

main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --config)           CONFIG_FILE="$2"; shift 2 ;;
            --dry-run)          DRY_RUN=true; shift ;;
            --list-projects)    preflight; list_projects; exit 0 ;;
            --help|-h)          usage ;;
            # Private flag: used by agent/project_loop.py (issue #349 migration).
            # Runs the project-iteration body without emitting run lifecycle
            # events (run_start / run_complete are owned by RunDriver).
            --internal-iterate) INTERNAL_ITERATE=true; shift ;;
            -*)              log_error "Unknown option: $1"; usage ;;
            *)               log_error "Unknown argument: $1"; usage ;;
        esac
    done

    preflight

    # Load dashboard-managed GitHub token (falls back to existing GH_TOKEN)
    ensure_gh_token

    local project_count
    project_count=$(get_project_count)

    if [ "$project_count" -eq 0 ]; then
        log_warn "No projects configured in $CONFIG_FILE"
        exit 0
    fi

    log_info "Run ID: $RUN_ID"
    log_info "Projects: $project_count"

    # Read concurrency settings
    local max_concurrent max_per_project budget_strategy
    max_concurrent=$(get_max_concurrent)
    max_per_project=$(get_max_per_project)
    budget_strategy=$(get_budget_strategy)
    CONCURRENT_GROUP_ID="group-${RUN_ID}"

    log_info "Concurrency: max_concurrent=$max_concurrent, max_per_project=$max_per_project, strategy=$budget_strategy"

    _RUN_START_EPOCH=$(date +%s)
    # When invoked with --internal-iterate, RunDriver (Python) owns run_start.
    if [ "$INTERNAL_ITERATE" != "true" ]; then
        webhook_event "run_start" \
            project_count "$project_count" \
            max_concurrent "$max_concurrent" \
            concurrent_group_id "$CONCURRENT_GROUP_ID" \
            log_file "$LOG_DIR/run-${RUN_ID}.log"
    fi

    # ---- Count total employees to spawn (for budget calculation) ----
    local total_employees=0
    for ((i = 0; i < project_count; i++)); do
        local enabled_check
        enabled_check=$(get_project_field "$i" "enabled" 2>/dev/null || echo "true")
        [ "$enabled_check" = "false" ] && continue
        local mode_check
        mode_check=$(get_project_field "$i" "mode" 2>/dev/null || echo "full")
        local employees_for_project=$max_per_project
        # Analyze/plan modes only use 1 employee
        if [ "$mode_check" = "analyze" ] || [ "$mode_check" = "plan" ]; then
            employees_for_project=1
        fi
        total_employees=$((total_employees + employees_for_project))
    done
    log_info "Total employees to spawn: $total_employees"

    # ---- PHASE 0.3.5: Purge old completed/failed queue items ----
    queue_api POST "/api/queue/purge?max_age_days=7" >/dev/null 2>&1 || true

    # ---- PHASE 0.4: Resume paused and recover orphaned queue items ----
    # Resume paused items from previous runs
    local paused_count
    paused_count=$(queue_api GET "/api/queue?state=paused&limit=1" | python3 -c "import json,sys; print(json.load(sys.stdin).get('total',0))" 2>/dev/null || echo "0")
    if [ "$paused_count" -gt 0 ]; then
        log_info "Resuming $paused_count paused queue items from previous runs"
        local paused_items
        paused_items=$(queue_api GET "/api/queue?state=paused&limit=100")
        echo "$paused_items" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for item in data.get('items', []):
    print(item['id'])
" 2>/dev/null | while read -r qid; do
            queue_api PUT "/api/queue/$qid" "{\"state\":\"pending\",\"run_id\":\"run-$RUN_ID\"}" >/dev/null 2>&1
        done
    fi

    # Recover orphaned items stuck in assigned/in_progress/review from dead runs
    for orphan_state in assigned in_progress review; do
        local orphan_count
        orphan_count=$(queue_api GET "/api/queue?state=$orphan_state&limit=1" | python3 -c "import json,sys; print(json.load(sys.stdin).get('total',0))" 2>/dev/null || echo "0")
        if [ "$orphan_count" -gt 0 ]; then
            log_info "Recovering $orphan_count orphaned queue items in state '$orphan_state'"
            local orphan_items
            orphan_items=$(queue_api GET "/api/queue?state=$orphan_state&limit=100")
            # Only recover items NOT belonging to the current run
            echo "$orphan_items" | python3 -c "
import json, sys
data = json.load(sys.stdin)
current_run = 'run-$RUN_ID'
for item in data.get('items', []):
    if item.get('run_id') != current_run:
        print(item['id'])
" 2>/dev/null | while read -r qid; do
                queue_api PUT "/api/queue/$qid" "{\"state\":\"pending\",\"run_id\":null,\"assigned_to\":null}" >/dev/null 2>&1
            done
        fi
    done

    # ---- PHASE 0.5: Workspace setup + (optional) issue pre-assignment ----
    #
    # The Agent Teams orchestrator expects each project's workspace to
    # already exist (it cd's into it for `gh issue list` and creates
    # worktrees there). Clone/refresh every enabled project up-front, then
    # pre-assign only when running multiple employees on the same repo.
    for ((i = 0; i < project_count; i++)); do
        local repo_check enabled_check mode_check
        repo_check=$(get_project_field "$i" "repo")
        enabled_check=$(get_project_field "$i" "enabled" 2>/dev/null || echo "true")
        [ "$enabled_check" = "false" ] && continue
        mode_check=$(get_project_field "$i" "mode" 2>/dev/null || echo "full")

        local mode_branch
        mode_branch=$(get_project_field "$i" "branch" 2>/dev/null || echo "")
        local assign_workspace
        assign_workspace=$(setup_workspace "$repo_check" "$mode_branch") || {
            log_warn "Failed to setup workspace for $repo_check, orchestrator will skip this project"
            continue
        }

        # Pre-assign only for multi-employee projects in full mode; the
        # orchestrator self-selects in single-employee runs.
        local employees_for_assign=$max_per_project
        if [ "$employees_for_assign" -gt 1 ] && [ "$mode_check" = "full" ]; then
            assign_work "$repo_check" "$i" "$employees_for_assign" || true
        fi
    done

    # ---- Check for coordinated mode (enabled by default when parallel) ----
    local coordinated
    coordinated=$(json_get "$CONFIG_FILE" "coordinator.enabled" 2>/dev/null || echo "true")

    # ---- PHASE 1: Agent Teams orchestration ----
    local has_work=false

    log_info "=== AGENT TEAMS MODE: Using Claude Agent SDK orchestrator ==="

    local agent_dir
    agent_dir="$(cd "$SCRIPT_DIR/.." && pwd)"

    # PYTHONPATH must include both the agent root (so ``import agent``
    # resolves) AND the dashboard backend (so the orchestrator's queue
    # consumer can ``from app.database import async_session`` —
    # introduced in #290 to drain pending QueueItems from approved
    # plan_only runs). Dropping the latter silently disables the queue
    # drain with a ModuleNotFoundError after ``Processing project:``,
    # leaving operators with a "trigger has no effect" symptom.
    PYTHONPATH="$agent_dir/..:$agent_dir/../dashboard/backend" python3 -m agent.station_orchestrator \
        --config "$CONFIG_FILE" \
        --run-id "$RUN_ID" \
        --workspaces-dir "$WORKSPACES_DIR"

    local orch_exit=$?
    if [ $orch_exit -eq 0 ]; then
        has_work=true
        log_ok "Agent Teams orchestration completed successfully"
    else
        log_warn "Agent Teams orchestration failed (exit $orch_exit)"
        has_work=true
    fi

    if [ "$has_work" = false ]; then
        log_warn "No employees ran. Exiting."
        exit 0
    fi

    # ---- PHASE 1.7: Intelligence — Confidence Gate + Escalation ----
    # For each employee report, check if it passes the confidence gate (auto-PR)
    # or needs escalation (progressive deepening). Reports that pass the gate
    # are excluded from manager review.
    local -a auto_pr_projects=()  # Projects that passed confidence gate

    for ((i = 0; i < project_count; i++)); do
        local repo_cg enabled_cg
        repo_cg=$(get_project_field "$i" "repo")
        enabled_cg=$(get_project_field "$i" "enabled" 2>/dev/null || echo "true")
        [ "$enabled_cg" = "false" ] && continue

        local name_cg
        name_cg=$(repo_name "$repo_cg")
        local ws_cg="$WORKSPACES_DIR/$name_cg"

        # Check main report and indexed reports
        for report_cg in "$ws_cg"/.claude-employee-report*.json; do
            [ -f "$report_cg" ] || continue

            # --- Confidence gate: auto-PR for high-confidence work ---
            local conf_result
            conf_result=$(python3 -m agent.coordinator.decide --run-id "$RUN_ID" \
                check-confidence --report-file "$report_cg" --config "$CONFIG_FILE" 2>/dev/null || echo "")
            if [ -n "$conf_result" ]; then
                local gate_passed
                gate_passed=$(echo "$conf_result" | python3 -c "import json,sys; print(json.load(sys.stdin).get('gate_passed',False))" 2>/dev/null || echo "False")
                if [ "$gate_passed" = "True" ]; then
                    local conf_branch conf_issue conf_confidence
                    conf_branch=$(python3 -c "import json; print(json.load(open('$report_cg')).get('branch',''))" 2>/dev/null || echo "")
                    conf_issue=$(python3 -c "import json; print(json.load(open('$report_cg')).get('issue_number',''))" 2>/dev/null || echo "")
                    conf_confidence=$(echo "$conf_result" | python3 -c "import json,sys; print(json.load(sys.stdin).get('confidence',0))" 2>/dev/null || echo "0")

                    if [ -n "$conf_branch" ] && [ "$conf_branch" != "main" ]; then
                        log_info "  Confidence gate PASSED for $repo_cg (confidence=$conf_confidence)"
                        cd "$ws_cg"

                        # Push and create PR with [auto-pr] tag
                        if git push origin "$conf_branch" 2>/dev/null; then
                            local pr_url close_line
                            close_line=$(format_close_keywords "$conf_issue" "")
                            pr_url=$(gh pr create --repo "$repo_cg" --head "$conf_branch" \
                                --title "[auto-pr] Issue #${conf_issue}" \
                                --body "## Auto-PR (Confidence Gate)

Confidence: **${conf_confidence}** (above threshold)
Tests: Passed
Mode: $(python3 -c "import json; print(json.load(open('$report_cg')).get('mode',''))" 2>/dev/null || echo "unknown")${close_line:+

$close_line}

This PR was auto-created because the employee's work passed the confidence gate.
Human review is still required for merge.

---
Run: $RUN_ID | \`[auto-pr]\`" 2>/dev/null || echo "")
                            if [ -n "$pr_url" ]; then
                                log_ok "  Auto-PR created: $pr_url"
                                webhook_event "intelligence.confidence_gate_passed" \
                                    project "$repo_cg" \
                                    confidence "$conf_confidence" \
                                    branch "$conf_branch" \
                                    pr_url "$pr_url"
                                auto_pr_projects+=("$repo_cg")
                            fi
                        else
                            log_warn "  Failed to push $conf_branch for auto-PR"
                        fi
                    fi
                fi
            fi

            # --- Escalation: progressive deepening for low-confidence work ---
            local esc_result
            esc_result=$(python3 -m agent.coordinator.decide --run-id "$RUN_ID" \
                check-escalation --report-file "$report_cg" --config "$CONFIG_FILE" 2>/dev/null || echo "")
            if [ -n "$esc_result" ]; then
                local should_escalate
                should_escalate=$(echo "$esc_result" | python3 -c "import json,sys; print(json.load(sys.stdin).get('should_escalate',False))" 2>/dev/null || echo "False")
                if [ "$should_escalate" = "True" ]; then
                    local esc_mode esc_model esc_turns esc_rung
                    esc_mode=$(echo "$esc_result" | python3 -c "import json,sys; print(json.load(sys.stdin).get('next_mode',''))" 2>/dev/null || echo "")
                    esc_model=$(echo "$esc_result" | python3 -c "import json,sys; print(json.load(sys.stdin).get('next_model',''))" 2>/dev/null || echo "")
                    esc_turns=$(echo "$esc_result" | python3 -c "import json,sys; print(json.load(sys.stdin).get('next_max_turns',''))" 2>/dev/null || echo "")
                    esc_rung=$(echo "$esc_result" | python3 -c "import json,sys; print(json.load(sys.stdin).get('next_rung',''))" 2>/dev/null || echo "")

                    log_info "  ESCALATION triggered for $repo_cg: rung $esc_rung, mode=$esc_mode, model=$esc_model"

                    # Write handoff context file for the escalated employee
                    local esc_ctx_file="$ws_cg/.claude-escalation-context.json"
                    echo "$esc_result" | python3 -c "import json,sys; json.dump(json.load(sys.stdin).get('handoff_context',{}), open('$esc_ctx_file','w'), indent=2)" 2>/dev/null

                    # Re-run employee immediately with escalated config
                    log_info "  Re-running employee with escalated config (rung $esc_rung)"
                    run_employee "$repo_cg" "$ws_cg" "$i" "0" "$esc_turns" "$esc_mode" "$esc_model" "$esc_turns" "$esc_ctx_file"
                    has_work=true

                    # Skip this project from manager review — the escalated run will be reviewed
                    auto_pr_projects+=("$repo_cg")
                fi
            fi
        done
    done

    # ---- PHASE 1.8: Intelligence — Independent Verification ----
    # For auto-PR candidates and high-risk changes, run an independent reviewer.
    # If the reviewer flags critical issues, revoke the auto-PR and send to manager.
    local verification_enabled
    verification_enabled=$(json_get "$CONFIG_FILE" "intelligence.independent_verification" 2>/dev/null || echo "false")
    if [ "$verification_enabled" = "true" ]; then
        local -a verified_revokes=()
        for ((i = 0; i < project_count; i++)); do
            local repo_vf
            repo_vf=$(get_project_field "$i" "repo")
            local name_vf
            name_vf=$(repo_name "$repo_vf")
            local ws_vf="$WORKSPACES_DIR/$name_vf"

            for report_vf in "$ws_vf"/.claude-employee-report*.json; do
                [ -f "$report_vf" ] || continue

                local verify_result
                verify_result=$(python3 -m agent.coordinator.decide --run-id "$RUN_ID" \
                    should-verify --report-file "$report_vf" --config "$CONFIG_FILE" 2>/dev/null || echo "")
                if [ -n "$verify_result" ]; then
                    local should_verify
                    should_verify=$(echo "$verify_result" | python3 -c "import json,sys; print(json.load(sys.stdin).get('should_verify',False))" 2>/dev/null || echo "False")
                    if [ "$should_verify" = "True" ]; then
                        local vf_branch
                        vf_branch=$(python3 -c "import json; print(json.load(open('$report_vf')).get('branch',''))" 2>/dev/null || echo "")
                        log_info "  Running independent verification for $repo_vf ($vf_branch)"

                        # Run the verifier
                        local vf_result
                        vf_result=$(python3 -c "
import json
from agent.coordinator.verifier import run_verification
result = run_verification('$ws_vf', '$vf_branch', 'main', '$repo_vf')
print(json.dumps(result))
" 2>/dev/null || echo "")

                        if [ -n "$vf_result" ]; then
                            local vf_recommendation
                            vf_recommendation=$(echo "$vf_result" | python3 -c "import json,sys; print(json.load(sys.stdin).get('recommendation','needs_review'))" 2>/dev/null || echo "needs_review")
                            if [ "$vf_recommendation" = "revoke_auto_pr" ]; then
                                log_warn "  Verification REVOKED auto-PR for $repo_vf"
                                verified_revokes+=("$repo_vf")
                            else
                                log_info "  Verification result: $vf_recommendation"
                            fi
                        fi
                    fi
                fi
            done
        done

        # Remove revoked projects from auto-PR list
        for revoked in "${verified_revokes[@]}"; do
            local -a new_auto_pr=()
            for ap in "${auto_pr_projects[@]}"; do
                [ "$ap" != "$revoked" ] && new_auto_pr+=("$ap")
            done
            auto_pr_projects=("${new_auto_pr[@]}")
        done
    fi

    # Write auto-PR project list for collect_employee_reports to skip
    if [ ${#auto_pr_projects[@]} -gt 0 ]; then
        printf '%s\n' "${auto_pr_projects[@]}" > "/tmp/claude-agent-auto-pr-${RUN_ID}.list"
        log_info "Auto-PR/escalated projects (skipping manager review): ${auto_pr_projects[*]}"
    fi

    # ---- PHASE 1.9: Stale-PR sweep ----
    # Catch auto-merge PRs that didn't merge at creation time (transient
    # gh failure, branch protection delay, or — most importantly — a
    # manager run that produced no verdicts, OR a cycle with no eligible
    # backlog at all). Must run BEFORE the no-reports / no-verdicts
    # early-exit paths below, otherwise empty-backlog cycles never get
    # to retry their orphaned PRs. Only runs when integration is enabled;
    # only touches PRs carrying the `autonomous-agent/auto-merge` label.
    if integration_enabled; then
        local _sweep_i _sweep_repo _sweep_enabled
        for ((_sweep_i = 0; _sweep_i < project_count; _sweep_i++)); do
            _sweep_repo=$(get_project_field "$_sweep_i" "repo")
            _sweep_enabled=$(get_project_field "$_sweep_i" "enabled" 2>/dev/null || echo "true")
            [ "$_sweep_enabled" = "false" ] && continue
            [ -z "$_sweep_repo" ] && continue
            sweep_stale_integration_prs "$_sweep_repo" || true
        done
    fi

    # ---- PHASE 2: Manager review ----

    # Check if any employee produced a report — skip manager if nothing to review
    local any_reports=false
    for ((i = 0; i < project_count; i++)); do
        local repo_check
        repo_check=$(get_project_field "$i" "repo")
        local name_check
        name_check=$(repo_name "$repo_check")
        # Check main report and any indexed reports
        if [ -f "$WORKSPACES_DIR/$name_check/.claude-employee-report.json" ]; then
            any_reports=true
            break
        fi
        for indexed_check in "$WORKSPACES_DIR/$name_check"/.claude-employee-report-*.json; do
            if [ -f "$indexed_check" ]; then
                any_reports=true
                break 2
            fi
        done
    done

    if [ "$any_reports" = false ]; then
        log_warn "No employee reports found. Skipping manager review (saves session budget)."
        notify "skip" "Skipped manager review — no employee reports in run $RUN_ID"
        write_digest ""
        log_ok "Run $RUN_ID complete (no work to review)"
        notify "complete" "Run $RUN_ID finished (no work to review)"
        local _duration_ms_nr=0
        [ "$_RUN_START_EPOCH" -gt 0 ] && _duration_ms_nr=$(( ($(date +%s) - _RUN_START_EPOCH) * 1000 ))
        # When --internal-iterate is set, RunDriver (Python) owns run_complete.
        if [ "$INTERNAL_ITERATE" != "true" ]; then
            webhook_event "run_complete" \
                status "no_reports" \
                tokens_input "$_TOTAL_TOKENS_IN" \
                tokens_output "$_TOTAL_TOKENS_OUT" \
                tokens_total "$((_TOTAL_TOKENS_IN + _TOTAL_TOKENS_OUT))" \
                turns "$_TOTAL_TURNS" \
                duration_ms "$_duration_ms_nr"
            _RUN_COMPLETE_SENT=1
        fi
        exit 0
    fi

    if ! check_rate_limit; then
        log_warn "Rate limit reached before manager review. Employee work stays local."
        notify "rate_limit" "Rate limit reached before manager review in run $RUN_ID"
        local _duration_ms_rl=0
        [ "$_RUN_START_EPOCH" -gt 0 ] && _duration_ms_rl=$(( ($(date +%s) - _RUN_START_EPOCH) * 1000 ))
        # When --internal-iterate is set, RunDriver (Python) owns run_complete.
        if [ "$INTERNAL_ITERATE" != "true" ]; then
            webhook_event "run_complete" \
                status "rate_limited" \
                tokens_input "$_TOTAL_TOKENS_IN" \
                tokens_output "$_TOTAL_TOKENS_OUT" \
                tokens_total "$((_TOTAL_TOKENS_IN + _TOTAL_TOKENS_OUT))" \
                turns "$_TOTAL_TURNS" \
                duration_ms "$_duration_ms_rl"
            _RUN_COMPLETE_SENT=1
        fi
        exit 0
    fi

    local review_package
    review_package=$(collect_employee_reports "$project_count")
    log_info "Review package: $review_package"

    local verdicts_file
    verdicts_file=$(run_manager_review "$review_package")

    # ---- PHASE 3: Execute verdicts ----
    execute_verdicts "$verdicts_file"

    # ---- PHASE 3.5: Plan-review gate (issue #266) ----
    # For each plan_only project, drive the gate: parse the manager's
    # plan_verdicts, enqueue follow-up full runs on APPROVE_PLAN, write
    # revision feedback on REVISE_PLAN, log REJECT_PLAN. The Python
    # driver flips the Run row's status via the dashboard webhook.
    for ((i = 0; i < project_count; i++)); do
        local repo_pg enabled_pg mode_pg
        repo_pg=$(get_project_field "$i" "repo")
        enabled_pg=$(get_project_field "$i" "enabled" 2>/dev/null || echo "true")
        [ "$enabled_pg" = "false" ] && continue
        mode_pg=$(get_project_field "$i" "mode" 2>/dev/null || echo "full")
        [ -z "$mode_pg" ] && mode_pg="full"
        if [ "$mode_pg" != "plan_only" ]; then
            continue
        fi

        local name_pg
        name_pg=$(repo_name "$repo_pg")
        local workspace_pg="$WORKSPACES_DIR/$name_pg"

        log_info "Plan-review gate: applying for $repo_pg (run-$RUN_ID)"
        local agent_dir_pg
        agent_dir_pg="$(cd "$SCRIPT_DIR/.." && pwd)"
        PYTHONPATH="$agent_dir_pg/.." python3 -m agent.plan_review_gate \
            --project-mode "plan_only" \
            --verdicts "$verdicts_file" \
            --project-repo "$repo_pg" \
            --run-id "run-$RUN_ID" \
            --workspace "$workspace_pg" \
            2>&1 | while IFS= read -r line; do log_info "  gate: $line"; done || \
            log_warn "Plan-review gate exited non-zero for $repo_pg"
    done

    # ---- PHASE 3a: Clean up approved plan files and assignment files (no longer needed after verdicts) ----
    for ((i = 0; i < project_count; i++)); do
        local repo_ap
        repo_ap=$(get_project_field "$i" "repo")
        local name_ap
        name_ap=$(repo_name "$repo_ap")
        rm -f "$WORKSPACES_DIR/$name_ap"/.claude-approved-plan-*.json 2>/dev/null || true
        rm -f "$WORKSPACES_DIR/$name_ap"/.claude-assignment-*.json 2>/dev/null || true
    done

    # ---- PHASE 3b: Retry loop for rejected projects ----
    local max_retries
    max_retries=$(json_get "$CONFIG_FILE" "limits.max_rejection_retries" 2>/dev/null || echo "1")

    local retry_round=0
    while [ "$retry_round" -lt "$max_retries" ]; do
        # Check which projects have pending feedback files (meaning they were rejected and need retry)
        local retry_projects=()
        for ((i = 0; i < project_count; i++)); do
            local repo_retry
            repo_retry=$(get_project_field "$i" "repo")
            local name_retry
            name_retry=$(repo_name "$repo_retry")
            local ws_retry="$WORKSPACES_DIR/$name_retry"

            if [ -f "$ws_retry/.claude-manager-feedback.json" ]; then
                retry_projects+=("$i")
            fi
        done

        if [ ${#retry_projects[@]} -eq 0 ]; then
            log_info "No rejected projects to retry."
            break
        fi

        retry_round=$((retry_round + 1))
        log_info "=========================================="
        log_info "RETRY ROUND $retry_round/$max_retries: ${#retry_projects[@]} project(s) to retry"
        log_info "=========================================="

        # Re-run employees for rejected projects
        for idx in "${retry_projects[@]}"; do
            local repo_r
            repo_r=$(get_project_field "$idx" "repo")
            local name_r
            name_r=$(repo_name "$repo_r")
            local ws_r="$WORKSPACES_DIR/$name_r"

            if ! check_rate_limit; then
                log_warn "Rate limit reached during retry. Remaining retries cancelled."
                # Clean up remaining feedback files since we can't retry
                for remaining_idx in "${retry_projects[@]}"; do
                    local rr=$(get_project_field "$remaining_idx" "repo")
                    local rn=$(repo_name "$rr")
                    rm -f "$WORKSPACES_DIR/$rn/.claude-manager-feedback.json"
                done
                break 2
            fi

            if ! check_rate_limit; then
                log_warn "Plan usage cap reached during retry. Remaining retries cancelled."
                for remaining_idx in "${retry_projects[@]}"; do
                    local rr=$(get_project_field "$remaining_idx" "repo")
                    local rn=$(repo_name "$rr")
                    rm -f "$WORKSPACES_DIR/$rn/.claude-manager-feedback.json"
                done
                break 2
            fi

            # Issue freshness check before retry
            local _retry_assign="$ws_r/.claude-assignment-0.json"
            if [ -f "$_retry_assign" ]; then
                local _retry_issue
                _retry_issue=$(python3 -c "import json; print(json.load(open('$_retry_assign')).get('issue_number',''))" 2>/dev/null || echo "")
                if [ -n "$_retry_issue" ] && [ "$_retry_issue" != "None" ] && [ "$_retry_issue" != "null" ]; then
                    local _retry_state
                    _retry_state=$(gh issue view "$_retry_issue" --repo "$repo_r" --json state -q '.state' 2>/dev/null || echo "")
                    if [ -n "$_retry_state" ] && ! echo "$_retry_state" | grep -qi "open"; then
                        log_warn "Issue #$_retry_issue is no longer open (state: $_retry_state), skipping retry for $repo_r"
                        rm -f "$ws_r/.claude-manager-feedback.json"
                        continue
                    fi
                fi
            fi

            log_info "Retrying employee for: $repo_r"
            run_employee "$repo_r" "$ws_r" "$idx"

            # Pause between retries
            if [ ${#retry_projects[@]} -gt 1 ]; then
                log_info "Pausing 10s before next retry..."
                sleep 10
            fi
        done

        # Re-run manager review for retried projects
        if ! check_rate_limit; then
            log_warn "Rate limit reached before retry manager review."
            break
        fi

        local retry_review_package
        retry_review_package=$(collect_employee_reports "$project_count")
        log_info "Retry review package: $retry_review_package"

        local retry_verdicts_file
        retry_verdicts_file=$(run_manager_review "$retry_review_package")

        # Execute retry verdicts
        execute_verdicts "$retry_verdicts_file"

        # Update verdicts_file to the latest for digest
        verdicts_file="$retry_verdicts_file"
    done

    # Clean up any remaining feedback files (in case retries were exhausted by the loop limit)
    for ((i = 0; i < project_count; i++)); do
        local repo_cleanup
        repo_cleanup=$(get_project_field "$i" "repo")
        local name_cleanup
        name_cleanup=$(repo_name "$repo_cleanup")
        rm -f "$WORKSPACES_DIR/$name_cleanup/.claude-manager-feedback.json"
    done

    # ---- PHASE 4: Write digest ----
    write_digest "$verdicts_file"

    log_ok "Run $RUN_ID complete"
    notify "complete" "Run $RUN_ID finished successfully"
    local _duration_ms_ok=0
    [ "$_RUN_START_EPOCH" -gt 0 ] && _duration_ms_ok=$(( ($(date +%s) - _RUN_START_EPOCH) * 1000 ))
    # When --internal-iterate is set, RunDriver (Python) owns run_complete.
    if [ "$INTERNAL_ITERATE" != "true" ]; then
        webhook_event "run_complete" \
            status "success" \
            tokens_input "$_TOTAL_TOKENS_IN" \
            tokens_output "$_TOTAL_TOKENS_OUT" \
            tokens_total "$((_TOTAL_TOKENS_IN + _TOTAL_TOKENS_OUT))" \
            turns "$_TOTAL_TURNS" \
            duration_ms "$_duration_ms_ok"
        _RUN_COMPLETE_SENT=1
    fi
}

# Only run main when executed directly; allow `source` for helper testing.
if [ "${BASH_SOURCE[0]}" = "${0}" ] || [ -z "${BASH_SOURCE[0]-}" ]; then
    main "$@"
fi
