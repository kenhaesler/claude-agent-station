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
    curl -s --max-time 3 -X POST "$webhook_url" \
        -H "Content-Type: application/json" \
        -d "{\"event\":\"$event\",\"run_id\":\"run-$RUN_ID\",\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",${payload}}" \
        2>/dev/null || true
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

trap 'cleanup_children TERM; cleanup_all_worktrees 2>/dev/null || true; exit 130' SIGTERM
trap 'cleanup_children INT; cleanup_all_worktrees 2>/dev/null || true; exit 130' SIGINT

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
    local session_limit max_percent window_hours
    session_limit=$(json_get "$CONFIG_FILE" "limits.session_limit_24h" 2>/dev/null || echo "50")
    max_percent=$(json_get "$CONFIG_FILE" "limits.max_session_percent" 2>/dev/null || echo "80")
    window_hours=24

    local result
    result=$(python3 -c "
import json, time, os

tracking_file = '$RATE_TRACKING_FILE'
session_limit = int($session_limit)
max_percent = float($max_percent)
now = time.time()
window_sec = $window_hours * 3600
threshold = int(session_limit * max_percent / 100.0)

state = {'window_start': now, 'sessions_used': 0}
if os.path.exists(tracking_file):
    try:
        with open(tracking_file) as f:
            state = json.load(f)
    except (json.JSONDecodeError, KeyError):
        pass

elapsed = now - state.get('window_start', now)
if elapsed >= window_sec:
    print('OK|0|%d' % threshold)
else:
    used = state.get('sessions_used', 0)
    if used >= threshold:
        remaining_min = int((window_sec - elapsed) / 60)
        print('LIMIT|%d|%d|%d' % (used, threshold, remaining_min))
    else:
        print('OK|%d|%d' % (used, threshold))
" 2>/dev/null)

    local status
    IFS='|' read -r status _ <<< "$result"
    if [ "$status" = "LIMIT" ]; then
        log_warn "Session limit reached: $result"
        return 1
    fi
    log_info "Rate limit: $result"
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

# ============================================================================
# TOKEN BUDGET CHECK
# ============================================================================

check_token_budget() {
    # Check if token consumption is within configured limits.
    # Queries the dashboard API for current token usage.
    # Returns 0 if OK to proceed, 1 if budget exceeded.
    local token_limit_daily token_reserve_pct
    token_limit_daily=$(json_get "$CONFIG_FILE" "limits.token_limit_daily" 2>/dev/null || echo "0")
    token_reserve_pct=$(json_get "$CONFIG_FILE" "limits.token_reserve_percent" 2>/dev/null || echo "20")

    # If no daily token limit configured, skip check
    [ "$token_limit_daily" = "0" ] && return 0

    # Try to get token usage from dashboard API
    local usage_json
    usage_json=$(curl -s --max-time 3 "http://127.0.0.1:8420/api/config/token-usage" 2>/dev/null || echo "")
    if [ -z "$usage_json" ]; then
        log_warn "Could not reach dashboard API for token budget check, proceeding anyway"
        return 0
    fi

    local can_spawn
    can_spawn=$(echo "$usage_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('can_spawn_employee', True))" 2>/dev/null || echo "True")

    if [ "$can_spawn" = "False" ]; then
        local daily_total daily_limit
        daily_total=$(echo "$usage_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('daily',{}).get('tokens_total',0))" 2>/dev/null || echo "?")
        daily_limit=$(echo "$usage_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('daily',{}).get('effective_limit',0))" 2>/dev/null || echo "?")
        log_warn "Token budget exceeded: ${daily_total} tokens used, effective limit ${daily_limit} (reserve ${token_reserve_pct}%)"
        return 1
    fi

    log_info "Token budget: OK"
    return 0
}

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
    local assigner_prompt_file="$SCRIPT_DIR/../prompts/assigner.md"
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
    local name
    name=$(repo_name "$repo")
    local mode
    mode=$(get_project_field "$project_index" "mode" 2>/dev/null || echo "full")
    local custom_instructions
    custom_instructions=$(get_project_field "$project_index" "custom_instructions" 2>/dev/null || echo "")

    log_info "=========================================="
    log_info "EMPLOYEE: $repo (mode: $mode, index: $employee_index)"
    log_info "Workspace: $workspace"
    log_info "=========================================="

    webhook_event "employee_start" "\"project\":\"$repo\",\"mode\":\"$mode\",\"employee_index\":$employee_index,\"concurrent_group_id\":\"$CONCURRENT_GROUP_ID\""

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

    # Analyze/plan modes use Sonnet (cheaper — no code changes, just analysis/planning)
    if [ "$mode" = "analyze" ] || [ "$mode" = "plan" ]; then
        model="claude-sonnet-4-6"
        max_turns=50
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
        system_prompt="$SCRIPT_DIR/../prompts/planner.md"
        employee_prompt="Create implementation plans for the repository: $repo

Environment variables available:
- GITHUB_REPO=$repo
- GH_TOKEN is set

Your workspace is: $workspace

Analyze open issues and create detailed implementation plans. Write plans to the dashboard API at http://127.0.0.1:8420/api/plans. Each plan should include step-by-step instructions, files to change, code snippets, and acceptance criteria.

Write your report to $workspace/.claude-employee-report${report_suffix}.json

Remember: You are in PLAN mode. Read and analyze only — do NOT modify any source files."
    elif [ "$mode" = "analyze" ]; then
        system_prompt="$SCRIPT_DIR/../prompts/analyst.md"
        employee_prompt="Analyze the repository: $repo

Environment variables available:
- GITHUB_REPO=$repo
- GH_TOKEN is set

Your workspace is: $workspace

Analyze the codebase for bugs, technical debt, security issues, and improvement opportunities. Create well-defined GitHub issues for each finding. Refine any existing vague issues with analysis details.

Write your report to $workspace/.claude-employee-report${report_suffix}.json

Remember: You are in ANALYZE mode. Read and analyze only — do NOT modify any source files."
    else
        system_prompt="$SCRIPT_DIR/../prompts/employee.md"

        # Check if there's a plan file to implement
        local plan_file="$workspace/.claude-plan-to-implement.json"
        if [ -f "$plan_file" ]; then
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
    # No --allowedTools/--disallowedTools: full VM access, prompt-enforced guardrails

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

    webhook_event "employee_complete" "\"project\":\"$repo\",\"exit_code\":$exit_code,\"employee_index\":$employee_index"

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

        local name
        name=$(repo_name "$repo")
        local workspace="$WORKSPACES_DIR/$name"

        echo "---" >> "$review_package"
        echo "## Project: $repo" >> "$review_package"
        echo "" >> "$review_package"

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
                echo "### ${employee_label}: No changes (employee stayed on $report_base_branch)" >> "$review_package"
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
    cmd+=(--system-prompt-file "$SCRIPT_DIR/../prompts/manager.md")
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

        local project verdict branch issue_number reasoning base_branch
        project=$(echo "$verdict_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('project',''))")
        verdict=$(echo "$verdict_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('verdict','REJECT'))")
        branch=$(echo "$verdict_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('branch',''))")
        issue_number=$(echo "$verdict_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('issue_number',''))")
        reasoning=$(echo "$verdict_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('reasoning',''))")
        base_branch=$(echo "$verdict_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('base_branch','main'))")

        local name
        name=$(repo_name "$project")
        local workspace="$WORKSPACES_DIR/$name"

        log_info "Project: $project | Verdict: $verdict | Issue: #$issue_number | Branch: $branch"
        log_info "Reasoning: $reasoning"

        webhook_event "verdict_execute" "\"project\":\"$project\",\"verdict\":\"$verdict\",\"issue_number\":\"$issue_number\""

        if [ ! -d "$workspace/.git" ]; then
            log_error "Workspace not found: $workspace"
            continue
        fi

        cd "$workspace"

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

    # ---- PHASE 0.5: Pre-assign issues for multi-employee projects ----
    for ((i = 0; i < project_count; i++)); do
        local repo_check enabled_check mode_check
        repo_check=$(get_project_field "$i" "repo")
        enabled_check=$(get_project_field "$i" "enabled" 2>/dev/null || echo "true")
        [ "$enabled_check" = "false" ] && continue
        mode_check=$(get_project_field "$i" "mode" 2>/dev/null || echo "full")

        local employees_for_assign=$max_per_project
        if [ "$mode_check" = "analyze" ] || [ "$mode_check" = "plan" ]; then
            employees_for_assign=1
        fi

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

    # ---- PHASE 1: Run employees per project ----
    local has_work=false
    local is_parallel=false
    [ "$max_concurrent" -gt 1 ] && is_parallel=true

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
        # Analyze/plan modes only use 1 employee
        if [ "$mode_for_project" = "analyze" ] || [ "$mode_for_project" = "plan" ]; then
            employees_this_project=1
        fi

        for ((ei = 0; ei < employees_this_project; ei++)); do
            log_info "Project $((i+1))/$project_count: $repo (priority: $priority, employee: $ei)"

            # Check rate limit before each employee
            if ! check_rate_limit; then
                log_warn "Rate limit reached. Stopping before $repo employee $ei"
                notify "rate_limit" "Rate limit reached before $repo employee $ei in run $RUN_ID"
                break 2
            fi

            # Check token budget before each employee
            if ! check_token_budget; then
                log_warn "Token budget exceeded. Stopping before $repo employee $ei"
                notify "token_budget" "Token budget exceeded before $repo employee $ei in run $RUN_ID"
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

            # Calculate per-employee turn budget
            local employee_turns
            employee_turns=$(calculate_employee_budget "$total_employees" "$priority")

            if [ "$is_parallel" = true ]; then
                # Parallel mode: wait for a slot, then spawn in background
                wait_for_slot "$max_concurrent"

                log_info "  Spawning employee $ei for $repo in background (budget: $employee_turns turns)"
                run_employee "$repo" "$workspace" "$i" "$ei" "$employee_turns" &
                CHILD_PIDS+=($!)
                has_work=true
            else
                # Sequential mode (backward compatible): run blocking
                log_info "  Running employee $ei for $repo sequentially (budget: $employee_turns turns)"
                run_employee "$repo" "$workspace" "$i" "$ei" "$employee_turns"
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

        # Clean up assignment files
        rm -f "$main_ws_wt"/.claude-assignment-*.json 2>/dev/null || true
    done

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

            if ! check_token_budget; then
                log_warn "Token budget exceeded during retry. Remaining retries cancelled."
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
