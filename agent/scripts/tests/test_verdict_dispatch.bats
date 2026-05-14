#!/usr/bin/env bats
# Contract test (source-grep, not executable) for the APPROVE_INTEGRATION
# case arm in run-manager.sh (issue #388).
#
# This test verifies the STRUCTURE of the case arm by reading the source —
# it does NOT execute the arm. Running the arm in isolation would require
# sourcing run-manager.sh's globals + reproducing its dispatch loop, which
# is out of scope for a smoke test. The `setup()` stubs below are scaffold
# for a future execution-mode test; today they are unused.
#
# What this catches:
#   - The case arm is present at all
#   - The arm contains git push, gh pr create (non-draft), gh pr merge
#     --auto --squash, webhook_event "verdict_execute"
#
# What this does NOT catch:
#   - Variable substitution / quoting bugs in the arm body
#   - Order of operations
#   - Behaviour under failure paths (no execution)

setup() {
    export TMPDIR_RUN="$(mktemp -d)"
    export PATH="${TMPDIR_RUN}/bin:${PATH}"
    mkdir -p "${TMPDIR_RUN}/bin"
    cat > "${TMPDIR_RUN}/bin/gh" <<'EOF'
#!/usr/bin/env bash
echo "GH_CALL:" "$@" >> "${TMPDIR_RUN}/calls.log"
if [[ "$1" == "pr" && "$2" == "create" ]]; then
    echo "https://github.com/owner/repo/pull/99"
fi
exit 0
EOF
    cat > "${TMPDIR_RUN}/bin/git" <<'EOF'
#!/usr/bin/env bash
echo "GIT_CALL:" "$@" >> "${TMPDIR_RUN}/calls.log"
exit 0
EOF
    chmod +x "${TMPDIR_RUN}/bin/gh" "${TMPDIR_RUN}/bin/git"
}

teardown() {
    rm -rf "${TMPDIR_RUN}"
}

@test "APPROVE_INTEGRATION runs push, non-draft pr create, auto-merge, issue comment" {
    grep -n "APPROVE_INTEGRATION)" agent/scripts/run-manager.sh
    [ "$status" -eq 0 ]

    grep -A 25 "APPROVE_INTEGRATION)" agent/scripts/run-manager.sh > "${TMPDIR_RUN}/arm.txt"

    # Must push the branch
    grep -q "git push" "${TMPDIR_RUN}/arm.txt"

    # Must create a non-draft PR — i.e. no "--draft" inside this arm
    ! grep -q -- "--draft" "${TMPDIR_RUN}/arm.txt"

    # Must invoke gh pr create with --base pr_base_branch
    grep -q "gh pr create" "${TMPDIR_RUN}/arm.txt"
    grep -q "\-\-base \"\$pr_base_branch\"" "${TMPDIR_RUN}/arm.txt"

    # Must arm auto-merge
    grep -q "gh pr merge --auto --squash" "${TMPDIR_RUN}/arm.txt"

    # Must emit a webhook event
    grep -q "webhook_event \"verdict_execute\"" "${TMPDIR_RUN}/arm.txt"
}
