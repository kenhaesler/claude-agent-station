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
        log_info "Pulling latest for $repo..." >&2
        cd "$workspace"
        git checkout main 2>/dev/null >&2 || git checkout master 2>/dev/null >&2 || true

        # Clean up stale autonomous branches from previous failed runs
        local stale_branches
        stale_branches=$(git branch --list 'autonomous/*' 2>/dev/null | tr -d ' *')
        if [ -n "$stale_branches" ]; then
            log_info "Cleaning up stale autonomous branches..." >&2
            echo "$stale_branches" | while IFS= read -r b; do
                git branch -D "$b" 2>/dev/null >&2 || true
            done
        fi

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
    local name
    name=$(repo_name "$repo")
    local mode
    mode=$(get_project_field "$project_index" "mode" 2>/dev/null || echo "full")
    local custom_instructions
    custom_instructions=$(get_project_field "$project_index" "custom_instructions" 2>/dev/null || echo "")

    log_info "=========================================="
    log_info "EMPLOYEE: $repo (mode: $mode)"
    log_info "Workspace: $workspace"
    log_info "=========================================="

    webhook_event "employee_start" "\"project\":\"$repo\",\"mode\":\"$mode\""

    local model max_turns max_budget
    model=$(json_get "$CONFIG_FILE" "models.employee" 2>/dev/null || echo "claude-opus-4-6")
    max_turns=$(json_get "$CONFIG_FILE" "limits.max_employee_turns" 2>/dev/null || echo "200")
    max_budget=$(json_get "$CONFIG_FILE" "limits.max_employee_budget_usd" 2>/dev/null || echo "25.00")

    # Analyze mode uses Sonnet (cheaper — no code changes, just analysis)
    if [ "$mode" = "analyze" ]; then
        model="claude-sonnet-4-6"
        max_turns=50
        max_budget="5.00"
    fi

    # Determine fallback model (must differ from primary)
    local fallback_model="claude-sonnet-4-6"
    if [ "$model" = "claude-sonnet-4-6" ]; then
        fallback_model="claude-haiku-4-5-20251001"
    fi

    # Clean up any previous report
    rm -f "$workspace/.claude-employee-report.json"

    # Select prompt based on mode
    local system_prompt employee_prompt
    if [ "$mode" = "analyze" ]; then
        system_prompt="$SCRIPT_DIR/../prompts/analyst.md"
        employee_prompt="Analyze the repository: $repo

Environment variables available:
- GITHUB_REPO=$repo
- GH_TOKEN is set

Your workspace is: $workspace

Analyze the codebase for bugs, technical debt, security issues, and improvement opportunities. Create well-defined GitHub issues for each finding. Refine any existing vague issues with analysis details.

Write your report to $workspace/.claude-employee-report.json

Remember: You are in ANALYZE mode. Read and analyze only — do NOT modify any source files."
    else
        system_prompt="$SCRIPT_DIR/../prompts/employee.md"
        employee_prompt="Work on the repository: $repo

Environment variables available:
- GITHUB_REPO=$repo
- GH_TOKEN is set

Your workspace is: $workspace

Find the most actionable open issue, implement it fully, run tests, and write your report to $workspace/.claude-employee-report.json

Remember: commit locally but NEVER push. The manager will review and push if approved."
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
    cmd+=(--max-budget-usd "$max_budget")
    cmd+=(--system-prompt-file "$system_prompt")
    # No --allowedTools/--disallowedTools: full VM access, prompt-enforced guardrails

    log_info "Employee command: ${cmd[*]} '<prompt>'"

    if [ "$DRY_RUN" = true ]; then
        log_info "[DRY RUN] Would run employee for: $repo"
        return 0
    fi

    local stream_file="$LOG_DIR/run-${RUN_ID}-employee-${name}.stream.jsonl"
    local stderr_file="$LOG_DIR/run-${RUN_ID}-employee-${name}.stderr.log"
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
                local result_cost result_turns
                result_cost=$(echo "$line" | python3 -c "import json,sys; print(f'{json.load(sys.stdin).get(\"total_cost_usd\",0):.2f}')" 2>/dev/null || echo "?")
                result_turns=$(echo "$line" | python3 -c "import json,sys; print(json.load(sys.stdin).get('num_turns',0))" 2>/dev/null || echo "?")
                log_info "  Employee result: Turns=$result_turns Cost=\$$result_cost"
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

    webhook_event "employee_complete" "\"project\":\"$repo\",\"exit_code\":$exit_code"

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
        local name
        name=$(repo_name "$repo")
        local workspace="$WORKSPACES_DIR/$name"

        echo "---" >> "$review_package"
        echo "## Project: $repo" >> "$review_package"
        echo "" >> "$review_package"

        # Employee report
        local report_file="$workspace/.claude-employee-report.json"
        if [ -f "$report_file" ]; then
            echo "### Employee Report" >> "$review_package"
            echo '```json' >> "$review_package"
            cat "$report_file" >> "$review_package"
            echo '```' >> "$review_package"
            echo "" >> "$review_package"

            # Git diff (main vs current branch)
            cd "$workspace"
            local current_branch
            current_branch=$(git branch --show-current 2>/dev/null || echo "main")

            # Detect base branch from employee report or default
            local report_base_branch
            report_base_branch=$(python3 -c "
import json
with open('$report_file') as f:
    print(json.load(f).get('base_branch', ''))
" 2>/dev/null || echo "")
            [ -z "$report_base_branch" ] && report_base_branch="main"

            if [ "$current_branch" != "$report_base_branch" ]; then
                echo "### Git Diff ($report_base_branch..$current_branch)" >> "$review_package"
                echo '```diff' >> "$review_package"
                git diff "$report_base_branch".."$current_branch" 2>/dev/null | head -2000 >> "$review_package"
                echo '```' >> "$review_package"
                echo "" >> "$review_package"

                echo "### Git Log" >> "$review_package"
                echo '```' >> "$review_package"
                git log "$report_base_branch".."$current_branch" --oneline 2>/dev/null >> "$review_package"
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
                echo "### No changes (employee stayed on main)" >> "$review_package"
                echo "" >> "$review_package"
            fi
        else
            echo "### No employee report found" >> "$review_package"
            echo "Employee did not produce a report file. Check logs." >> "$review_package"
            echo "" >> "$review_package"
        fi
    done

    echo "$review_package"
}

run_manager_review() {
    local review_package="$1"
    local verdicts_file="$LOG_DIR/run-${RUN_ID}-verdicts.json"

    log_info "==========================================" >&2
    log_info "MANAGER: Reviewing employee work" >&2
    log_info "==========================================" >&2

    webhook_event "manager_review" "\"review_package\":\"$review_package\""

    local model max_turns max_budget
    model=$(json_get "$CONFIG_FILE" "models.manager" 2>/dev/null || echo "claude-sonnet-4-6")
    max_turns=$(json_get "$CONFIG_FILE" "limits.max_manager_turns" 2>/dev/null || echo "30")
    max_budget=$(json_get "$CONFIG_FILE" "limits.max_manager_budget_usd" 2>/dev/null || echo "3.00")

    # Determine fallback model for manager
    local manager_fallback="claude-haiku-4-5-20251001"
    if [ "$model" = "claude-haiku-4-5-20251001" ]; then
        manager_fallback="claude-sonnet-4-6"
    fi

    local -a cmd=(claude -p --verbose --output-format stream-json --no-session-persistence --dangerously-skip-permissions)
    cmd+=(--model "$model")
    cmd+=(--fallback-model "$manager_fallback")
    cmd+=(--max-turns "$max_turns")
    cmd+=(--max-budget-usd "$max_budget")
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
                local result_cost
                result_cost=$(echo "$line" | python3 -c "import json,sys; print(f'{json.load(sys.stdin).get(\"total_cost_usd\",0):.2f}')" 2>/dev/null || echo "?")
                log_info "  Manager review cost: \$$result_cost" >&2
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
                log_info "REJECT: Resetting workspace"
                git checkout "$base_branch" 2>/dev/null || true
                git branch -D "$branch" 2>/dev/null || true
                log_ok "Rejected changes cleaned up"

                if [ -n "$issue_number" ] && [ "$issue_number" != "None" ] && [ "$issue_number" != "null" ]; then
                    gh issue comment "$issue_number" --repo "$project" --body "🤖 **Manager verdict: REJECTED** — $reasoning. Will retry next cycle.

Run: $RUN_ID" 2>/dev/null || true
                    # Clean up agent labels
                    gh issue edit "$issue_number" --repo "$project" --remove-label "autonomous-agent/done" 2>/dev/null || true
                    gh issue edit "$issue_number" --repo "$project" --remove-label "autonomous-agent/in-progress" 2>/dev/null || true
                fi

                notify "reject" "REJECTED: $project #$issue_number - $reasoning"
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

        # Extract cost data from stream files
        echo "## Cost Summary"
        local total_cost=0
        for stream in "$LOG_DIR"/run-${RUN_ID}-*.stream.jsonl; do
            [ -f "$stream" ] || continue
            local stream_name cost
            stream_name=$(basename "$stream" .stream.jsonl | sed "s/run-${RUN_ID}-//")
            cost=$(tail -1 "$stream" | python3 -c "import json,sys; print(f'{json.load(sys.stdin).get(\"total_cost_usd\",0):.4f}')" 2>/dev/null || echo "0.0000")
            echo "- **$stream_name**: \$$cost"
            total_cost=$(python3 -c "print(f'{$total_cost + $cost:.4f}')" 2>/dev/null || echo "?")
        done
        echo "- **Total**: \$$total_cost"
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

    webhook_event "run_start" "\"project_count\":$project_count"

    # ---- PHASE 1: Run employees per project (sequentially) ----
    local has_work=false

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

        log_info "Project $((i+1))/$project_count: $repo (priority: $priority)"

        # Check rate limit before each employee
        if ! check_rate_limit; then
            log_warn "Rate limit reached. Stopping before $repo"
            notify "rate_limit" "Rate limit reached before $repo in run $RUN_ID"
            break
        fi

        # Setup workspace
        local workspace
        workspace=$(setup_workspace "$repo") || {
            log_error "Failed to setup workspace for $repo"
            continue
        }

        # Run employee
        run_employee "$repo" "$workspace" "$i"
        has_work=true

        # Pause between employees
        log_info "Pausing 10s before next project..."
        sleep 10
    done

    if [ "$has_work" = false ]; then
        log_warn "No employees ran. Exiting."
        exit 0
    fi

    # ---- PHASE 2: Manager review ----

    # Check if any employee produced a report — skip manager if nothing to review
    local any_reports=false
    for ((i = 0; i < project_count; i++)); do
        local repo_check
        repo_check=$(get_project_field "$i" "repo")
        local name_check
        name_check=$(repo_name "$repo_check")
        if [ -f "$WORKSPACES_DIR/$name_check/.claude-employee-report.json" ]; then
            any_reports=true
            break
        fi
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

    # ---- PHASE 4: Write digest ----
    write_digest "$verdicts_file"

    log_ok "Run $RUN_ID complete"
    notify "complete" "Run $RUN_ID finished successfully"
    webhook_event "run_complete" "\"status\":\"success\""
}

main "$@"
