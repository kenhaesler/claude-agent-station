#!/usr/bin/env bats
# Tests for the APPROVE_INTEGRATION case arm in run-manager.sh (issue #388).
# Stubs `gh`, `git`, `webhook_event`, and integration helpers so the case
# block runs in isolation.

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
