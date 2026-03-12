#!/usr/bin/env bash
# =============================================================================
# Tests for install.sh
# =============================================================================
# Validates the install script's syntax, structure, and dry-run behavior
# without actually performing any installation.
#
# Usage: bash tests/test_install.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INSTALL_SCRIPT="${REPO_ROOT}/install.sh"

PASS=0
FAIL=0
TOTAL=0

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

assert_ok() {
    local test_name="$1"
    TOTAL=$((TOTAL + 1))
    if "$@" 2>/dev/null; then
        PASS=$((PASS + 1))
        echo -e "${GREEN}PASS${NC}: ${test_name}"
    else
        FAIL=$((FAIL + 1))
        echo -e "${RED}FAIL${NC}: ${test_name}"
    fi
}

assert_contains() {
    local test_name="$1"
    local haystack="$2"
    local needle="$3"
    TOTAL=$((TOTAL + 1))
    if echo "$haystack" | grep -q "$needle"; then
        PASS=$((PASS + 1))
        echo -e "${GREEN}PASS${NC}: ${test_name}"
    else
        FAIL=$((FAIL + 1))
        echo -e "${RED}FAIL${NC}: ${test_name}"
    fi
}

assert_true() {
    local test_name="$1"
    shift
    TOTAL=$((TOTAL + 1))
    if eval "$@"; then
        PASS=$((PASS + 1))
        echo -e "${GREEN}PASS${NC}: ${test_name}"
    else
        FAIL=$((FAIL + 1))
        echo -e "${RED}FAIL${NC}: ${test_name}"
    fi
}

echo "=== Claude Agent Station Install Script Tests ==="
echo ""

# Test 1: Script exists
assert_true "install.sh exists at repo root" "[[ -f '${INSTALL_SCRIPT}' ]]"

# Test 2: Script is valid bash
assert_true "install.sh has valid bash syntax" "bash -n '${INSTALL_SCRIPT}'"

# Test 3: Script has shebang
assert_true "install.sh has bash shebang" "head -1 '${INSTALL_SCRIPT}' | grep -q '#!/usr/bin/env bash'"

# Test 4: Script uses set -euo pipefail
assert_true "install.sh uses strict mode (set -euo pipefail)" "grep -q 'set -euo pipefail' '${INSTALL_SCRIPT}'"

# Test 5: Script has --dry-run flag
assert_true "install.sh supports --dry-run flag" "grep -q '\-\-dry-run' '${INSTALL_SCRIPT}'"

# Test 6: Script has --help flag
assert_true "install.sh supports --help flag" "grep -q '\-\-help' '${INSTALL_SCRIPT}'"

# Test 7: Script has --upgrade flag
assert_true "install.sh supports --upgrade flag" "grep -q '\-\-upgrade' '${INSTALL_SCRIPT}'"

# Test 8: Script has --uninstall flag
assert_true "install.sh supports --uninstall flag" "grep -q '\-\-uninstall' '${INSTALL_SCRIPT}'"

# Test 9: Script checks for root
assert_true "install.sh checks for root access" "grep -q 'EUID' '${INSTALL_SCRIPT}'"

# Test 10: Script checks OS
assert_true "install.sh checks OS compatibility" "grep -q 'os-release' '${INSTALL_SCRIPT}'"

# Test 11: Script installs system deps
SCRIPT_CONTENT=$(cat "$INSTALL_SCRIPT")
assert_contains "install.sh installs python3" "$SCRIPT_CONTENT" "python3"
assert_contains "install.sh installs git" "$SCRIPT_CONTENT" "git"
assert_contains "install.sh installs jq" "$SCRIPT_CONTENT" "jq"
assert_contains "install.sh installs socat" "$SCRIPT_CONTENT" "socat"
assert_contains "install.sh installs bubblewrap" "$SCRIPT_CONTENT" "bubblewrap"

# Test 12: Script creates service user
assert_contains "install.sh creates claude-agent user" "$SCRIPT_CONTENT" "claude-agent"
assert_contains "install.sh uses useradd" "$SCRIPT_CONTENT" "useradd"

# Test 13: Script sets up directories
assert_contains "install.sh creates /opt/claude-agent-station" "$SCRIPT_CONTENT" "/opt/claude-agent-station"
assert_contains "install.sh creates /var/lib/claude-agent-station" "$SCRIPT_CONTENT" "/var/lib/claude-agent-station"
assert_contains "install.sh creates /var/log/claude-agent" "$SCRIPT_CONTENT" "/var/log/claude-agent"

# Test 14: Script sets up Python venv
assert_contains "install.sh creates Python venv" "$SCRIPT_CONTENT" "venv"
assert_contains "install.sh installs pip requirements" "$SCRIPT_CONTENT" "requirements.txt"

# Test 15: Script builds frontend
assert_contains "install.sh runs npm install" "$SCRIPT_CONTENT" "npm install"
assert_contains "install.sh runs npm run build" "$SCRIPT_CONTENT" "npm run build"

# Test 16: Script initializes database
assert_contains "install.sh initializes SQLite database" "$SCRIPT_CONTENT" "station.db"
assert_contains "install.sh references init_db" "$SCRIPT_CONTENT" "init_db"

# Test 17: Script installs systemd units
assert_contains "install.sh installs claude-agent.service" "$SCRIPT_CONTENT" "claude-agent.service"
assert_contains "install.sh installs claude-agent.timer" "$SCRIPT_CONTENT" "claude-agent.timer"
assert_contains "install.sh installs dashboard service" "$SCRIPT_CONTENT" "claude-station-dashboard.service"
assert_contains "install.sh reloads systemd" "$SCRIPT_CONTENT" "daemon-reload"

# Test 18: Script configures SELinux
assert_contains "install.sh handles SELinux" "$SCRIPT_CONTENT" "selinux\|SELinux\|semodule"

# Test 19: Script configures firewall
assert_contains "install.sh configures firewall" "$SCRIPT_CONTENT" "firewall-cmd"
assert_contains "install.sh opens port 8420" "$SCRIPT_CONTENT" "8420"

# Test 20: Script installs Claude CLI
assert_contains "install.sh references Claude CLI" "$SCRIPT_CONTENT" "claude-code\|claude"

# Test 21: Script is idempotent-safe (checks before creating)
assert_contains "install.sh checks if user exists before creating" "$SCRIPT_CONTENT" "id.*SERVICE_USER"
assert_contains "install.sh checks if dirs exist" "$SCRIPT_CONTENT" "! -d"
assert_contains "install.sh checks if config exists" "$SCRIPT_CONTENT" "! -f"

# Test 22: Script prints access URL
assert_contains "install.sh prints access URL" "$SCRIPT_CONTENT" "http://.*8420\|DASHBOARD_PORT"

# Test 23: Script prints first-run instructions
assert_contains "install.sh mentions GH_TOKEN setup" "$SCRIPT_CONTENT" "GH_TOKEN"
assert_contains "install.sh mentions claude login" "$SCRIPT_CONTENT" "claude login"

# Test 24: Script has error messages
assert_contains "install.sh has error handling" "$SCRIPT_CONTENT" "log_error"

# Test 25: --help works
HELP_OUTPUT=$(bash "${INSTALL_SCRIPT}" --help 2>&1 || true)
assert_contains "install.sh --help shows usage" "$HELP_OUTPUT" "Usage"
assert_contains "install.sh --help shows --dry-run" "$HELP_OUTPUT" "dry-run"

echo ""
echo "=== Results ==="
echo -e "Passed: ${GREEN}${PASS}${NC} / ${TOTAL}"
if [[ $FAIL -gt 0 ]]; then
    echo -e "Failed: ${RED}${FAIL}${NC} / ${TOTAL}"
    exit 1
else
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
fi
