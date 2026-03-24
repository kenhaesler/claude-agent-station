# sprint-cycle.sh
# Library of functions for orchestrating multi-role sprint analysis cycles.
# Sourced by run-manager.sh — must NOT have set -euo pipefail or shebang.
#
# Depends on run-manager.sh globals:
#   log_info, log_warn, log_error, log_ok, webhook_event, queue_api,
#   json_get, get_project_field, repo_name, resolve_prompt,
#   $WORKSPACES_DIR, $CONFIG_FILE, $RUN_ID, $SCRIPT_DIR, $DRY_RUN
#
# Depends on integration-branch.sh:
#   integration_enabled, validate_dev

# ============================================================================
# SPRINT CONFIGURATION
# ============================================================================

sprint_enabled() {
    local enabled
    enabled=$(json_get "$CONFIG_FILE" "sprint.enabled" 2>/dev/null || echo "false")
    [ "$enabled" = "true" ]
}

# ============================================================================
# SPRINT BRIEF
# ============================================================================

create_sprint_brief() {
    local workspace="$1" project="$2"
    local sprint_dir="$workspace/.claude-sprint"
    mkdir -p "$sprint_dir"

    local sprint_id="sprint-${RUN_ID}-$(date +%s)"

    # Gather open issue count and categories
    local open_count=0 issue_categories="{}"
    if command -v gh >/dev/null 2>&1; then
        open_count=$(gh issue list --repo "$project" --state open --limit 1000 --json number 2>/dev/null \
            | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

        issue_categories=$(gh issue list --repo "$project" --state open --limit 200 --json labels 2>/dev/null \
            | python3 -c "
import json, sys, collections
issues = json.load(sys.stdin)
cats = collections.Counter()
for issue in issues:
    for label in issue.get('labels', []):
        name = label.get('name', '') if isinstance(label, dict) else str(label)
        if name:
            cats[name] += 1
print(json.dumps(dict(cats.most_common(20))))
" 2>/dev/null || echo "{}")
    fi

    # Check dev branch state if integration is enabled
    local dev_state="disabled"
    if integration_enabled 2>/dev/null; then
        local name
        name=$(repo_name "$project")
        local ws="$WORKSPACES_DIR/$name"
        if [ -d "$ws/.git" ]; then
            local dev_branch
            dev_branch=$(get_dev_branch 2>/dev/null || echo "autonomous/dev")
            local ahead_count=0
            cd "$ws" 2>/dev/null && {
                git fetch origin "$dev_branch" 2>/dev/null || true
                git fetch origin main 2>/dev/null || true
                ahead_count=$(git rev-list --count "origin/main..origin/$dev_branch" 2>/dev/null || echo "0")
            }
            dev_state="active"
        fi
    fi

    # Write brief.json
    python3 -c "
import json, datetime
brief = {
    'sprint_id': '$sprint_id',
    'project': '$project',
    'run_id': 'run-$RUN_ID',
    'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'open_issues': $open_count,
    'issue_categories': $issue_categories,
    'dev_branch_state': '$dev_state',
    'ahead_of_main': ${ahead_count:-0}
}
with open('$sprint_dir/brief.json', 'w') as f:
    json.dump(brief, f, indent=2)
" 2>/dev/null || {
        log_warn "Failed to generate sprint brief via python, writing minimal JSON"
        echo "{\"sprint_id\":\"$sprint_id\",\"project\":\"$project\",\"run_id\":\"run-$RUN_ID\",\"open_issues\":$open_count}" \
            > "$sprint_dir/brief.json"
    }

    echo "$sprint_id"
}

# ============================================================================
# RUN A SINGLE ANALYST ROLE
# ============================================================================

run_analyst_role() {
    local role="$1" project="$2" workspace="$3" sprint_id="$4"
    local role_prompt_file="$SCRIPT_DIR/../prompts/roles/${role}.md"

    if [ ! -f "$role_prompt_file" ]; then
        log_warn "Role prompt not found: $role_prompt_file"
        return 0
    fi

    local role_instructions
    role_instructions=$(cat "$role_prompt_file")

    local analyst_prompt_file
    analyst_prompt_file=$(resolve_prompt "analyst")

    local sprint_context="Sprint ID: $sprint_id. Sprint workspace: $workspace/.claude-sprint/"

    mkdir -p "$workspace/.claude-sprint/$role"

    local role_turns
    role_turns=$(json_get "$CONFIG_FILE" "sprint.role_turns" 2>/dev/null || echo "40")

    local model
    model=$(json_get "$CONFIG_FILE" "models.analyst" 2>/dev/null || echo "claude-sonnet-4-6")

    local fallback_model="claude-sonnet-4-6"
    [ "$model" = "claude-sonnet-4-6" ] && fallback_model="claude-haiku-4-5-20251001"

    log_info "Running sprint role: $role (model=$model, turns=$role_turns)"
    webhook_event "role_start" "\"sprint_id\":\"$sprint_id\",\"role\":\"$role\",\"project\":\"$project\",\"max_turns\":$role_turns" >&2

    if [ "$DRY_RUN" = true ]; then
        log_info "[DRY RUN] Would run analyst role: $role"
        webhook_event "role_complete" "\"sprint_id\":\"$sprint_id\",\"role\":\"$role\",\"proposals_count\":0,\"dry_run\":true" >&2
        return 0
    fi

    local report_file="$workspace/.claude-employee-report.json"
    local stream_file="$workspace/.claude-sprint/${role}/stream.jsonl"
    local stderr_file="$workspace/.claude-sprint/${role}/stderr.log"

    # Build the combined prompt: analyst base + role instructions + sprint context
    local combined_prompt
    combined_prompt="Analyze the repository: $project

Environment variables available:
  GITHUB_REPO=$project

<SPRINT_ROLE>
$role_instructions
</SPRINT_ROLE>

<SPRINT_CONTEXT>
$sprint_context
Read the sprint brief at .claude-sprint/brief.json for context.
Read previous role findings at .claude-sprint/*/findings.json.
Write YOUR findings to .claude-sprint/$role/findings.json.
Do NOT create GitHub issues directly — mark proposals with create_github_issue: true.
</SPRINT_CONTEXT>

Report file path: $report_file"

    # Run the analyst with role-specific instructions
    cd "$workspace"
    local -a cmd=(claude -p --verbose --output-format stream-json --no-session-persistence --dangerously-skip-permissions)
    cmd+=(--model "$model")
    cmd+=(--fallback-model "$fallback_model")
    cmd+=(--max-turns "$role_turns")
    cmd+=(--system-prompt-file "$analyst_prompt_file")

    GITHUB_REPO="$project" "${cmd[@]}" -- "$combined_prompt" > "$stream_file" 2>>"$stderr_file" || true

    # Check if findings were created
    local findings_file="$workspace/.claude-sprint/$role/findings.json"
    local findings_count=0
    if [ -f "$findings_file" ]; then
        findings_count=$(python3 -c "
import json
with open('$findings_file') as f:
    data = json.load(f)
print(len(data.get('proposals', [])))
" 2>/dev/null || echo "0")
    fi

    webhook_event "role_complete" "\"sprint_id\":\"$sprint_id\",\"role\":\"$role\",\"project\":\"$project\",\"proposals_count\":$findings_count" >&2
    log_ok "Role $role complete: $findings_count proposals"
}

# ============================================================================
# CREATE GITHUB ISSUES FROM FINDINGS
# ============================================================================

create_issues_from_findings() {
    local workspace="$1" project="$2"
    local sprint_dir="$workspace/.claude-sprint"
    local issues_created=0

    # Collect all findings files
    local findings_files
    findings_files=$(find "$sprint_dir" -name "findings.json" -path "*/*/findings.json" 2>/dev/null || echo "")

    if [ -z "$findings_files" ]; then
        log_info "No findings files found — skipping issue creation"
        return 0
    fi

    # Use python to parse all findings and create issues for flagged proposals
    local proposals_json
    proposals_json=$(python3 -c "
import json, glob, os

sprint_dir = '$sprint_dir'
all_proposals = []

for fpath in sorted(glob.glob(os.path.join(sprint_dir, '*/findings.json'))):
    role = os.path.basename(os.path.dirname(fpath))
    try:
        with open(fpath) as f:
            data = json.load(f)
        for p in data.get('proposals', []):
            if p.get('create_github_issue'):
                p['_source_role'] = role
                # Collect cross-references from reviews
                for r in data.get('reviews', []):
                    if r.get('target_id') == p.get('id'):
                        p.setdefault('_reviews', []).append(r)
                all_proposals.append(p)
    except (json.JSONDecodeError, OSError):
        pass

print(json.dumps(all_proposals))
" 2>/dev/null || echo "[]")

    local proposal_count
    proposal_count=$(echo "$proposals_json" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

    if [ "$proposal_count" = "0" ]; then
        log_info "No proposals flagged for GitHub issue creation"
        return 0
    fi

    log_info "Creating $proposal_count GitHub issue(s) from sprint findings"

    # Create an issue for each flagged proposal
    echo "$proposals_json" | python3 -c "
import json, sys, subprocess, os

proposals = json.load(sys.stdin)
project = '$project'
created = 0

for p in proposals:
    title = p.get('title', 'Sprint finding: ' + p.get('id', 'unknown'))
    role = p.get('_source_role', 'unknown')
    severity = p.get('severity', 'medium')
    description = p.get('description', '')
    rationale = p.get('rationale', '')
    reviews = p.get('_reviews', [])

    # Build issue body
    body_parts = [
        '## Description',
        '',
        description,
        '',
        '## Source',
        '',
        f'- **Sprint role**: {role}',
        f'- **Proposal ID**: {p.get(\"id\", \"N/A\")}',
        f'- **Severity**: {severity}',
    ]

    if rationale:
        body_parts += ['', '## Rationale', '', rationale]

    if p.get('files'):
        body_parts += ['', '## Files Referenced', '']
        for fref in p['files']:
            body_parts.append(f'- \`{fref}\`')

    if p.get('acceptance_criteria'):
        body_parts += ['', '## Acceptance Criteria', '']
        for ac in p['acceptance_criteria']:
            body_parts.append(f'- [ ] {ac}')

    if reviews:
        body_parts += ['', '## Cross-Role Reviews', '']
        for r in reviews:
            body_parts.append(f'- **{r.get(\"target_role\", \"unknown\")}**: {r.get(\"assessment\", \"\")}')

    body_parts += [
        '',
        '---',
        f'*Created by autonomous sprint cycle (role: {role})*',
    ]
    body = '\n'.join(body_parts)

    # Determine labels
    labels = ['autonomous-agent/analyzed', 'sprint']
    type_label = p.get('type', 'enhancement')
    if type_label:
        labels.append(type_label)
    severity_map = {'critical': 'priority/critical', 'high': 'priority/high', 'medium': 'priority/medium', 'low': 'priority/low'}
    prio_label = severity_map.get(severity, 'priority/medium')
    labels.append(prio_label)

    cmd = [
        'gh', 'issue', 'create',
        '--repo', project,
        '--title', title,
        '--body', body,
    ]
    for l in labels:
        cmd += ['--label', l]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            created += 1
            url = result.stdout.strip()
            print(f'Created issue: {url}', file=sys.stderr)
        else:
            print(f'Failed to create issue \"{title}\": {result.stderr.strip()}', file=sys.stderr)
    except Exception as e:
        print(f'Error creating issue \"{title}\": {e}', file=sys.stderr)

print(created)
" 2>/dev/null
    issues_created=$?

    # Read actual count from stdout (python prints it)
    log_ok "Sprint issue creation complete"
    return 0
}

# ============================================================================
# MAIN SPRINT ORCHESTRATOR
# ============================================================================

sprint_cycle() {
    local project="$1" workspace="$2" conf_branch="$3" setup_script="${4:-}" project_idx="$5"

    log_info "========================================"
    log_info "SPRINT CYCLE: $project"
    log_info "========================================"

    webhook_event "sprint_start" "\"project\":\"$project\",\"run_id\":\"run-$RUN_ID\"" >&2

    # Step 1: Create sprint workspace and brief
    local sprint_id
    sprint_id=$(create_sprint_brief "$workspace" "$project")
    log_ok "Sprint brief created: $sprint_id"

    # Step 2: Read sprint configuration
    local analyze_threshold
    analyze_threshold=$(json_get "$CONFIG_FILE" "sprint.analyze_threshold" 2>/dev/null || echo "15")

    local auto_implement
    auto_implement=$(json_get "$CONFIG_FILE" "sprint.auto_implement" 2>/dev/null || echo "false")

    local creative_roles="visionary architect designer"
    local defensive_roles="security quality performance"

    # Allow config override of role lists
    local config_creative
    config_creative=$(json_get "$CONFIG_FILE" "sprint.creative_roles" 2>/dev/null || echo "")
    [ -n "$config_creative" ] && creative_roles="$config_creative"

    local config_defensive
    config_defensive=$(json_get "$CONFIG_FILE" "sprint.defensive_roles" 2>/dev/null || echo "")
    [ -n "$config_defensive" ] && defensive_roles="$config_defensive"

    # Step 3: Read open issue count from brief
    local open_count
    open_count=$(python3 -c "
import json
with open('$workspace/.claude-sprint/brief.json') as f:
    print(json.load(f).get('open_issues', 0))
" 2>/dev/null || echo "0")

    # Step 4: Run creative roles if below threshold
    local total_roles=0 completed_roles=0

    if [ "$open_count" -lt "$analyze_threshold" ]; then
        log_info "Open issues ($open_count) below threshold ($analyze_threshold) — running creative roles"
        for role in $creative_roles; do
            total_roles=$((total_roles + 1))
            run_analyst_role "$role" "$project" "$workspace" "$sprint_id"
            completed_roles=$((completed_roles + 1))
            log_info "Sprint progress: $completed_roles roles completed"
        done
    else
        log_info "Open issues ($open_count) at/above threshold ($analyze_threshold) — skipping creative roles"
    fi

    # Step 5: Always run defensive roles
    log_info "Running defensive roles"
    for role in $defensive_roles; do
        total_roles=$((total_roles + 1))
        run_analyst_role "$role" "$project" "$workspace" "$sprint_id"
        completed_roles=$((completed_roles + 1))
        log_info "Sprint progress: $completed_roles roles completed"
    done

    # Step 6: Create GitHub issues from findings
    log_info "Processing findings for issue creation"
    create_issues_from_findings "$workspace" "$project"

    # Step 7: If auto_implement, report that normal full mode should follow
    if [ "$auto_implement" = "true" ]; then
        log_info "Sprint auto_implement is enabled — full mode will follow"
        webhook_event "sprint_auto_implement" "\"sprint_id\":\"$sprint_id\",\"project\":\"$project\"" >&2
    fi

    # Step 8: If integration enabled and had work, validate dev
    if integration_enabled 2>/dev/null; then
        log_info "Integration enabled — validating dev branch"
        validate_dev "$project" "$setup_script" || {
            log_warn "Dev validation failed after sprint cycle"
        }
    fi

    # Step 9: Emit sprint_complete summary
    local total_proposals=0
    total_proposals=$(python3 -c "
import json, glob, os
total = 0
for fpath in glob.glob('$workspace/.claude-sprint/*/findings.json'):
    try:
        with open(fpath) as f:
            total += len(json.load(f).get('proposals', []))
    except (json.JSONDecodeError, OSError):
        pass
print(total)
" 2>/dev/null || echo "0")

    webhook_event "sprint_complete" "\"sprint_id\":\"$sprint_id\",\"project\":\"$project\",\"roles_completed\":$completed_roles,\"total_proposals\":$total_proposals,\"auto_implement\":\"$auto_implement\"" >&2

    log_ok "Sprint cycle complete: $completed_roles roles, $total_proposals total proposals"
    log_info "========================================"

    # Return the sprint_id for the caller
    echo "$sprint_id"
}
