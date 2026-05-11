#!/usr/bin/env bash
# promote.sh - Standalone promotion of validated features from autonomous/dev to main.
# Can be run independently: systemd timer, dashboard trigger, or manually.
#
# Usage:
#   promote.sh --validate-and-promote          # validate dev, promote if green
#   promote.sh --promote-only                  # promote without re-validating
#   promote.sh --validate-only                 # validate only, no promotion
#   promote.sh --project owner/repo            # target a single project
#   promote.sh --strategy batch|individual     # override promotion strategy

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${STATION_CONFIG:-/home/claude-agent/.claude/autonomous/manager-config.json}"
WORKSPACES_DIR="${STATION_WORKSPACES:-/home/claude-agent/workspaces}"
RUN_ID="promote-$(date -u +%Y%m%dT%H%M%SZ)"

# ============================================================================
# MINIMAL HELPERS (subset of run-manager.sh, kept standalone)
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

repo_name() { echo "$1" | cut -d'/' -f2; }

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

webhook_event() {
    local event="$1"; shift
    local payload="$*"
    local webhook_url
    webhook_url=$(json_get "$CONFIG_FILE" "dashboard.webhook_url" 2>/dev/null || echo "")
    [ -z "$webhook_url" ] && webhook_url="http://127.0.0.1:8420/api/webhook/run-event"
    local webhook_secret
    webhook_secret="${STATION_WEBHOOK_SECRET:-$(json_get "$CONFIG_FILE" "dashboard.webhook_secret" 2>/dev/null || echo "")}"
    local -a auth_header=()
    if [ -n "$webhook_secret" ]; then
        auth_header=(-H "X-Webhook-Token: $webhook_secret")
    fi
    curl -s --max-time 3 -X POST "$webhook_url" \
        -H "Content-Type: application/json" \
        "${auth_header[@]}" \
        -d "{\"event\":\"$event\",\"run_id\":\"$RUN_ID\",\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",${payload}}" \
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
# SOURCE INTEGRATION BRANCH LIBRARY
# ============================================================================

# shellcheck source=integration-branch.sh
source "$SCRIPT_DIR/integration-branch.sh"

# ============================================================================
# CLI ARGUMENT PARSING
# ============================================================================

MODE=""               # validate-and-promote | promote-only | validate-only
TARGET_PROJECT=""     # empty = all enabled projects
STRATEGY_OVERRIDE=""  # empty = use config value

usage() {
    cat << 'EOF'
Promote validated features from autonomous/dev to main.

Usage: promote.sh <MODE> [OPTIONS]

Modes (exactly one required):
  --validate-and-promote   Validate dev branch, promote if tests pass
  --promote-only           Promote without re-validating (assumes already green)
  --validate-only          Run validation only, do not promote

Options:
  --project <owner/repo>   Target a single project (default: all enabled)
  --strategy <strategy>    Override promotion strategy: batch | individual
  --help                   Show this help message
EOF
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --validate-and-promote) MODE="validate-and-promote"; shift ;;
        --promote-only)         MODE="promote-only";         shift ;;
        --validate-only)        MODE="validate-only";        shift ;;
        --project)              TARGET_PROJECT="$2";         shift 2 ;;
        --strategy)             STRATEGY_OVERRIDE="$2";      shift 2 ;;
        --help)                 usage ;;
        *)
            log_error "Unknown argument: $1"
            usage
            ;;
    esac
done

if [ -z "$MODE" ]; then
    log_error "A mode is required (--validate-and-promote, --promote-only, or --validate-only)"
    usage
fi

# ============================================================================
# PREFLIGHT
# ============================================================================

if [ ! -f "$CONFIG_FILE" ]; then
    log_error "Config file not found: $CONFIG_FILE"
    exit 1
fi

if ! integration_enabled; then
    log_error "Integration branch feature is not enabled in config"
    exit 1
fi

log_info "=== promote.sh starting (mode=$MODE, run=$RUN_ID) ==="
webhook_event "promote_start" "\"mode\":\"$MODE\",\"target_project\":\"${TARGET_PROJECT:-all}\"" >&2

# ============================================================================
# MAIN LOOP
# ============================================================================

project_count=$(get_project_count)
overall_exit=0

for (( i=0; i<project_count; i++ )); do
    project=$(get_project_field "$i" "repo")
    enabled=$(get_project_field "$i" "enabled" 2>/dev/null || echo "true")
    [ "$enabled" = "false" ] && continue

    # Filter to single project if requested
    if [ -n "$TARGET_PROJECT" ] && [ "$project" != "$TARGET_PROJECT" ]; then
        continue
    fi

    base_branch=$(get_project_field "$i" "branch" 2>/dev/null || echo "main")
    # promotion_target = where the integration meta-PR is opened.
    # Falls back to base_branch (the project trunk) when unset.
    promotion_target=$(get_project_field "$i" "promotion_target" 2>/dev/null || echo "")
    [ -z "$promotion_target" ] && promotion_target="$base_branch"
    setup_script=$(get_project_field "$i" "setup_script" 2>/dev/null || echo "")
    strategy="${STRATEGY_OVERRIDE:-$(json_get "$CONFIG_FILE" "integration.promotion_strategy" 2>/dev/null || echo "batch")}"

    log_info "--- Processing $project (base=$base_branch, promotion_target=$promotion_target, strategy=$strategy) ---"

    # Ensure workspace exists
    name=$(repo_name "$project")
    workspace="$WORKSPACES_DIR/$name"
    if [ ! -d "$workspace" ]; then
        log_warn "Workspace $workspace not found, skipping $project"
        continue
    fi

    # Sync dev branch with the promotion target before any operation.
    # Rebasing onto promotion_target keeps the meta-PR diff minimal
    # (only the agent's commits, not unrelated trunk drift).
    if ! sync_dev_with_main "$project" "$promotion_target"; then
        log_warn "Sync failed for $project, skipping"
        overall_exit=1
        continue
    fi

    # --- Validate (if mode requires it) ---
    if [ "$MODE" = "validate-and-promote" ] || [ "$MODE" = "validate-only" ]; then
        if validate_dev "$project" "$setup_script"; then
            log_ok "Validation passed for $project"
        else
            log_error "Validation failed for $project -- skipping promotion"
            overall_exit=1
            continue
        fi
    fi

    # --- Promote (if mode requires it) ---
    if [ "$MODE" = "validate-and-promote" ] || [ "$MODE" = "promote-only" ]; then
        if promote_to_main "$project" "$promotion_target" "$strategy"; then
            log_ok "Promotion complete for $project"
        else
            log_error "Promotion failed for $project"
            overall_exit=1
        fi
    fi
done

# ============================================================================
# SUMMARY
# ============================================================================

if [ "$overall_exit" -eq 0 ]; then
    log_ok "=== promote.sh finished successfully (mode=$MODE) ==="
    webhook_event "promote_complete" "\"mode\":\"$MODE\",\"status\":\"success\"" >&2
else
    log_warn "=== promote.sh finished with errors (mode=$MODE) ==="
    webhook_event "promote_complete" "\"mode\":\"$MODE\",\"status\":\"partial_failure\"" >&2
fi

exit "$overall_exit"
