#!/usr/bin/env bash
# run-manager.sh - Manager/Employee Autonomous Agent Orchestrator
# Manages multiple projects with a manager-reviews-employees pattern
# Part of Claude Agent Station

set -euo pipefail

# ============================================================================
# CONFIGURATION
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR=""
DIGEST_DIR=""
WORKSPACES_DIR="${STATION_WORKSPACES:-/home/claude-agent/workspaces}"
DRY_RUN=false

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
    local file="$1" path="$2"
    python3 -c "
import json, sys
with open('$file') as f:
    data = json.load(f)
keys = '$path'.split('.')
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
" 2>/dev/null
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
            [ -n "$url" ] && curl -s -X POST "$url" \
                -H "Content-Type: application/json" \
                -d "{\"status\":\"$status\",\"message\":\"$message\",\"run_id\":\"$RUN_ID\"}" 2>/dev/null || true
            ;;
    esac
}

# ============================================================================
# DASHBOARD WEBHOOK (best-effort, never fails the agent run)
# ============================================================================

webhook_event() {
    local event="$1"
    shift
    local payload="$*"
    local webhook_url
    webhook_url=$(json_get "$CONFIG_FILE" "dashboard.webhook_url" 2>/dev/null || echo "")
    # Default to local dashboard if not configured
    [ -z "$webhook_url" ] && webhook_url="http://127.0.0.1:8420/api/webhook/run-event"
    # Include auth token header if a webhook secret is configured
    local webhook_secret
    webhook_secret="${STATION_WEBHOOK_SECRET:-$(json_get "$CONFIG_FILE" "dashboard.webhook_secret" 2>/dev/null || echo "")}"
    local -a auth_header=()
    if [ -n "$webhook_secret" ]; then
        auth_header=(-H "X-Webhook-Token: $webhook_secret")
    fi
    curl -s --max-time 3 -X POST "$webhook_url" \
        -H "Content-Type: application/json" \
        "${auth_header[@]}" \
        -d "{\"event\":\"$event\",\"run_id\":\"run-$RUN_ID\",\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",${payload}}" \
        2>/dev/null || true
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

# ============================================================================
# PARALLEL EXECUTION INFRASTRUCTURE
# ============================================================================

# Track child PIDs for signal propagation
declare -a CHILD_PIDS=()
CONCURRENT_GROUP_ID=""

cleanup_children() {
    local sig="${1:-TERM}"
    if [ ${#CHILD_PIDS[@]} -gt 0 ]; then
        log_warn "Propagating SIG$sig to ${#CHILD_PIDS[@]} child processes"
        for pid in "${CHILD_PIDS[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                kill -"$sig" "$pid" 2>/dev/null || true
            fi
        done
        # Give children time to exit gracefully
        sleep 2
        for pid in "${CHILD_PIDS[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null || true
            fi
        done
    fi
}

trap 'queue_api POST "/api/queue/batch-pause" "{\"run_id\":\"run-$RUN_ID\"}" 2>/dev/null; cleanup_children TERM; cleanup_all_worktrees 2>/dev/null || true; exit 130' SIGTERM
trap 'queue_api POST "/api/queue/batch-pause" "{\"run_id\":\"run-$RUN_ID\"}" 2>/dev/null; cleanup_children INT; cleanup_all_worktrees 2>/dev/null || true; exit 130' SIGINT

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

# Wait for a slot to open up when max concurrency is reached
wait_for_slot() {
    local max_concurrent="$1"
    while [ ${#CHILD_PIDS[@]} -ge "$max_concurrent" ]; do
        # Clean up finished processes
        local new_pids=()
        for pid in "${CHILD_PIDS[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                new_pids+=("$pid")
            fi
        done
        CHILD_PIDS=("${new_pids[@]}")

        if [ ${#CHILD_PIDS[@]} -ge "$max_concurrent" ]; then
            # Wait for any child to finish
            wait -n 2>/dev/null || true
        fi
    done
}

wait_for_all_children() {
    if [ ${#CHILD_PIDS[@]} -gt 0 ]; then
        log_info "Waiting for ${#CHILD_PIDS[@]} employee(s) to complete..."
        for pid in "${CHILD_PIDS[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                wait "$pid" 2>/dev/null || true
            fi
        done
        CHILD_PIDS=()
        log_ok "All employees completed"
    fi
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
with open(tracking_file, 'w') as f:
    json.dump(state, f, indent=2)
" 2>/dev/null
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

    for cmd in python3 claude git gh; do
        if ! command -v "$cmd" &>/dev/null; then
            log_error "$cmd is required but not found"
            exit 1
        fi
    done

    # Check authentication (with token expiry validation)
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
    if remaining > 300:
        print('valid')
    else:
        print('expired')
except:
    print('unknown')
" 2>/dev/null || echo "unknown")
            if [ "$token_status" = "expired" ]; then
                log_error "OAuth token expired. Re-authenticate or provide ANTHROPIC_API_KEY."
                notify "auth_failure" "OAuth token expired in run $RUN_ID"
                exit 1
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

# Extract repo name from "owner/repo" -> "repo"
repo_name() {
    echo "$1" | cut -d'/' -f2
}

# Ensure workspace exists and is up to date
setup_workspace() {
    local repo="$1"
    local name
    name=$(repo_name "$repo")
    local workspace="$WORKSPACES_DIR/$name"

    if [ -d "$workspace/.git" ]; then
        log_info "Resetting workspace to main for $repo..." >&2
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

        # 3. Switch to main (or master) — force checkout in case of conflicts
        local default_branch="main"
        if ! git rev-parse --verify main >/dev/null 2>&1; then
            default_branch="master"
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
# WORKTREE MANAGEMENT (for concurrent employees on same project)
# ============================================================================

setup_employee_worktree() {
    local repo="$1" employee_index="$2"
    local name
    name=$(repo_name "$repo")
    local main_workspace="$WORKSPACES_DIR/$name"
    local worktree_path="$WORKSPACES_DIR/${name}-e${employee_index}"

    # Remove stale worktree if it exists
    if [ -d "$worktree_path" ]; then
        log_info "Removing stale worktree: $worktree_path" >&2
        cd "$main_workspace"
        git worktree remove "$worktree_path" --force 2>/dev/null >&2 || rm -rf "$worktree_path"
    fi

    cd "$main_workspace"
    local current_branch
    current_branch=$(git branch --show-current 2>/dev/null || echo "main")

    # Create a new branch for the worktree — git won't allow checking out the
    # same branch in multiple worktrees, so each employee gets its own branch
    local worktree_branch="employee-${employee_index}-$(date +%s)"
    git worktree add -b "$worktree_branch" "$worktree_path" HEAD 2>&1 >&2 || {
        log_error "Failed to create worktree for $repo employee $employee_index" >&2
        return 1
    }

    log_info "Created worktree: $worktree_path (branch $worktree_branch from $current_branch)" >&2
    echo "$worktree_path"
}

cleanup_worktrees() {
    local repo="$1"
    local name
    name=$(repo_name "$repo")
    local main_workspace="$WORKSPACES_DIR/$name"

    if [ ! -d "$main_workspace/.git" ]; then
        return 0
    fi

    cd "$main_workspace"

    # Find and remove all employee worktrees for this repo
    local worktree_pattern="$WORKSPACES_DIR/${name}-e"
    for wt in "$worktree_pattern"*; do
        if [ -d "$wt" ]; then
            log_info "Cleaning up worktree: $wt"
            git worktree remove "$wt" --force 2>/dev/null || rm -rf "$wt"
        fi
    done

    # Prune stale worktree references
    git worktree prune 2>/dev/null || true

    # Clean up temporary employee branches
    git branch --list 'employee-*' 2>/dev/null | while read -r branch; do
        git branch -D "$branch" 2>/dev/null || true
    done
}

cleanup_all_worktrees() {
    local count
    count=$(get_project_count 2>/dev/null) || return 0
    for ((i = 0; i < count; i++)); do
        local repo
        repo=$(get_project_field "$i" "repo" 2>/dev/null) || continue
        cleanup_worktrees "$repo" 2>/dev/null || true
    done
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

    # Fetch open issues
    local issues_json
    issues_json=$(cd "$workspace" && GITHUB_REPO="$repo" gh issue list --repo "$repo" --state open --limit 30 --json number,title,body,labels,assignees 2>/dev/null) || {
        log_warn "Failed to fetch issues for $repo, employees will self-select"
        return 1
    }

    # Fetch open PRs to avoid duplicating work
    local prs_json
    prs_json=$(cd "$workspace" && GITHUB_REPO="$repo" gh pr list --repo "$repo" --state open --json number,title,headRefName 2>/dev/null) || prs_json="[]"

    # Build assignment prompt
    local assignment_prompt="Assign issues from this repository to $employee_count employees.

## Open Issues:
$issues_json

## Open PRs (avoid duplicating these):
$prs_json

## Employee Count: $employee_count

Return ONLY the JSON assignment object, no other text."

    # Run assigner with Haiku (fast + cheap)
    local assigner_prompt_file="$(resolve_prompt assigner)"
    local assignment_output
    assignment_output=$(echo "$assignment_prompt" | claude -p \
        --system-prompt "$(cat "$assigner_prompt_file")" \
        --model "claude-haiku-4-5-20251001" \
        --max-turns 1 \
        --no-session-persistence \
        --dangerously-skip-permissions \
        --output-format text 2>/dev/null) || {
        log_warn "Assignment agent failed for $repo, employees will self-select"
        return 1
    }

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
    curl -s --max-time 3 -X POST "$_ewh_url" \
        -H "Content-Type: application/json" \
        "${_ewh_auth[@]}" \
        -d "{\"event\":\"employee_start\",\"run_id\":\"$employee_run_id\",\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"project\":\"$repo\",\"mode\":\"$mode\",\"employee_index\":$employee_index,\"concurrent_group_id\":\"${CONCURRENT_GROUP_ID:-run-$RUN_ID}\"}" \
        2>/dev/null || true

    # Transition queue item to in_progress
    local _qid
    _qid=$(queue_find_item "$repo" "$employee_index")
    if [ -n "$_qid" ]; then
        queue_api PUT "/api/queue/$_qid" "{\"state\":\"in_progress\"}" >/dev/null 2>&1 &
    fi

    # Run setup script if configured for this project (install dependencies, etc.)
    local setup_script
    setup_script=$(get_project_field "$project_index" "setup_script" 2>/dev/null || echo "")
    if [ -n "$setup_script" ]; then
        log_info "Running setup script for $repo..."
        cd "$workspace"
        if bash -c "$setup_script" 2>&1 | tail -20; then
            log_ok "Setup script completed"
        else
            log_warn "Setup script failed (exit $?), continuing anyway"
        fi
    fi

    local model max_turns
    model=$(json_get "$CONFIG_FILE" "models.employee" 2>/dev/null || echo "claude-opus-4-6")
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
        system_prompt="$(resolve_prompt analyst)"
        employee_prompt="Analyze the repository: $repo

Environment variables available:
- GITHUB_REPO=$repo
- GH_TOKEN is set

Your workspace is: $workspace

Analyze the codebase for bugs, technical debt, security issues, and improvement opportunities. Create well-defined GitHub issues for each finding. Refine any existing vague issues with analysis details.

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

    if [ $exit_code -eq 0 ]; then
        log_ok "Employee finished: $repo"
    else
        log_warn "Employee exited with code $exit_code: $repo"
    fi

    # Use employee-specific run_id to complete the correct Run record
    curl -s --max-time 3 -X POST "$_ewh_url" \
        -H "Content-Type: application/json" \
        "${_ewh_auth[@]}" \
        -d "{\"event\":\"employee_complete\",\"run_id\":\"$employee_run_id\",\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"project\":\"$repo\",\"exit_code\":$exit_code,\"employee_index\":$employee_index,\"concurrent_group_id\":\"${CONCURRENT_GROUP_ID:-run-$RUN_ID}\"}" \
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
            queue_api PUT "/api/queue/$_qid2" "{\"state\":\"review\",\"employee_report\":$report_escaped}" >/dev/null 2>&1 &
        else
            queue_api PUT "/api/queue/$_qid2" "{\"state\":\"review\"}" >/dev/null 2>&1 &
        fi
    fi

    return 0  # Don't fail the whole run if one employee errors
}

# ============================================================================
# SHARED: Claude CLI subprocess helper
# ============================================================================

run_claude_agent() {
    # Shared helper to build and execute the standard Claude CLI command.
    # Used by run_employee_plan_only() and run_manager_plan_review().
    # run_employee() keeps its own execution (has special inline stream parsing).
    local model="$1" fallback="$2" turns="$3" sysprompt="$4"
    local prompt="$5" stream="$6" stderr="$7" workspace="$8" repo="$9"

    local -a cmd=(claude -p --verbose --output-format stream-json --no-session-persistence --dangerously-skip-permissions)
    cmd+=(--model "$model")
    cmd+=(--fallback-model "$fallback")
    cmd+=(--max-turns "$turns")
    cmd+=(--system-prompt-file "$sysprompt")

    if [ "$DRY_RUN" = true ]; then
        log_info "[DRY RUN] Would run claude agent (model=$model, turns=$turns)"
        return 0
    fi

    cd "$workspace"
    GITHUB_REPO="$repo" "${cmd[@]}" -- "$prompt" > "$stream" 2>>"$stderr"
    return $?
}

# ============================================================================
# PLAN REVIEW (Phase 1.5: Plan-before-implement loop)
# ============================================================================

should_skip_planning() {
    local repo="$1" project_index="$2" employee_index="${3:-0}"

    # Check global config flag
    local planning_enabled
    planning_enabled=$(json_get "$CONFIG_FILE" "planning.enabled" 2>/dev/null || echo "true")
    [ "$planning_enabled" = "false" ] && return 0

    # Analyze mode: skip planning entirely (analyst prompt only — no implementation plans)
    local project_mode
    project_mode=$(get_project_field "$project_index" "mode" 2>/dev/null || echo "full")
    if [ "$project_mode" = "analyze" ]; then
        return 0
    fi

    # Check for skip-planning label on the assigned issue
    local name workspace
    name=$(repo_name "$repo")
    workspace="$WORKSPACES_DIR/$name"
    local assignment_file="$workspace/.claude-assignment-${employee_index}.json"
    if [ -f "$assignment_file" ]; then
        local has_skip_label
        has_skip_label=$(python3 -c "
import json
a = json.load(open('$assignment_file'))
labels = [l.get('name','') if isinstance(l, dict) else str(l) for l in a.get('labels', [])]
print('true' if 'skip-planning' in labels else 'false')
" 2>/dev/null || echo "false")
        [ "$has_skip_label" = "true" ] && return 0
    fi

    return 1  # Don't skip (planning is required)
}

run_employee_plan_only() {
    local repo="$1" workspace="$2" project_index="$3"
    local employee_index="${4:-0}"
    local revision_feedback="${5:-}"
    local name
    name=$(repo_name "$repo")

    local report_suffix=""
    [ "$employee_index" -gt 0 ] && report_suffix="-${employee_index}"

    local plan_output="$workspace/.claude-employee-plan-${employee_index}.json"
    rm -f "$plan_output"

    local model max_turns
    model=$(json_get "$CONFIG_FILE" "models.employee" 2>/dev/null || echo "claude-opus-4-6")
    max_turns=20  # Plan phase is lighter — 20 turns is generous

    local fallback_model="claude-sonnet-4-6"
    [ "$model" = "claude-sonnet-4-6" ] && fallback_model="claude-haiku-4-5-20251001"

    local system_prompt="$(resolve_prompt employee)"

    # Check for assignment
    local assignment_section=""
    local assignment_file="$workspace/.claude-assignment-${employee_index}.json"
    if [ -f "$assignment_file" ]; then
        local assign_issue assign_title
        assign_issue=$(python3 -c "import json; print(json.load(open('$assignment_file')).get('issue_number',''))" 2>/dev/null || echo "")
        assign_title=$(python3 -c "import json; print(json.load(open('$assignment_file')).get('issue_title',''))" 2>/dev/null || echo "")
        assignment_section="
## DIRECTED MODE: Pre-Assigned Issue

- **Issue**: #${assign_issue}
- **Title**: ${assign_title}
"
    fi

    local revision_section=""
    if [ -n "$revision_feedback" ]; then
        revision_section="
## PLAN_REVISION: Manager Feedback on Previous Plan

The manager reviewed your previous plan and requested changes:

${revision_feedback}

Revise your plan based on this feedback.
"
    fi

    local employee_prompt="Work on the repository: $repo

Environment variables available:
- GITHUB_REPO=$repo
- GH_TOKEN is set

Your workspace is: $workspace

## PLAN_ONLY_MODE

You are in **plan-only mode**. Create an implementation plan but do NOT write any code.

1. Read the issue fully (including all comments)
2. Read all relevant source code
3. Create a detailed implementation plan
4. Write the plan JSON to: $plan_output
5. Write your report to: $workspace/.claude-employee-report${report_suffix}.json with mode \"plan_only\"
6. STOP. Do not implement anything.
${assignment_section}
${revision_section}

Remember: Plan only. Do NOT create branches, modify source files, or commit anything."

    local stream_file="$LOG_DIR/run-${RUN_ID}-employee-${name}-plan.stream.jsonl"
    local stderr_file="$LOG_DIR/run-${RUN_ID}-employee-${name}-plan.stderr.log"

    log_info "Running employee plan phase for $repo (employee $employee_index)"

    run_claude_agent "$model" "$fallback_model" "$max_turns" "$system_prompt" \
        "$employee_prompt" "$stream_file" "$stderr_file" "$workspace" "$repo"
    local exit_code=$?

    record_session

    if [ $exit_code -eq 0 ] && [ -f "$plan_output" ]; then
        log_ok "Employee plan phase complete for $repo"
        return 0
    else
        log_warn "Employee plan phase failed for $repo (exit $exit_code)"
        return 1
    fi
}

run_manager_plan_review() {
    local repo="$1" workspace="$2" employee_index="${3:-0}" project_index="${4:-0}"
    local name
    name=$(repo_name "$repo")

    local project_mode
    project_mode=$(get_project_field "$project_index" "mode" 2>/dev/null || echo "full")

    local plan_file="$workspace/.claude-employee-plan-${employee_index}.json"
    local verdict_file="$LOG_DIR/run-${RUN_ID}-plan-verdict-${name}.json"
    local review_file="$LOG_DIR/run-${RUN_ID}-plan-review-${name}.md"

    if [ ! -f "$plan_file" ]; then
        log_warn "No plan file found for $repo employee $employee_index"
        return 1
    fi

    # Build review package
    {
        echo "# Plan Review Package - Employee $employee_index"
        echo ""
        echo "## MODE: PLAN_REVIEW"
        echo ""
        echo "## PROJECT_MODE: $project_mode"
        echo ""
        if [ "$project_mode" = "plan" ]; then
            echo "**NOTE: This project is in PLAN mode. Review the plan for quality but be aware implementation will NOT proceed. Focus on plan quality for future reference.**"
            echo ""
        fi
        echo "## Project: $repo"
        echo ""
        echo "## Employee's Implementation Plan:"
        echo '```json'
        cat "$plan_file"
        echo '```'
        echo ""
        echo "Write your plan verdict to: $verdict_file"
    } > "$review_file"

    local model
    model=$(json_get "$CONFIG_FILE" "models.manager" 2>/dev/null || echo "claude-sonnet-4-6")

    local manager_fallback="claude-haiku-4-5-20251001"
    [ "$model" = "claude-haiku-4-5-20251001" ] && manager_fallback="claude-sonnet-4-6"

    local manager_prompt="Review the employee's implementation plan in: $review_file

Write your plan verdict to: $verdict_file

Use APPROVE_PLAN if the plan is solid, REVISE_PLAN with specific feedback if it needs changes, or REJECT_PLAN if fundamentally flawed."

    local stream_file="$LOG_DIR/run-${RUN_ID}-plan-review-${name}.stream.jsonl"
    local stderr_file="$LOG_DIR/run-${RUN_ID}-plan-review-${name}.stderr.log"

    log_info "Running manager plan review for $repo"

    run_claude_agent "$model" "$manager_fallback" 5 "$(resolve_prompt manager)" \
        "$manager_prompt" "$stream_file" "$stderr_file" "$workspace" "$repo"

    record_session

    # Parse verdict
    if [ -f "$verdict_file" ]; then
        local plan_verdict
        plan_verdict=$(python3 -c "
import json
with open('$verdict_file') as f:
    data = json.load(f)
verdicts = data.get('plan_verdicts', [])
if verdicts:
    print(verdicts[0].get('verdict', 'APPROVE_PLAN'))
else:
    print('APPROVE_PLAN')
" 2>/dev/null || echo "APPROVE_PLAN")
        echo "$plan_verdict"
    else
        log_warn "No plan verdict file produced for $repo, defaulting to APPROVE_PLAN"
        echo "APPROVE_PLAN"
    fi
}

get_plan_review_feedback() {
    local repo="$1"
    local name
    name=$(repo_name "$repo")
    local verdict_file="$LOG_DIR/run-${RUN_ID}-plan-verdict-${name}.json"

    if [ -f "$verdict_file" ]; then
        python3 -c "
import json
with open('$verdict_file') as f:
    data = json.load(f)
verdicts = data.get('plan_verdicts', [])
if verdicts:
    print(verdicts[0].get('feedback', ''))
" 2>/dev/null || echo ""
    fi
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

        # Detect project mode from config
        local project_mode
        project_mode=$(get_project_field "$i" "mode" 2>/dev/null || echo "full")
        [ -z "$project_mode" ] && project_mode="full"
        if [ "$project_mode" = "analyze" ]; then
            echo "### ⚠️ MODE: ANALYZE — No code changes expected" >> "$review_package"
            echo "" >> "$review_package"
            echo "This project is running in **analyze mode**. The employee was instructed to read code and create/refine GitHub issues ONLY — not to make any code changes. **Do NOT reject for absence of code changes.** Review the quality of created/refined issues instead." >> "$review_package"
            echo "" >> "$review_package"
        fi

        # Verify no source files were modified in analyze/plan modes (defense in depth)
        if [ "$project_mode" = "analyze" ] || [ "$project_mode" = "plan" ]; then
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

    webhook_event "manager_review" "\"review_package\":\"$review_package\"" >&2

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

    local manager_prompt="Review the employee work in this file: $review_package

Write your verdicts to: $verdicts_file

Read the review package file first, then evaluate each project's work against the criteria in your system prompt. Be strict on completeness — never approve partial implementations."

    log_info "Manager command: ${cmd[*]} '<prompt>'" >&2

    if [ "$DRY_RUN" = true ]; then
        log_info "[DRY RUN] Would run manager review" >&2
        return 0
    fi

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

    echo "$verdicts_file"
}

# ============================================================================
# VERDICT EXECUTION
# ============================================================================

execute_verdicts() {
    local verdicts_file="$1"

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
        issue_number=$(echo "$verdict_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('issue_number',''))")
        reasoning=$(echo "$verdict_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('reasoning',''))")
        base_branch=$(echo "$verdict_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('base_branch','main'))")
        verdict_mode=$(echo "$verdict_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('mode',''))" 2>/dev/null || echo "")

        local name
        name=$(repo_name "$project")
        local workspace="$WORKSPACES_DIR/$name"

        log_info "Project: $project | Verdict: $verdict | Issue: #$issue_number | Branch: $branch"
        log_info "Reasoning: $reasoning"

        # Escape reasoning for JSON (replace newlines and quotes)
        local escaped_reasoning
        escaped_reasoning=$(echo "$reasoning" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip())[1:-1])" 2>/dev/null || echo "$reasoning")
        webhook_event "verdict_execute" "\"project\":\"$project\",\"verdict\":\"$verdict\",\"issue_number\":\"$issue_number\",\"branch\":\"$branch\",\"reasoning\":\"$escaped_reasoning\""

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
            >/dev/null 2>&1 &

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
            _aqid=$(queue_api GET "/api/queue?project_repo=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$project'))" 2>/dev/null)&run_id=run-$RUN_ID&state=review&limit=1" | python3 -c "import json,sys; items=json.load(sys.stdin).get('items',[]); print(items[0]['id'] if items else '')" 2>/dev/null || echo "")
            if [ -n "$_aqid" ]; then
                if [ "$verdict" = "APPROVE" ]; then
                    queue_api PUT "/api/queue/$_aqid" "{\"state\":\"approved\"}" >/dev/null 2>&1
                    queue_api PUT "/api/queue/$_aqid" "{\"state\":\"completed\"}" >/dev/null 2>&1
                else
                    queue_api PUT "/api/queue/$_aqid" "{\"state\":\"rejected\"}" >/dev/null 2>&1
                fi
            fi

            # Return to base branch and continue to next verdict
            git checkout "$base_branch" 2>/dev/null || true
            continue
        fi

        case "$verdict" in
            APPROVE)
                log_info "APPROVE: Pushing, merging, and closing issue (base: $base_branch)"
                local push_merge_ok=false
                # Push the branch
                if git push origin "$branch" 2>/dev/null; then
                    log_ok "Pushed $branch"

                    # Merge to base branch
                    git checkout "$base_branch" 2>/dev/null
                    if git merge "$branch" 2>/dev/null; then
                        git push origin "$base_branch" 2>/dev/null && log_ok "Merged to $base_branch"
                        push_merge_ok=true

                        # Cleanup branch
                        git branch -d "$branch" 2>/dev/null || true
                        git push origin --delete "$branch" 2>/dev/null || true
                    else
                        log_error "Merge failed. Creating PR instead."
                        git checkout "$branch" 2>/dev/null
                        gh pr create --repo "$project" --base "$base_branch" \
                            --title "autonomous: $(git log -1 --format=%s)" \
                            --body "Merge conflict detected. Manual resolution needed.

Run: $RUN_ID" 2>/dev/null || true
                    fi
                else
                    log_error "Push failed for $branch"
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

                # Queue: approved → completed
                local _vqid
                _vqid=$(queue_api GET "/api/queue?project_repo=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$project'))" 2>/dev/null)&run_id=run-$RUN_ID&state=review&limit=1" | python3 -c "import json,sys; items=json.load(sys.stdin).get('items',[]); print(items[0]['id'] if items else '')" 2>/dev/null || echo "")
                if [ -n "$_vqid" ]; then
                    queue_api PUT "/api/queue/$_vqid" "{\"state\":\"approved\"}" >/dev/null 2>&1
                    queue_api PUT "/api/queue/$_vqid" "{\"state\":\"completed\"}" >/dev/null 2>&1
                fi
                ;;

            PR)
                log_info "PR: Pushing branch and creating PR for human review (base: $base_branch)"
                if git push origin "$branch" 2>/dev/null; then
                    log_ok "Pushed $branch"
                    gh pr create --repo "$project" --base "$base_branch" \
                        --title "autonomous: $(git log -1 --format=%s)" \
                        --body "## Needs Human Review

**Manager reasoning**: $reasoning

**Issue**: #$issue_number

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

                # Queue: approved → completed (PR is also a terminal success)
                local _prqid
                _prqid=$(queue_api GET "/api/queue?project_repo=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$project'))" 2>/dev/null)&run_id=run-$RUN_ID&state=review&limit=1" | python3 -c "import json,sys; items=json.load(sys.stdin).get('items',[]); print(items[0]['id'] if items else '')" 2>/dev/null || echo "")
                if [ -n "$_prqid" ]; then
                    queue_api PUT "/api/queue/$_prqid" "{\"state\":\"approved\"}" >/dev/null 2>&1
                    queue_api PUT "/api/queue/$_prqid" "{\"state\":\"completed\"}" >/dev/null 2>&1
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
                    _rqid=$(queue_api GET "/api/queue?project_repo=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$project'))" 2>/dev/null)&run_id=run-$RUN_ID&state=review&limit=1" | python3 -c "import json,sys; items=json.load(sys.stdin).get('items',[]); print(items[0]['id'] if items else '')" 2>/dev/null || echo "")
                    if [ -n "$_rqid" ]; then
                        local fb_escaped
                        fb_escaped=$(echo "$verdict_json" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" 2>/dev/null || echo "null")
                        queue_api PUT "/api/queue/$_rqid" "{\"state\":\"rejected\",\"manager_feedback\":$fb_escaped}" >/dev/null 2>&1
                    fi
                else
                    # Max retries exhausted — clean up
                    log_info "REJECT: Max retries ($max_retries) exhausted. Resetting workspace."
                    git checkout "$base_branch" 2>/dev/null || true
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
                    _fqid=$(queue_api GET "/api/queue?project_repo=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$project'))" 2>/dev/null)&run_id=run-$RUN_ID&state=review&limit=1" | python3 -c "import json,sys; items=json.load(sys.stdin).get('items',[]); print(items[0]['id'] if items else '')" 2>/dev/null || echo "")
                    if [ -n "$_fqid" ]; then
                        queue_api PUT "/api/queue/$_fqid" "{\"state\":\"rejected\",\"error_message\":\"Max retries exhausted\"}" >/dev/null 2>&1
                        queue_api PUT "/api/queue/$_fqid" "{\"state\":\"failed\",\"error_message\":\"Max retries exhausted\"}" >/dev/null 2>&1
                    fi
                fi
                ;;

            SKIP)
                log_info "SKIP: No eligible work for $project — $reasoning"
                notify "skip" "SKIP: $project - $reasoning"

                # Queue: mark as completed (not a failure)
                local _sqid
                _sqid=$(queue_api GET "/api/queue?project_repo=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$project'))" 2>/dev/null)&run_id=run-$RUN_ID&state=review&limit=1" | python3 -c "import json,sys; items=json.load(sys.stdin).get('items',[]); print(items[0]['id'] if items else '')" 2>/dev/null || echo "")
                if [ -n "$_sqid" ]; then
                    queue_api PUT "/api/queue/$_sqid" "{\"state\":\"approved\"}" >/dev/null 2>&1
                    queue_api PUT "/api/queue/$_sqid" "{\"state\":\"completed\"}" >/dev/null 2>&1
                fi
                ;;

            *)
                log_warn "Unknown verdict: $verdict for $project"
                ;;
        esac

        # Always return to base branch
        git checkout "$base_branch" 2>/dev/null || true
    done
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
            --config)        CONFIG_FILE="$2"; shift 2 ;;
            --dry-run)       DRY_RUN=true; shift ;;
            --list-projects) preflight; list_projects; exit 0 ;;
            --help|-h)       usage ;;
            -*)              log_error "Unknown option: $1"; usage ;;
            *)               log_error "Unknown argument: $1"; usage ;;
        esac
    done

    preflight

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

    webhook_event "run_start" "\"project_count\":$project_count,\"max_concurrent\":$max_concurrent,\"concurrent_group_id\":\"$CONCURRENT_GROUP_ID\""

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

    # ---- PHASE 0.5: Pre-assign issues for multi-employee projects ----
    for ((i = 0; i < project_count; i++)); do
        local repo_check enabled_check mode_check
        repo_check=$(get_project_field "$i" "repo")
        enabled_check=$(get_project_field "$i" "enabled" 2>/dev/null || echo "true")
        [ "$enabled_check" = "false" ] && continue
        mode_check=$(get_project_field "$i" "mode" 2>/dev/null || echo "full")

        local employees_for_assign=$max_per_project

        # Only pre-assign if multiple employees on same project in full mode
        if [ "$employees_for_assign" -gt 1 ] && [ "$mode_check" = "full" ]; then
            # Need workspace for fetching issues
            local assign_workspace
            assign_workspace=$(setup_workspace "$repo_check") || {
                log_warn "Failed to setup workspace for $repo_check assignment, will self-select"
                continue
            }
            assign_work "$repo_check" "$i" "$employees_for_assign" || true
        fi
    done

    # ---- Check for coordinated mode (enabled by default when parallel) ----
    local coordinated
    coordinated=$(json_get "$CONFIG_FILE" "coordinator.enabled" 2>/dev/null || echo "true")

    # ---- PHASE 1: Run employees per project ----
    local has_work=false
    local is_parallel=false
    [ "$max_concurrent" -gt 1 ] && is_parallel=true

    # ---- COORDINATED MODE: delegate to Python async coordinator ----
    if [ "$coordinated" = "true" ] && [ "$is_parallel" = true ]; then
        log_info "=== COORDINATED MODE: Using Python async coordinator ==="

        # Build assignments JSON for the coordinator
        local assignments_json="$LOG_DIR/run-${RUN_ID}-assignments.json"
        local agent_dir_for_py
        agent_dir_for_py="$(cd "$SCRIPT_DIR/.." && pwd)"
        PYTHONPATH="$agent_dir_for_py/.." python3 -c "
import json, sys
config = json.load(open('$CONFIG_FILE'))
projects = config.get('projects', [])
assignments = []
max_per = int('$max_per_project')
workspaces_dir = '$WORKSPACES_DIR'

for i, proj in enumerate(projects):
    if not proj.get('enabled', True):
        continue
    repo = proj['repo']
    mode = proj.get('mode', 'full')
    employees = max_per
    repo_name = repo.split('/')[-1] if '/' in repo else repo
    workspace = f'{workspaces_dir}/{repo_name}'

    # Check for pre-assignments
    issue_number = None
    import os
    assign_file = f'{workspace}/.claude-assignment-0.json'
    if os.path.exists(assign_file):
        try:
            assign = json.load(open(assign_file))
            issue_number = assign.get('issue_number')
        except:
            pass

    assignments.append({
        'repo': repo,
        'workspace': workspace,
        'employee_count': employees,
        'mode': mode,
        'issue_number': issue_number,
    })

json.dump(assignments, open('$assignments_json', 'w'), indent=2)
print(f'Wrote {len(assignments)} assignments')
" 2>&1 | while read -r line; do log_info "  $line"; done

        if [ -f "$assignments_json" ]; then
            local agent_dir
            agent_dir="$(cd "$SCRIPT_DIR/.." && pwd)"

            PYTHONPATH="$agent_dir/.." python3 -m agent.coordinator \
                --run-id "$RUN_ID" \
                --config-file "$CONFIG_FILE" \
                --log-dir "$LOG_DIR" \
                --workspaces-dir "$WORKSPACES_DIR" \
                --assignments-file "$assignments_json" \
                --concurrent-group-id "$CONCURRENT_GROUP_ID"

            has_work=true
        else
            log_warn "Failed to build assignments JSON, falling back to legacy mode"
        fi
    fi

    # ---- LEGACY MODE: existing bash employee loop ----
    if [ "$has_work" = false ]; then

    for ((i = 0; i < project_count; i++)); do
        local repo priority enabled
        repo=$(get_project_field "$i" "repo")
        priority=$(get_project_field "$i" "priority" 2>/dev/null || echo "medium")
        enabled=$(get_project_field "$i" "enabled" 2>/dev/null || echo "true")

        # Skip disabled projects
        if [ "$enabled" = "false" ]; then
            log_info "Skipping disabled project: $repo"
            continue
        fi

        local mode_for_project
        mode_for_project=$(get_project_field "$i" "mode" 2>/dev/null || echo "full")
        local employees_this_project=$max_per_project

        for ((ei = 0; ei < employees_this_project; ei++)); do
            log_info "Project $((i+1))/$project_count: $repo (priority: $priority, employee: $ei)"

            # Check rate limit before each employee
            if ! check_rate_limit; then
                log_warn "Rate limit reached. Stopping before $repo employee $ei"
                notify "rate_limit" "Rate limit reached before $repo employee $ei in run $RUN_ID"
                break 2
            fi

            # Check token budget before each employee
            if ! check_rate_limit; then
                log_warn "Plan usage cap reached. Stopping before $repo employee $ei"
                notify "rate_limit" "Plan usage cap reached before $repo employee $ei in run $RUN_ID"
                queue_api POST "/api/queue/batch-pause" "{\"run_id\":\"run-$RUN_ID\"}" >/dev/null 2>&1 &
                break 2
            fi

            # Setup workspace: employee 0 uses main workspace, others get worktrees
            local workspace
            if [ "$ei" -eq 0 ]; then
                workspace=$(setup_workspace "$repo") || {
                    log_error "Failed to setup workspace for $repo"
                    continue 2
                }
            elif [ "$employees_this_project" -gt 1 ]; then
                # Create isolated worktree for concurrent employees — NO fallback
                workspace=$(setup_employee_worktree "$repo" "$ei") || {
                    log_error "Failed to create worktree for $repo employee $ei, skipping (no shared workspace fallback)"
                    continue
                }
                # Copy assignment file to worktree if it exists
                local main_ws="$WORKSPACES_DIR/$(repo_name "$repo")"
                if [ -f "$main_ws/.claude-assignment-${ei}.json" ]; then
                    cp "$main_ws/.claude-assignment-${ei}.json" "$workspace/.claude-assignment-${ei}.json"
                fi
            else
                workspace="$WORKSPACES_DIR/$(repo_name "$repo")"
            fi

            # ---- Plan review gate ----
            if ! should_skip_planning "$repo" "$i" "$ei"; then
                local max_plan_revisions
                max_plan_revisions=$(json_get "$CONFIG_FILE" "planning.max_revisions" 2>/dev/null || echo "2")
                local plan_approved=false

                for ((pr = 0; pr <= max_plan_revisions; pr++)); do
                    local plan_feedback=""
                    [ "$pr" -gt 0 ] && plan_feedback=$(get_plan_review_feedback "$repo")

                    if ! run_employee_plan_only "$repo" "$workspace" "$i" "$ei" "$plan_feedback"; then
                        log_warn "Plan phase failed for $repo employee $ei"
                        break
                    fi

                    if ! check_rate_limit; then
                        log_warn "Rate limit reached during plan review"
                        break 3
                    fi

                    local plan_verdict
                    plan_verdict=$(run_manager_plan_review "$repo" "$workspace" "$ei" "$i")

                    if [ "$plan_verdict" = "APPROVE_PLAN" ]; then
                        # Copy plan as approved plan for implementation
                        cp "$workspace/.claude-employee-plan-${ei}.json" \
                           "$workspace/.claude-approved-plan-${ei}.json"
                        plan_approved=true
                        log_ok "Plan APPROVED for $repo employee $ei"
                        break
                    elif [ "$plan_verdict" = "REJECT_PLAN" ]; then
                        log_warn "Plan REJECTED for $repo employee $ei"
                        break
                    else
                        # REVISE_PLAN
                        log_info "Plan needs revision for $repo employee $ei (attempt $((pr+1))/$max_plan_revisions)"
                        if [ "$pr" -eq "$max_plan_revisions" ]; then
                            log_warn "Max plan revisions reached, auto-approving"
                            cp "$workspace/.claude-employee-plan-${ei}.json" \
                               "$workspace/.claude-approved-plan-${ei}.json"
                            plan_approved=true
                        fi
                    fi
                done

                if [ "$plan_approved" = false ]; then
                    log_warn "Skipping implementation for $repo employee $ei (plan not approved)"
                    continue
                fi
            fi
            # ---- End plan review gate ----

            # Note: analyze/plan mode enforcement is handled by prompt selection
            # above (analyst.md / plan prompt) and disallowed tools in the CLI call.

            # Calculate per-employee turn budget
            local employee_turns
            employee_turns=$(calculate_employee_budget "$total_employees" "$priority")

            # Intelligence: per-issue mode selection via decide.py
            local intel_mode="" intel_model="" intel_turns="" intel_esc_file=""
            local auto_mode_enabled
            auto_mode_enabled=$(json_get "$CONFIG_FILE" "intelligence.auto_mode_selection" 2>/dev/null || echo "false")
            if [ "$auto_mode_enabled" = "true" ]; then
                # Find issue JSON for this employee (from assignment or workspace)
                local issue_json_file="$workspace/.claude-assignment-${ei}.json"
                if [ ! -f "$issue_json_file" ]; then
                    issue_json_file="$workspace/.claude-issue.json"
                fi
                if [ -f "$issue_json_file" ]; then
                    local decide_result
                    decide_result=$(python3 -m agent.coordinator.decide --run-id "$RUN_ID" \
                        select-mode --issue-json "$issue_json_file" \
                        --config "$CONFIG_FILE" --project-mode "$mode_for_project" 2>/dev/null || echo "")
                    if [ -n "$decide_result" ]; then
                        intel_mode=$(echo "$decide_result" | python3 -c "import json,sys; print(json.load(sys.stdin).get('mode',''))" 2>/dev/null || echo "")
                        intel_model=$(echo "$decide_result" | python3 -c "import json,sys; print(json.load(sys.stdin).get('model',''))" 2>/dev/null || echo "")
                        intel_turns=$(echo "$decide_result" | python3 -c "import json,sys; print(json.load(sys.stdin).get('max_turns',''))" 2>/dev/null || echo "")
                        local intel_score
                        intel_score=$(echo "$decide_result" | python3 -c "import json,sys; print(json.load(sys.stdin).get('complexity_score',''))" 2>/dev/null || echo "")
                        log_info "  Intelligence decision: mode=$intel_mode model=$intel_model turns=$intel_turns complexity=$intel_score"
                        # Update queue item with complexity score and mode
                        local _iqid
                        _iqid=$(queue_find_item "$repo" "$ei")
                        if [ -n "$_iqid" ] && [ -n "$intel_score" ]; then
                            queue_api PUT "/api/queue/$_iqid" "{\"mode\":\"$intel_mode\",\"complexity_score\":$intel_score}" >/dev/null 2>&1 &
                        fi
                    fi
                fi
            fi

            if [ "$is_parallel" = true ]; then
                # Parallel mode: wait for a slot, then spawn in background
                wait_for_slot "$max_concurrent"

                log_info "  Spawning employee $ei for $repo in background (budget: $employee_turns turns)"
                run_employee "$repo" "$workspace" "$i" "$ei" "$employee_turns" "$intel_mode" "$intel_model" "$intel_turns" "$intel_esc_file" &
                CHILD_PIDS+=($!)
                has_work=true
            else
                # Sequential mode (backward compatible): run blocking
                log_info "  Running employee $ei for $repo sequentially (budget: $employee_turns turns)"
                run_employee "$repo" "$workspace" "$i" "$ei" "$employee_turns" "$intel_mode" "$intel_model" "$intel_turns" "$intel_esc_file"
                has_work=true

                # Pause between employees in sequential mode
                if [ "$ei" -lt $((employees_this_project - 1)) ] || [ "$i" -lt $((project_count - 1)) ]; then
                    log_info "Pausing 10s before next employee..."
                    sleep 10
                fi
            fi
        done
    done

    # Wait for all parallel employees to finish
    if [ "$is_parallel" = true ]; then
        wait_for_all_children
    fi

    fi  # end of legacy mode (if [ "$has_work" = false ])

    if [ "$has_work" = false ]; then
        log_warn "No employees ran. Exiting."
        exit 0
    fi

    # ---- PHASE 1.5: Collect worktree reports and clean up ----
    for ((i = 0; i < project_count; i++)); do
        local repo_wt enabled_wt
        repo_wt=$(get_project_field "$i" "repo")
        enabled_wt=$(get_project_field "$i" "enabled" 2>/dev/null || echo "true")
        [ "$enabled_wt" = "false" ] && continue

        local name_wt
        name_wt=$(repo_name "$repo_wt")
        local main_ws_wt="$WORKSPACES_DIR/$name_wt"

        # Copy employee reports from worktrees to main workspace before cleanup
        for wt_dir in "$WORKSPACES_DIR/${name_wt}-e"*; do
            if [ -d "$wt_dir" ]; then
                for wt_report in "$wt_dir"/.claude-employee-report*.json; do
                    if [ -f "$wt_report" ]; then
                        local report_basename
                        report_basename=$(basename "$wt_report")
                        cp "$wt_report" "$main_ws_wt/$report_basename"
                        log_info "Copied worktree report: $report_basename from $wt_dir"
                    fi
                done
            fi
        done

        # Clean up worktrees
        cleanup_worktrees "$repo_wt" 2>/dev/null || true

        # Clean up assignment files and plan files
        rm -f "$main_ws_wt"/.claude-assignment-*.json 2>/dev/null || true
        rm -f "$main_ws_wt"/.claude-employee-plan-*.json 2>/dev/null || true
        rm -f "$main_ws_wt"/.claude-plan-feedback-*.json 2>/dev/null || true
        # Note: .claude-approved-plan-*.json is cleaned up AFTER manager review (needed for cross-reference)
    done

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
                            local pr_url
                            pr_url=$(gh pr create --repo "$repo_cg" --head "$conf_branch" \
                                --title "[auto-pr] Issue #${conf_issue}" \
                                --body "## Auto-PR (Confidence Gate)

Confidence: **${conf_confidence}** (above threshold)
Tests: Passed
Mode: $(python3 -c "import json; print(json.load(open('$report_cg')).get('mode',''))" 2>/dev/null || echo "unknown")

This PR was auto-created because the employee's work passed the confidence gate.
Human review is still required for merge.

---
Run: $RUN_ID | \`[auto-pr]\`" 2>/dev/null || echo "")
                            if [ -n "$pr_url" ]; then
                                log_ok "  Auto-PR created: $pr_url"
                                webhook_event "intelligence.confidence_gate_passed" "\"project\":\"$repo_cg\",\"confidence\":$conf_confidence,\"branch\":\"$conf_branch\",\"pr_url\":\"$pr_url\""
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
        webhook_event "run_complete" "\"status\":\"no_reports\""
        exit 0
    fi

    if ! check_rate_limit; then
        log_warn "Rate limit reached before manager review. Employee work stays local."
        notify "rate_limit" "Rate limit reached before manager review in run $RUN_ID"
        exit 0
    fi

    local review_package
    review_package=$(collect_employee_reports "$project_count")
    log_info "Review package: $review_package"

    local verdicts_file
    verdicts_file=$(run_manager_review "$review_package")

    # ---- PHASE 3: Execute verdicts ----
    execute_verdicts "$verdicts_file"

    # ---- PHASE 3a: Clean up approved plan files (no longer needed after review) ----
    for ((i = 0; i < project_count; i++)); do
        local repo_ap
        repo_ap=$(get_project_field "$i" "repo")
        local name_ap
        name_ap=$(repo_name "$repo_ap")
        rm -f "$WORKSPACES_DIR/$name_ap"/.claude-approved-plan-*.json 2>/dev/null || true
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
    webhook_event "run_complete" "\"status\":\"success\""
}

main "$@"
