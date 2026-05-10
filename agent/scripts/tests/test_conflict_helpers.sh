#!/usr/bin/env bash
# agent/scripts/tests/test_conflict_helpers.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../lib/conflict-helpers.sh
source "$SCRIPT_DIR/lib/conflict-helpers.sh"

fail=0
assert_eq() {
    local expected="$1" actual="$2" label="$3"
    if [ "$expected" = "$actual" ]; then
        echo "  PASS  $label"
    else
        echo "  FAIL  $label: expected='$expected' actual='$actual'"
        fail=1
    fi
}

# is_lockfile_only_conflict empty list → false (nothing to be lockfile-only)
out=$(is_lockfile_only_conflict "" && echo true || echo false)
assert_eq "false" "$out" "empty conflict list"

out=$(is_lockfile_only_conflict "package-lock.json" && echo true || echo false)
assert_eq "true" "$out" "single package-lock.json"

out=$(is_lockfile_only_conflict $'package-lock.json\nyarn.lock' && echo true || echo false)
assert_eq "true" "$out" "multiple lockfiles"

out=$(is_lockfile_only_conflict $'package-lock.json\nsrc/main.ts' && echo true || echo false)
assert_eq "false" "$out" "lockfile + non-lockfile"

out=$(is_lockfile_only_conflict $'src/main.ts' && echo true || echo false)
assert_eq "false" "$out" "non-lockfile only"

if [ "$fail" -eq 0 ]; then
    echo "All tests passed."
    exit 0
else
    echo "Tests failed."
    exit 1
fi
