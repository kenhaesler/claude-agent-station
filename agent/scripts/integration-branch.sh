# integration-branch.sh
# Library of functions for managing the autonomous/dev integration branch.
# Sourced by run-manager.sh — must NOT have set -euo pipefail or shebang.
#
# Depends on run-manager.sh globals:
#   log_info, log_warn, log_error, log_ok, webhook_event, queue_api,
#   json_get, repo_name, notify, $WORKSPACES_DIR, $CONFIG_FILE, $RUN_ID

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

    # Ensure the integration branch exists before attempting merge
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

    # Checkout dev and pull latest
    git checkout "$dev_branch" 2>/dev/null || {
        log_error "Cannot checkout $dev_branch"
        return 1
    }
    git pull origin "$dev_branch" 2>/dev/null || true

    # Attempt the merge
    if git merge "$branch" --no-edit 2>/dev/null; then
        log_ok "Merged $branch into $dev_branch"

        git push origin "$dev_branch" 2>/dev/null || {
            log_error "Failed to push $dev_branch after merge"
            return 1
        }
        log_ok "Pushed $dev_branch"
    else
        # Merge conflict — abort, create a PR for manual resolution instead
        git merge --abort 2>/dev/null || true
        log_warn "Merge conflict: $branch into $dev_branch — creating PR for manual resolution"

        gh pr create --repo "$project" --base "$dev_branch" --head "$branch" \
            --title "autonomous: merge #${issue_number} to dev (conflict)" \
            --body "## Merge Conflict

Feature branch \`$branch\` could not be cleanly merged into \`$dev_branch\`.

**Issue**: #${issue_number}
**Reasoning**: ${reasoning}

Please resolve conflicts manually.

---
Autonomous run: $RUN_ID" 2>/dev/null || log_warn "PR creation failed for conflict resolution"

        # Label the issue so it's visible in the dashboard
        if [ -n "$issue_number" ] && [ "$issue_number" != "None" ] && [ "$issue_number" != "null" ]; then
            gh issue edit "$issue_number" --repo "$project" --add-label "autonomous-agent/conflict" 2>/dev/null || true
        fi

        webhook_event "dev_merged" "\"project\":\"$project\",\"branch\":\"$branch\",\"issue_number\":\"$issue_number\",\"status\":\"conflict\"" >&2
        return 0
    fi

    # Update issue labels to reflect the new state
    if [ -n "$issue_number" ] && [ "$issue_number" != "None" ] && [ "$issue_number" != "null" ]; then
        gh issue edit "$issue_number" --repo "$project" --remove-label "autonomous-agent/done" 2>/dev/null || true
        gh issue edit "$issue_number" --repo "$project" --remove-label "autonomous-agent/in-progress" 2>/dev/null || true
        gh issue edit "$issue_number" --repo "$project" --add-label "autonomous-agent/merged-to-dev" 2>/dev/null || true

        gh issue comment "$issue_number" --repo "$project" --body "## Merged to Integration Branch

Branch \`$branch\` merged into \`$dev_branch\`.
Will be promoted to \`$base_branch\` after validation.

**Manager reasoning**: $reasoning

---
Autonomous run: $RUN_ID" 2>/dev/null || log_warn "Failed to comment on issue #$issue_number"
    fi

    # Record the feature in the dashboard API
    local escaped_reasoning
    escaped_reasoning=$(printf '%s' "$reasoning" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" 2>/dev/null || echo "\"\"")
    queue_api POST "/api/integration/features" "{
        \"project\": \"$project\",
        \"branch\": \"$branch\",
        \"issue_number\": \"$issue_number\",
        \"dev_branch\": \"$dev_branch\",
        \"run_id\": \"run-$RUN_ID\",
        \"reasoning\": $escaped_reasoning
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

    # Run setup if provided (e.g. dependency install)
    if [ -n "$setup_script" ]; then
        log_info "Running setup: $setup_script"
        if ! eval "$setup_script" 2>&1 | while IFS= read -r line; do log_info "  [setup] $line"; done; then
            log_warn "Setup script failed, continuing with validation anyway"
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

    return "$test_exit"
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

    log_ok "Integration labels ensured for $project"
}
