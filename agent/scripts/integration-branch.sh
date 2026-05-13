# integration-branch.sh
# Library of functions for managing the autonomous/dev integration branch.
# Sourced by run-manager.sh — must NOT have set -euo pipefail or shebang.
#
# Depends on run-manager.sh globals:
#   log_info, log_warn, log_error, log_ok, webhook_event, queue_api,
#   json_get, repo_name, notify, $WORKSPACES_DIR, $CONFIG_FILE, $RUN_ID

# Setup-script validator/runner (issue #179) — definitions live in lib/
# so the test suite can source them without depending on the rest of this
# file's run-manager.sh globals.
# shellcheck source=lib/setup_script.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/setup_script.sh"

# ============================================================================
# INTEGRATION BRANCH CONFIGURATION
# ============================================================================

integration_enabled() {
    local enabled
    enabled=$(json_get "$CONFIG_FILE" "integration.enabled" 2>/dev/null || echo "false")
    [ "$enabled" = "true" ]
}

get_dev_branch() {
    local branch
    branch=$(json_get "$CONFIG_FILE" "integration.dev_branch" 2>/dev/null || echo "")
    echo "${branch:-autonomous/dev}"
}

# Resolve the `gh pr merge` strategy flag from config. Defaults to
# `--merge` (a merge commit) — branch protection on the integration
# branch may require `--squash` or `--rebase` instead, so let the
# operator pick.
get_merge_flag() {
    local s
    s=$(json_get "$CONFIG_FILE" "integration.merge_strategy" 2>/dev/null || echo "merge")
    case "$s" in
        squash) echo "--squash" ;;
        rebase) echo "--rebase" ;;
        merge|"") echo "--merge" ;;
        *)
            log_warn "Unknown integration.merge_strategy '$s' — falling back to --merge" >&2
            echo "--merge"
            ;;
    esac
}

# ============================================================================
# BRANCH LIFECYCLE
# ============================================================================

ensure_dev_branch() {
    local project="$1" base_branch="$2"
    local dev_branch
    dev_branch=$(get_dev_branch)
    local name
    name=$(repo_name "$project")
    local workspace="$WORKSPACES_DIR/$name"

    cd "$workspace" || { log_error "Workspace $workspace not found"; return 1; }

    # Check whether the dev branch already exists on the remote
    local remote_ref
    remote_ref=$(git ls-remote --heads origin "$dev_branch" 2>/dev/null | head -1)

    if [ -n "$remote_ref" ]; then
        log_info "Dev branch $dev_branch exists on remote — fetching"
        git fetch origin "$dev_branch" 2>/dev/null || true
        # Ensure local tracking branch exists
        if ! git rev-parse --verify "$dev_branch" >/dev/null 2>&1; then
            git branch "$dev_branch" "origin/$dev_branch" 2>/dev/null || true
        fi
    else
        log_info "Creating dev branch $dev_branch from $base_branch"
        git checkout "$base_branch" 2>/dev/null || true
        git pull origin "$base_branch" 2>/dev/null || true
        git checkout -b "$dev_branch" 2>/dev/null || {
            log_error "Failed to create $dev_branch"
            return 1
        }
        git push -u origin "$dev_branch" 2>/dev/null || {
            log_error "Failed to push new $dev_branch to remote"
            return 1
        }
        log_ok "Created and pushed $dev_branch"
    fi

    return 0
}

sync_dev_with_main() {
    local project="$1" base_branch="$2"
    local dev_branch
    dev_branch=$(get_dev_branch)
    local name
    name=$(repo_name "$project")
    local workspace="$WORKSPACES_DIR/$name"

    cd "$workspace" || { log_error "Workspace $workspace not found"; return 1; }

    log_info "Syncing $dev_branch with $base_branch for $project"

    git fetch origin "$base_branch" 2>/dev/null || true
    git fetch origin "$dev_branch" 2>/dev/null || true

    git checkout "$dev_branch" 2>/dev/null || {
        log_error "Cannot checkout $dev_branch"
        return 1
    }
    git reset --hard "origin/$dev_branch" 2>/dev/null || true

    # Nothing to do if dev already contains all of main's commits
    if git merge-base --is-ancestor "origin/$base_branch" HEAD 2>/dev/null; then
        log_info "$dev_branch is already up to date with $base_branch"
        webhook_event "dev_sync" "\"project\":\"$project\",\"status\":\"up_to_date\"" >&2
        return 0
    fi

    # Prefer rebase for a clean history
    log_info "Rebasing $dev_branch onto $base_branch"
    if git rebase "origin/$base_branch" 2>/dev/null; then
        log_ok "Rebase succeeded"
    else
        # Rebase failed — abort and fall back to merge
        git rebase --abort 2>/dev/null || true
        log_warn "Rebase failed, falling back to merge"

        if git merge "origin/$base_branch" --no-edit 2>/dev/null; then
            log_ok "Merge succeeded"
        else
            # Merge also failed — abort and warn, but don't block the run
            git merge --abort 2>/dev/null || true
            log_warn "Merge conflict syncing $dev_branch with $base_branch — dev may be stale"
            webhook_event "dev_sync" "\"project\":\"$project\",\"status\":\"conflict\"" >&2
            return 0
        fi
    fi

    # Push the updated dev branch (force needed after rebase)
    git push origin "$dev_branch" --force-with-lease 2>/dev/null || {
        log_warn "Failed to push synced $dev_branch"
    }

    webhook_event "dev_sync" "\"project\":\"$project\",\"status\":\"synced\"" >&2
    log_ok "$dev_branch synced with $base_branch"
    return 0
}

# ============================================================================
# MERGE TO DEV
# ============================================================================

merge_to_dev() {
    local project="$1" branch="$2" base_branch="$3" issue_number="$4" reasoning="$5"
    local dev_branch
    dev_branch=$(get_dev_branch)
    local name
    name=$(repo_name "$project")
    local workspace="$WORKSPACES_DIR/$name"

    cd "$workspace" || { log_error "Workspace $workspace not found"; return 1; }

    # Ensure the integration branch exists before attempting merge. The
    # source is the project's underlying base (typically main) so a fresh
    # dev branch starts in sync.
    ensure_dev_branch "$project" "$base_branch" || {
        log_error "Cannot ensure dev branch for $project"
        return 1
    }

    # Push feature branch to remote so it can be referenced in PRs
    local push_ok=false
    for attempt in 1 2; do
        if git push -u origin "$branch" 2>&1 | while IFS= read -r line; do log_info "  $line"; done; then
            push_ok=true
            break
        fi
        [ "$attempt" -lt 2 ] && sleep 3
    done

    if [ "$push_ok" != true ]; then
        log_error "Failed to push $branch after 2 attempts"
        return 1
    fi
    log_ok "Pushed feature branch $branch"

    # Open a PR feature → dev so the merge is observable in GitHub history
    # and any branch protection on the integration branch is honored.
    local close_line=""
    if declare -F format_close_keywords >/dev/null 2>&1; then
        close_line=$(format_close_keywords "$issue_number" "" 2>/dev/null || echo "")
    fi
    local pr_title
    pr_title=$(git -C "$workspace" log -1 --format=%s "$branch" 2>/dev/null || echo "autonomous merge")
    local pr_url
    pr_url=$(gh pr create --repo "$project" --base "$dev_branch" --head "$branch" \
        --title "autonomous: $pr_title" \
        --body "Approved by autonomous manager. Merging into integration branch \`$dev_branch\`.

**Manager reasoning**: $reasoning${close_line:+

$close_line}

---
Autonomous run: $RUN_ID" 2>&1) || true

    if [ -z "$pr_url" ] || ! echo "$pr_url" | grep -q "http"; then
        log_error "Failed to create PR for $branch → $dev_branch: $pr_url"
        return 1
    fi
    log_ok "PR created: $pr_url"

    # Tag the PR so the stale-PR sweep knows it's an auto-merge candidate
    # if the immediate merge below fails.
    gh pr edit "$pr_url" --add-label "autonomous-agent/auto-merge" 2>/dev/null || true

    # Merge the PR into the integration branch (strategy from config —
    # branch protection may require squash or rebase instead of a merge
    # commit).
    local _merge_flag
    _merge_flag=$(get_merge_flag)
    local merge_commit_sha=""
    if gh pr merge "$pr_url" "$_merge_flag" --delete-branch 2>&1 | while IFS= read -r line; do log_info "  $line"; done; then
        log_ok "Merged PR $pr_url into $dev_branch"
        git fetch origin "$dev_branch" 2>/dev/null || true
        merge_commit_sha=$(git rev-parse "origin/$dev_branch" 2>/dev/null || echo "")
    else
        log_warn "PR merge failed for $pr_url — leaving open for manual resolution"

        # Label the issue so it's visible in the dashboard
        if [ -n "$issue_number" ] && [ "$issue_number" != "None" ] && [ "$issue_number" != "null" ]; then
            gh issue edit "$issue_number" --repo "$project" --add-label "autonomous-agent/conflict" 2>/dev/null || true
            gh issue comment "$issue_number" --repo "$project" --body "## Merge Conflict

PR $pr_url could not be auto-merged into \`$dev_branch\`. Please resolve conflicts manually.

---
Autonomous run: $RUN_ID" 2>/dev/null || true
        fi

        webhook_event "dev_merged" "\"project\":\"$project\",\"branch\":\"$branch\",\"issue_number\":\"$issue_number\",\"status\":\"conflict\",\"pr_url\":\"$pr_url\"" >&2
        return 0
    fi

    # Update issue labels to reflect the new state
    if [ -n "$issue_number" ] && [ "$issue_number" != "None" ] && [ "$issue_number" != "null" ]; then
        gh issue edit "$issue_number" --repo "$project" --remove-label "autonomous-agent/done" 2>/dev/null || true
        gh issue edit "$issue_number" --repo "$project" --remove-label "autonomous-agent/in-progress" 2>/dev/null || true
        gh issue edit "$issue_number" --repo "$project" --add-label "autonomous-agent/merged-to-dev" 2>/dev/null || true

        gh issue comment "$issue_number" --repo "$project" --body "## Merged to Integration Branch

PR $pr_url merged \`$branch\` into \`$dev_branch\`.
Will be promoted to \`$base_branch\` after validation.

**Manager reasoning**: $reasoning

---
Autonomous run: $RUN_ID" 2>/dev/null || log_warn "Failed to comment on issue #$issue_number"
    fi

    # Record the feature in the dashboard API
    # Resolve issue_title from the assignment file if available
    local _issue_title=""
    if [ -f "$workspace/.claude-assignment-0.json" ]; then
        _issue_title=$(python3 -c "import json; print(json.load(open('$workspace/.claude-assignment-0.json')).get('issue_title',''))" 2>/dev/null || echo "")
    fi
    local _escaped_title
    _escaped_title=$(printf '%s' "$_issue_title" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read())[1:-1])" 2>/dev/null || echo "")

    # Build issue_number as int or null for JSON
    local _issue_num_json="null"
    if [ -n "$issue_number" ] && [ "$issue_number" != "None" ] && [ "$issue_number" != "null" ]; then
        _issue_num_json="$issue_number"
    fi

    queue_api POST "/api/integration/features" "{
        \"project_repo\": \"$project\",
        \"branch\": \"$branch\",
        \"issue_number\": $_issue_num_json,
        \"issue_title\": \"$_escaped_title\",
        \"run_id\": \"run-$RUN_ID\",
        \"merge_commit\": \"$merge_commit_sha\"
    }" >/dev/null 2>&1 || true

    webhook_event "dev_merged" "\"project\":\"$project\",\"branch\":\"$branch\",\"issue_number\":\"$issue_number\",\"status\":\"success\"" >&2
    notify "dev_merge" "Merged $branch to $dev_branch for $project (#$issue_number)"

    return 0
}

# ============================================================================
# VALIDATION
# ============================================================================

validate_dev() {
    local project="$1" setup_script="${2:-}"
    local dev_branch
    dev_branch=$(get_dev_branch)
    local name
    name=$(repo_name "$project")
    local workspace="$WORKSPACES_DIR/$name"

    cd "$workspace" || { log_error "Workspace $workspace not found"; return 1; }

    log_info "Validating $dev_branch for $project"
    webhook_event "dev_validation_start" "\"project\":\"$project\",\"dev_branch\":\"$dev_branch\"" >&2

    git checkout "$dev_branch" 2>/dev/null || {
        log_error "Cannot checkout $dev_branch for validation"
        webhook_event "dev_validation_fail" "\"project\":\"$project\",\"reason\":\"checkout_failed\"" >&2
        return 1
    }
    git pull origin "$dev_branch" 2>/dev/null || true

    # Run setup if provided (e.g. dependency install). Announce the script
    # content only after it passes validation, so a rejected payload
    # doesn't reach logs verbatim. See lib/setup_script.sh / issue #179.
    if [ -n "$setup_script" ]; then
        if validate_setup_script "$setup_script"; then
            log_info "Running setup: $setup_script"
            if ! run_setup_script "$setup_script" "validate_dev($project)" 2>&1 | while IFS= read -r line; do log_info "  [setup] $line"; done; then
                log_warn "Setup script failed, continuing with validation anyway"
            fi
        else
            log_warn "setup_script rejected by validator, skipping for $project"
        fi
    fi

    # Detect and run the project's test suite
    local test_exit=0
    if [ -f "package.json" ]; then
        log_info "Detected Node.js project — running npm test"
        npm test 2>&1 | while IFS= read -r line; do log_info "  [test] $line"; done
        test_exit=${PIPESTATUS[0]}
    elif [ -f "requirements.txt" ] || [ -f "pyproject.toml" ] || [ -f "setup.py" ]; then
        log_info "Detected Python project — running pytest"
        python3 -m pytest 2>&1 | while IFS= read -r line; do log_info "  [test] $line"; done
        test_exit=${PIPESTATUS[0]}
    elif [ -f "Cargo.toml" ]; then
        log_info "Detected Rust project — running cargo test"
        cargo test 2>&1 | while IFS= read -r line; do log_info "  [test] $line"; done
        test_exit=${PIPESTATUS[0]}
    elif [ -f "go.mod" ]; then
        log_info "Detected Go project — running go test"
        go test ./... 2>&1 | while IFS= read -r line; do log_info "  [test] $line"; done
        test_exit=${PIPESTATUS[0]}
    elif [ -f "Makefile" ] && grep -q '^test:' Makefile 2>/dev/null; then
        log_info "Detected Makefile with test target"
        make test 2>&1 | while IFS= read -r line; do log_info "  [test] $line"; done
        test_exit=${PIPESTATUS[0]}
    else
        log_warn "No recognized test runner found — skipping tests"
        test_exit=0
    fi

    if [ "$test_exit" -eq 0 ]; then
        log_ok "Validation passed for $dev_branch"
        webhook_event "dev_validation_pass" "\"project\":\"$project\",\"dev_branch\":\"$dev_branch\"" >&2
    else
        log_error "Validation failed for $dev_branch (exit code: $test_exit)"
        webhook_event "dev_validation_fail" "\"project\":\"$project\",\"dev_branch\":\"$dev_branch\",\"exit_code\":$test_exit" >&2
    fi

    # Auto-bisect if enabled
    if [ "$test_exit" -ne 0 ]; then
        local auto_bisect
        auto_bisect=$(json_get "$CONFIG_FILE" "integration.auto_bisect" 2>/dev/null || echo "true")
        if [ "$auto_bisect" = "true" ]; then
            log_info "Auto-bisecting validation failure..."
            bisect_validation_failure "$project" "$setup_script" || log_warn "Bisect could not identify culprit"
        fi
    fi

    return "$test_exit"
}

# ============================================================================
# SELF-HEALING: BISECT VALIDATION FAILURES
# ============================================================================

bisect_validation_failure() {
    local project="$1" setup_script="${2:-}"
    local dev_branch
    dev_branch=$(get_dev_branch)
    local name
    name=$(repo_name "$project")
    local workspace="$WORKSPACES_DIR/$name"

    cd "$workspace" || { log_error "Workspace $workspace not found"; return 1; }

    log_info "Bisecting validation failure on $dev_branch for $project"

    git checkout "$dev_branch" 2>/dev/null || return 1
    git pull origin "$dev_branch" 2>/dev/null || true

    # Get the most recent merge commit (the likely culprit)
    local last_merge
    last_merge=$(git log --merges --format="%H" -1 2>/dev/null || echo "")

    if [ -z "$last_merge" ]; then
        log_warn "No merge commits found on $dev_branch — cannot bisect"
        return 1
    fi

    local last_merge_msg
    last_merge_msg=$(git log --format="%s" -1 "$last_merge" 2>/dev/null || echo "unknown")

    log_info "Reverting last merge: $last_merge ($last_merge_msg)"

    # Revert the merge commit
    if git revert -m 1 "$last_merge" --no-edit 2>/dev/null; then
        log_ok "Reverted merge: $last_merge"

        # Run tests again to confirm dev is green
        if validate_dev "$project" "$setup_script"; then
            log_ok "Validation passed after reverting — culprit identified: $last_merge"

            # Push the revert
            git push origin "$dev_branch" 2>/dev/null || {
                log_error "Failed to push revert"
                return 1
            }

            # Extract issue number from merge commit message
            local issue_number
            issue_number=$(echo "$last_merge_msg" | grep -oP '#\K[0-9]+' | head -1)

            if [ -n "$issue_number" ]; then
                # Update feature state via API
                local feature_id
                feature_id=$(queue_api GET "/api/integration/features?project_repo=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$project'))")" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for item in data.get('items', []):
    if item.get('issue_number') == $issue_number:
        print(item['id'])
        break
" 2>/dev/null || echo "")

                if [ -n "$feature_id" ]; then
                    queue_api PUT "/api/integration/features/$feature_id" '{"state":"validation_failed","validation_status":"fail"}' >/dev/null 2>&1
                fi

                # Label the issue
                gh issue edit "$issue_number" --repo "$project" --add-label "autonomous-agent/validation-failed" 2>/dev/null || true
                gh issue comment "$issue_number" --repo "$project" --body "## Validation Failed

Feature branch reverted from \`$dev_branch\` — tests failed after merge.

The merge commit \`${last_merge:0:12}\` has been reverted. This issue will be re-attempted in the next sprint cycle.

---
Autonomous run: $RUN_ID" 2>/dev/null || true
            fi

            webhook_event "dev_bisect_complete" "\"project\":\"$project\",\"culprit\":\"$last_merge\",\"issue_number\":\"${issue_number:-unknown}\",\"status\":\"reverted\"" >&2
            notify "validation_failed" "Feature reverted from $dev_branch: $last_merge_msg (#${issue_number:-?})"
            return 0
        else
            # Still failing after revert — problem is deeper
            log_warn "Validation still failing after revert — problem may be in multiple features"
            git revert HEAD --no-edit 2>/dev/null || true  # Undo our revert
            git push origin "$dev_branch" 2>/dev/null || true

            webhook_event "dev_bisect_complete" "\"project\":\"$project\",\"status\":\"inconclusive\"" >&2
            return 1
        fi
    else
        # Revert failed (conflicts)
        git revert --abort 2>/dev/null || true
        log_warn "Could not revert merge $last_merge — revert has conflicts"
        webhook_event "dev_bisect_complete" "\"project\":\"$project\",\"status\":\"revert_conflict\"" >&2
        return 1
    fi
}

# ============================================================================
# PROMOTION TO MAIN
# ============================================================================

promote_to_main() {
    local project="$1" base_branch="$2" strategy="${3:-batch}"
    local dev_branch
    dev_branch=$(get_dev_branch)
    local name
    name=$(repo_name "$project")
    local workspace="$WORKSPACES_DIR/$name"

    cd "$workspace" || { log_error "Workspace $workspace not found"; return 1; }

    log_info "Promoting $dev_branch to $base_branch for $project (strategy: $strategy)"
    webhook_event "promotion_start" "\"project\":\"$project\",\"strategy\":\"$strategy\",\"dev_branch\":\"$dev_branch\",\"base_branch\":\"$base_branch\"" >&2

    git fetch origin "$base_branch" 2>/dev/null || true
    git fetch origin "$dev_branch" 2>/dev/null || true

    # Nothing to promote if dev is identical to or behind main
    if git merge-base --is-ancestor "origin/$dev_branch" "origin/$base_branch" 2>/dev/null; then
        log_info "Nothing to promote — $dev_branch has no new commits over $base_branch"
        webhook_event "promotion_complete" "\"project\":\"$project\",\"status\":\"nothing_to_promote\"" >&2
        return 0
    fi

    case "$strategy" in
        batch)
            _promote_batch "$project" "$base_branch" "$dev_branch"
            ;;
        individual)
            _promote_individual "$project" "$base_branch" "$dev_branch"
            ;;
        *)
            log_error "Unknown promotion strategy: $strategy"
            return 1
            ;;
    esac
}

_promote_batch() {
    local project="$1" base_branch="$2" dev_branch="$3"

    # Collect feature summaries from the commit log between main and dev
    local feature_log
    feature_log=$(git log "origin/$base_branch..origin/$dev_branch" --oneline 2>/dev/null || echo "")
    local feature_count
    feature_count=$(echo "$feature_log" | grep -c '.' 2>/dev/null || echo "0")

    log_info "Creating batch promotion PR with $feature_count commit(s)"

    local pr_url
    pr_url=$(gh pr create --repo "$project" --base "$base_branch" --head "$dev_branch" \
        --title "autonomous: promote ${feature_count} feature(s) to ${base_branch}" \
        --body "## Integration Branch Promotion

**From**: \`$dev_branch\`
**To**: \`$base_branch\`
**Features**: ${feature_count} commit(s)

### Commits
\`\`\`
${feature_log}
\`\`\`

---
This PR was created by the autonomous agent after validation passed.
Autonomous run: $RUN_ID" 2>&1) || true

    if [ -n "$pr_url" ] && echo "$pr_url" | grep -q "http" 2>/dev/null; then
        log_ok "Promotion PR created: $pr_url"
        webhook_event "promotion_complete" "\"project\":\"$project\",\"status\":\"pr_created\",\"pr_url\":\"$pr_url\",\"feature_count\":$feature_count" >&2
        notify "promotion" "Promotion PR created for $project: $pr_url ($feature_count features)"
    else
        log_error "Failed to create promotion PR for $project"
        webhook_event "promotion_failed" "\"project\":\"$project\",\"status\":\"pr_creation_failed\"" >&2
        return 1
    fi

    return 0
}

_promote_individual() {
    local project="$1" base_branch="$2" dev_branch="$3"

    # Get individual merge commits (features) from dev that aren't in main
    local commits
    commits=$(git log "origin/$base_branch..origin/$dev_branch" --format="%H %s" 2>/dev/null || echo "")

    if [ -z "$commits" ]; then
        log_info "No commits to promote individually"
        webhook_event "promotion_complete" "\"project\":\"$project\",\"status\":\"nothing_to_promote\"" >&2
        return 0
    fi

    local total=0 success=0 failed=0
    while IFS= read -r commit_line; do
        [ -z "$commit_line" ] && continue
        total=$((total + 1))

        local commit_hash commit_msg
        commit_hash=$(echo "$commit_line" | cut -d' ' -f1)
        commit_msg=$(echo "$commit_line" | cut -d' ' -f2-)

        # Create a temp branch from main with just this commit cherry-picked
        local temp_branch="promote/${commit_hash:0:8}"

        git checkout "origin/$base_branch" 2>/dev/null || continue
        git checkout -b "$temp_branch" 2>/dev/null || continue

        if git cherry-pick "$commit_hash" --no-edit 2>/dev/null; then
            git push origin "$temp_branch" 2>/dev/null || { failed=$((failed + 1)); continue; }

            gh pr create --repo "$project" --base "$base_branch" --head "$temp_branch" \
                --title "autonomous: $commit_msg" \
                --body "## Individual Feature Promotion

Cherry-picked from \`$dev_branch\`: \`${commit_hash:0:12}\`

---
Autonomous run: $RUN_ID" 2>/dev/null && success=$((success + 1)) || failed=$((failed + 1))
        else
            git cherry-pick --abort 2>/dev/null || true
            log_warn "Cherry-pick failed for $commit_hash — skipping"
            failed=$((failed + 1))
        fi

        # Clean up temp branch locally
        git checkout "$dev_branch" 2>/dev/null || true
        git branch -D "$temp_branch" 2>/dev/null || true
    done <<< "$commits"

    log_ok "Individual promotion: $success/$total succeeded, $failed failed"
    webhook_event "promotion_complete" "\"project\":\"$project\",\"status\":\"individual_complete\",\"total\":$total,\"success\":$success,\"failed\":$failed" >&2

    return 0
}

# ============================================================================
# GITHUB LABELS
# ============================================================================

create_integration_labels() {
    local project="$1"

    log_info "Ensuring integration labels exist for $project"

    # Each label: name, color, description
    gh label create "autonomous-agent/merged-to-dev" --repo "$project" \
        --color 0E8A16 --description "Merged to integration branch (autonomous/dev)" --force 2>/dev/null || true
    gh label create "autonomous-agent/validated" --repo "$project" \
        --color 1D76DB --description "Passed validation on integration branch" --force 2>/dev/null || true
    gh label create "autonomous-agent/promoted" --repo "$project" \
        --color 5319E7 --description "Promoted to main via PR" --force 2>/dev/null || true
    gh label create "autonomous-agent/conflict" --repo "$project" \
        --color D93F0B --description "Merge conflict — needs manual resolution" --force 2>/dev/null || true
    gh label create "autonomous-agent/validation-failed" --repo "$project" \
        --color B60205 --description "Failed validation on integration branch" --force 2>/dev/null || true
    gh label create "autonomous-agent/auto-merge" --repo "$project" \
        --color FBCA04 --description "Auto-merge candidate — will be merged into the integration branch by the next sweep" --force 2>/dev/null || true

    log_ok "Integration labels ensured for $project"
}

# ============================================================================
# STALE-PR SWEEP
# ============================================================================
#
# Find open auto-merge PRs targeting the integration branch that didn't get
# merged at creation time (e.g. branch protection delay, race, gh transient
# failure, or — more importantly — a manager that ran out of turns and
# produced no verdicts at all, leaving previously pushed branches with
# unmerged PRs). Retry the merge for any that are now MERGEABLE.
#
# Only PRs carrying the `autonomous-agent/auto-merge` label are touched —
# PR-verdict PRs (opened for human review) are intentionally left alone.

sweep_stale_integration_prs() {
    local project="$1"
    local dev_branch
    dev_branch=$(get_dev_branch)

    if [ -z "$project" ]; then
        return 0
    fi

    local pr_json
    pr_json=$(gh pr list --repo "$project" --state open --base "$dev_branch" \
        --label "autonomous-agent/auto-merge" \
        --json number,title,mergeable --limit 50 2>/dev/null || echo "[]")

    # Parse all PRs in a single python invocation (TSV) — number<TAB>mergeable<TAB>title.
    # Tabs inside titles are normalised so the read split stays clean.
    local rows
    rows=$(echo "$pr_json" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for pr in data:
    num = pr.get('number','')
    merg = pr.get('mergeable','')
    title = (pr.get('title','') or '').replace('\t', ' ')
    print(f'{num}\t{merg}\t{title}')
" 2>/dev/null || echo "")

    if [ -z "$rows" ]; then
        return 0
    fi

    local pr_count
    pr_count=$(printf '%s\n' "$rows" | grep -c .)
    log_info "Sweep: $pr_count auto-merge PR(s) open against $dev_branch in $project"

    local _merge_flag
    _merge_flag=$(get_merge_flag)

    local swept=0 failed=0 unknown=0
    while IFS=$'\t' read -r pr_num pr_mergeable pr_title; do
        [ -z "$pr_num" ] && continue

        case "$pr_mergeable" in
            MERGEABLE)
                log_info "Sweep: merging #$pr_num ($_merge_flag) — $pr_title"
                if gh pr merge "$pr_num" --repo "$project" "$_merge_flag" --delete-branch 2>&1 | while IFS= read -r m; do log_info "  $m"; done; then
                    log_ok "Sweep: merged #$pr_num into $dev_branch"
                    swept=$((swept + 1))
                else
                    # Swap labels — drop auto-merge so this PR doesn't get
                    # retried (and re-notified) every sweep until a human
                    # resolves it.
                    log_warn "Sweep: merge failed for #$pr_num — handing off (auto-merge → conflict)"
                    gh pr edit "$pr_num" --repo "$project" \
                        --remove-label "autonomous-agent/auto-merge" \
                        --add-label "autonomous-agent/conflict" 2>/dev/null || true
                    failed=$((failed + 1))
                fi
                ;;
            CONFLICTING)
                log_info "Sweep: #$pr_num has conflicts — handing off (auto-merge → conflict)"
                gh pr edit "$pr_num" --repo "$project" \
                    --remove-label "autonomous-agent/auto-merge" \
                    --add-label "autonomous-agent/conflict" 2>/dev/null || true
                failed=$((failed + 1))
                ;;
            *)
                # UNKNOWN — GitHub hasn't finished computing mergeability yet
                log_info "Sweep: #$pr_num mergeability still computing ($pr_mergeable) — leaving for next sweep"
                unknown=$((unknown + 1))
                ;;
        esac
    done <<< "$rows"

    webhook_event "stale_prs_swept" \
        "\"project\":\"$project\",\"dev_branch\":\"$dev_branch\",\"swept\":$swept,\"failed\":$failed,\"unknown\":$unknown,\"total\":$pr_count" >&2

    if [ "$swept" -gt 0 ]; then
        notify "sweep" "Swept $swept stale PR(s) into $dev_branch for $project"
    fi

    return 0
}
