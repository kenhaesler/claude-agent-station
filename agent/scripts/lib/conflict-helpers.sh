#!/usr/bin/env bash
# agent/scripts/lib/conflict-helpers.sh
#
# Helpers shared between resolve-conflicts.sh and its tests. Sourced, not
# executed.

# Names of files we treat as machine-regenerable lockfiles.
# Keep in sync with agent/conflict_resolver/markers.py:LOCKFILE_NAMES.
CONFLICT_LOCKFILE_NAMES="package-lock.json yarn.lock pnpm-lock.yaml Cargo.lock"

# is_lockfile_only_conflict <newline-separated-paths>
# Exit 0 (true) iff every line is a lockfile name AND there's at least one line.
# Used by Phase 2 to decide whether lockfile regen alone can resolve.
is_lockfile_only_conflict() {
    local files="${1:-}"
    [ -z "$files" ] && return 1
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        local base
        base=$(basename "$f")
        local matched=false
        for lock in $CONFLICT_LOCKFILE_NAMES; do
            if [ "$base" = "$lock" ]; then
                matched=true
                break
            fi
        done
        [ "$matched" = false ] && return 1
    done <<< "$files"
    return 0
}

# regen_lockfile <workspace> <lockfile-basename>
# Runs the appropriate package manager to regenerate the lockfile from
# the merged source tree. Returns the package manager's exit code.
regen_lockfile() {
    local workspace="$1" lockfile="$2"
    case "$lockfile" in
        package-lock.json) (cd "$workspace" && npm install --silent) ;;
        yarn.lock) (cd "$workspace" && yarn install --silent) ;;
        pnpm-lock.yaml) (cd "$workspace" && pnpm install --silent) ;;
        Cargo.lock) (cd "$workspace" && cargo build --offline 2>/dev/null || cargo build) ;;
        *) return 1 ;;
    esac
}

# take_flock <branch> <ttl_seconds>
# Acquires a non-blocking flock. Stale locks (older than TTL) are deleted
# and re-acquired. Echoes the lockfile path on success; non-zero exit on
# failure. Caller must call release_flock with the same path.
take_flock() {
    local branch="$1" ttl="${2:-1800}"
    local lock_dir="${STATION_LOCK_DIR:-/var/lib/claude-agent-station/locks}"
    mkdir -p "$lock_dir"
    local lockpath="$lock_dir/conflict-$(echo "$branch" | tr '/' '_').lock"
    # Stale-lock GC: if file mtime older than TTL, remove it.
    if [ -f "$lockpath" ]; then
        local age
        age=$(( $(date +%s) - $(stat -c %Y "$lockpath" 2>/dev/null || stat -f %m "$lockpath") ))
        if [ "$age" -gt "$ttl" ]; then
            rm -f "$lockpath"
        fi
    fi
    # Try to acquire.
    exec 200>"$lockpath"
    flock -n 200 || return 1
    echo "$lockpath"
}

# release_flock — closes fd 200 (the take_flock fd).
release_flock() {
    exec 200>&-
}

# ensure_conflict_label <repo> <label>
# Idempotently creates the GitHub label on the repo. Safe to call every run.
ensure_conflict_label() {
    local repo="$1" label="$2"
    gh label create "$label" --repo "$repo" \
        --color D93F0B \
        --description "Auto-applied by Claude Agent Station conflict resolver" \
        2>/dev/null || true
}

# post_resolution_outcome <exit_code> <repo> <pr_num> <branch>
# Comment + label on the PR based on the resolver's exit code.
post_resolution_outcome() {
    local rc="$1" repo="$2" pr="$3" branch="$4"
    [ -z "$pr" ] && return 0  # no PR yet (pre-PR rebase) — nothing to comment on
    case "$rc" in
        0)
            gh pr comment "$pr" --repo "$repo" --body "🤖 Conflicts auto-resolved on \`$branch\`. The PR was rebased; any in-flight review comments may now show as outdated." 2>/dev/null || true
            ;;
        99)
            ensure_conflict_label "$repo" "conflict-budget-exhausted"
            gh pr edit "$pr" --repo "$repo" --add-label "conflict-budget-exhausted" 2>/dev/null || true
            gh pr comment "$pr" --repo "$repo" --body "🤖 Conflict resolution budget exhausted for \`$branch\` over the rolling 24h window. Resumes automatically tomorrow; remove this label to retry sooner." 2>/dev/null || true
            ;;
        10)
            ensure_conflict_label "$repo" "conflict-tests-failed"
            gh pr edit "$pr" --repo "$repo" --add-label "conflict-tests-failed" 2>/dev/null || true
            gh pr comment "$pr" --repo "$repo" --body "🤖 Conflict resolver produced a tree but tests failed after all feedback rounds. The branch state is the resolver's best attempt; review and fix manually." 2>/dev/null || true
            ;;
        11)
            ensure_conflict_label "$repo" "conflict-manager-rejected"
            gh pr edit "$pr" --repo "$repo" --add-label "conflict-manager-rejected" 2>/dev/null || true
            gh pr comment "$pr" --repo "$repo" --body "🤖 Conflict resolver produced a tree but the manager review rejected it. Review the resolver's last commit and the manager's reasoning in the audit log." 2>/dev/null || true
            ;;
        *)
            gh pr comment "$pr" --repo "$repo" --body "🤖 Conflict resolution errored (rc=$rc). Branch left as-is for manual intervention." 2>/dev/null || true
            ;;
    esac
}
