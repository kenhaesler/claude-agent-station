#!/usr/bin/env bash
# agent/scripts/resolve-conflicts.sh
#
# Phase pipeline for conflict resolution. See spec
# docs/superpowers/specs/2026-05-10-conflict-resolution-design.md.
#
# Usage:
#   resolve-conflicts.sh \
#       --workspace <path> --branch <head> --base <base> \
#       --repo <owner/name> [--pr <num>] [--triggered-by pre_pr|at_merge] \
#       [--run-id <id>]
#
# Exit codes mirror agent.conflict_resolver:
#   0  resolved + pushed
#   10 tests failed after rounds
#   11 manager rejected after rounds
#   99 budget exhausted
#   1  unrecoverable error
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/conflict-helpers.sh
source "$SCRIPT_DIR/lib/conflict-helpers.sh"

# --- args ---
WORKSPACE="" BRANCH="" BASE="" REPO="" PR_NUM="" TRIGGERED_BY="pre_pr" RUN_ID=""
while [ $# -gt 0 ]; do
    case "$1" in
        --workspace) WORKSPACE="$2"; shift 2 ;;
        --branch) BRANCH="$2"; shift 2 ;;
        --base) BASE="$2"; shift 2 ;;
        --repo) REPO="$2"; shift 2 ;;
        --pr) PR_NUM="$2"; shift 2 ;;
        --triggered-by) TRIGGERED_BY="$2"; shift 2 ;;
        --run-id) RUN_ID="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 1 ;;
    esac
done
for v in WORKSPACE BRANCH BASE REPO; do
    [ -z "${!v}" ] && { echo "missing --${v,,}" >&2; exit 1; }
done

LOG_PREFIX="[resolve-conflicts $BRANCH]"
log() { echo "$LOG_PREFIX $*" >&2; }

# --- flock ---
LOCK_TTL="${STATION_CONFLICT_LOCK_TTL:-1800}"
if ! lockpath=$(take_flock "$BRANCH" "$LOCK_TTL"); then
    log "another resolution attempt is running for this branch; exiting"
    exit 0
fi
trap 'release_flock' EXIT

# --- Phase 1: mechanical rebase ---
log "Phase 1: mechanical rebase against $BASE"
cd "$WORKSPACE"
if ! git fetch origin "$BASE" >&2; then
    log "git fetch failed; aborting"
    exit 1
fi
if git rebase "origin/$BASE" >&2; then
    log "Phase 1 clean — pushing"
    git push --force-with-lease origin "$BRANCH" >&2 || { log "push failed"; exit 1; }
    log "resolved at Phase 1"
    exit 0
fi
log "Phase 1 conflicts; continuing"

# Collect the conflict file list.
conflicted=$(git diff --name-only --diff-filter=U || true)

# --- Phase 2: lockfile regen ---
if is_lockfile_only_conflict "$conflicted"; then
    log "Phase 2: lockfile-only conflict; regenerating"
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        git checkout --theirs "$f" || { log "checkout --theirs $f failed"; break; }
        if regen_lockfile "$WORKSPACE" "$(basename "$f")"; then
            git add "$f"
        else
            log "regen failed for $f; falling through to Phase 3"
            git rebase --abort 2>/dev/null || true
            break
        fi
    done <<< "$conflicted"
    if git rebase --continue >&2 2>/dev/null; then
        log "Phase 2 clean — pushing"
        git push --force-with-lease origin "$BRANCH" >&2 || { log "push failed"; exit 1; }
        log "resolved at Phase 2"
        exit 0
    fi
fi

# --- Phase 3: LLM resolver ---
log "Phase 3: invoking LLM resolver"
phase3_args=(
    --workspace "$WORKSPACE"
    --branch "$BRANCH"
    --base-branch "$BASE"
    --repo "$REPO"
    --triggered-by "$TRIGGERED_BY"
)
[ -n "$PR_NUM" ] && phase3_args+=(--pr-number "$PR_NUM")
[ -n "$RUN_ID" ] && phase3_args+=(--run-id "$RUN_ID")

# python -m agent.conflict_resolver returns the harness exit codes (0/10/99/1).
set +e
python3 -m agent.conflict_resolver "${phase3_args[@]}"
phase3_rc=$?
set -e

case "$phase3_rc" in
    0)
        log "Phase 3 resolved — pushing"
        git push --force-with-lease origin "$BRANCH" >&2 || { log "push failed"; exit 1; }
        exit 0
        ;;
    99)
        log "budget exhausted"
        exit 99
        ;;
    10|11|1)
        log "Phase 3 returned $phase3_rc"
        exit "$phase3_rc"
        ;;
    *)
        log "unexpected exit code $phase3_rc from Phase 3"
        exit 1
        ;;
esac
