# `ClaudeSDKClient` Migration for Long-Running Agent Teams Sessions — Design

**Status**: design
**Date**: 2026-05-14
**Issue**: [#384](https://github.com/kenhaesler/claude-agent-station/issues/384) — Tier 1 / Issue B of epic [#382](https://github.com/kenhaesler/claude-agent-station/issues/382)
**Depends on / supersedes**: PR #381 (the temporary fixes this migration replaces)

## Context

`agent/station_orchestrator.py::orchestrate` drives Agent Teams sessions that routinely
run 30–50 minutes and emit thousands of stream messages. Today it does so via
`claude_agent_sdk.query()` — the SDK's one-shot helper. Per the Claude Agent SDK
README:

> Use `query()` for: Simple one-off questions, batch processing, automated scripts.
> Use `ClaudeSDKClient` for: Long-running sessions with state, interactive conversations
> with follow-ups, when you need interrupt capabilities.

We have been pushing `query()` well outside its design envelope. Every fix in PR #381
is a workaround for that mismatch:

- `_user_prompt_stream` (`agent/station_orchestrator.py:81–137`) yields a single user
  message and then deliberately `asyncio.sleep(3600)`s so the SDK's
  `Query.stream_input` cannot exit its `async for` and call
  `wait_for_result_and_end_input()`. That call closes stdin to the bundled CLI,
  after which every PreToolUse / PostToolUse hook callback fails with
  `Error: Stream closed` (cli.js:7552 `sendRequest`). The hang-forever generator
  is the only known way to keep stdin open across teammate spawns under `query()`.
- The inner `async for message in query(...)` loop at
  `agent/station_orchestrator.py:2047` adds an explicit `break` when
  `_is_work_complete(result_text)` matches (line 2099–2106), because PR #381 had to
  remove the SDK's incidental stream termination but the consumer side still needs
  a way to stop reading.
- `_force_exit_with_cleanup` (`agent/station_orchestrator.py:2460–2505`) walks
  `/proc` and SIGTERMs every direct child, then calls `os._exit(exit_code)` to
  bypass Python's `asyncio.run` finalizer. The finalizer reliably hangs trying to
  close `query()`'s anyio task group when the bundled CLI subprocess has not
  exited on its own.
- `agent/launcher.py:339` sets `CLAUDE_CODE_STREAM_CLOSE_TIMEOUT=1800000` (30 min)
  — a band-aid for the same stdin-close behaviour. With `ClaudeSDKClient`, the
  client owns its own lifecycle and this env var becomes irrelevant (see #387).

The core mismatch is structural: `query()` is internally a context manager that
opens, transmits a single user input, awaits replies, and tears down. We need
a *session* — open once, send turn N, receive replies, send turn N+1, receive
replies, …, close once. That is exactly what `ClaudeSDKClient` provides.

## Goals

- `agent.station_orchestrator.orchestrate` runs a single Agent Teams session as
  an `async with ClaudeSDKClient(...)` block whose lifetime spans every
  re-entry / follow-up turn.
- The "keep stdin open forever" `_user_prompt_stream` generator is deleted.
- `_force_exit_with_cleanup` is deleted; `asyncio.run(orchestrate(...))` exits
  cleanly with zero `asyncio.run() shutdown` warnings.
- Re-entry (`build_followup_prompt` flow) sends the next user message via
  `client.query(prompt)` on the *same* client, no resume token gymnastics.
- Stop requests use `client.interrupt()` / context-manager exit, not a `break`
  layered on top of a heuristic.
- Production runs leave zero lingering `claude` / bundled-CLI children after the
  orchestrator's Python process exits.

## Non-goals

- Changing webhook payload shapes or `run_id` semantics (dashboard compatibility).
- Replacing `_is_work_complete` — that is #385's job. This spec keeps the
  text-heuristic completion check in place; #385 will swap it for the
  `RunComplete` SDK tool once #384 has landed.
- Removing `CLAUDE_CODE_STREAM_CLOSE_TIMEOUT` — that is #387 (Tier 3 D).
- Touching `agent/audit_hook.py` beyond confirming hooks still attach to the
  `ClaudeAgentOptions` we hand to the client (the hook API is unchanged).
- Migrating the `agent/vision_analyst.py` `query()` call — that is short-lived
  and out of scope here. The migration is targeted at the long-session caller.

## Approach

### Files touched

| File | Change |
|---|---|
| `agent/station_orchestrator.py` | Rewrite `orchestrate` core; delete `_user_prompt_stream`; delete `_force_exit_with_cleanup`; rework re-entry loop |
| `agent/station_orchestrator.py` (CLI) | `main()` no longer wraps `asyncio.run` in `_force_exit_with_cleanup` |
| `dashboard/backend/tests/test_force_exit_cleanup.py` | Delete |
| `dashboard/backend/tests/test_orchestrator_wiring.py` | Update to assert `ClaudeSDKClient` usage, remove assertions about `_user_prompt_stream` |
| `dashboard/backend/tests/test_orchestrator_clientsdk.py` | **New** — covers the migration paths |

### New control flow

The current loop (simplified) lives between
`agent/station_orchestrator.py:1937–2118`:

```python
# Current (PR #381 era)
for iteration in range(max_reentries):
    prompt = build_team_prompt(...) if iteration == 0 else build_followup_prompt(...)
    options = ClaudeAgentOptions(...)
    if is_followup and session_id:
        options.resume = session_id
        options.continue_conversation = True
    async for message in query(prompt=_user_prompt_stream(prompt), options=options):
        ...
        if isinstance(message, ResultMessage):
            result_text = getattr(message, "result", "")
            if _is_work_complete(result_text):
                work_complete = True
                break
    if work_complete: break
    await asyncio.sleep(15)
```

After the migration:

```python
# Target
options = ClaudeAgentOptions(...)  # built once; no resume gymnastics
async with ClaudeSDKClient(options=options) as client:
    await client.query(build_team_prompt(...))
    work_complete = False
    for iteration in range(max_reentries):
        async for message in client.receive_response():
            handle_stream_event(message, ...)
            if control_flags["stop"]:
                await client.interrupt()
                raise OrchestratorStopRequested()
            if isinstance(message, ResultMessage):
                if _is_work_complete(getattr(message, "result", "")):
                    work_complete = True
                    break  # exits the receive_response iterator naturally
        if work_complete or control_flags["stop"]:
            break
        # Re-entry: keep the same client, send a follow-up
        await client.query(build_followup_prompt(
            workspace,
            operator_messages=pending_operator_messages,
        ))
        pending_operator_messages.clear()
        await asyncio.sleep(15)  # idle wait preserved as a courtesy
```

Key contract changes versus today:

- `ClaudeAgentOptions.resume` / `continue_conversation` are gone — a single
  client persists the session for us. Resume tokens were only there to stitch
  together what `query()` could not.
- The inner stop check no longer relies on the SDK closing stdin on its own —
  `client.interrupt()` is the documented stop primitive.
- `_user_prompt_stream` (lines 81–137) is **deleted**. The prompt-as-async-iterable
  workaround exists solely because `query()` closes stdin after the first
  `ResultMessage`; `client.query(str)` accepts the prompt directly.
- The inner `if isinstance(message, ResultMessage) and _is_work_complete(...): break`
  block survives unchanged for now (it is the same heuristic), but it now
  exits the iterator naturally instead of "fighting" the SDK.

### Hooks, tool policy, and audit log

`make_audited_policy`, `make_pre_tool_hook`, `make_post_tool_hook` (see
`agent/audit_hook.py`) attach via `ClaudeAgentOptions.can_use_tool` and `hooks`,
both of which `ClaudeSDKClient` accepts identically. The hook stops failing
with `Error: Stream closed` because the client's stdin lifetime extends across
every `client.query(...)` until the `async with` block exits.

`get_hook_callback_failure_count()` already exists; we add a single integration
test that asserts the counter is **zero** after a simulated 6-iteration run.

### Stop semantics

`_control_poll_loop` (`agent/station_orchestrator.py:1195`) continues to run
in the background as a task. Today it only flips `control_flags["stop"]`;
after migration the inner loop also calls `await client.interrupt()` once,
guarded so we don't double-interrupt:

```python
if control_flags["stop"] and not stop_signalled:
    await client.interrupt()
    stop_signalled = True
    raise OrchestratorStopRequested()
```

`ClaudeSDKClient.__aexit__` then tears down the bundled CLI on its own,
which is exactly the lifecycle hole `_force_exit_with_cleanup` exists to
paper over.

### Removing `_force_exit_with_cleanup`

`main()` returns to the plain form:

```python
def main() -> None:
    logging.basicConfig(...)
    args = parse_args()
    if args.driver:
        sys.exit(RunDriver(...).run())
    config = load_config(args.config)
    sys.exit(asyncio.run(orchestrate(config, args.run_id, args.workspaces_dir)))
```

If `asyncio.run` still emits `unhandled exception during asyncio.run() shutdown`
in the new path, that is a regression we treat as a release blocker — there are
no remaining structural reasons for it once the long-lived client owns its
teardown.

### Operator-facing behaviour

No change visible from the dashboard:

- Webhook events fire on the same boundaries (`narration`, `progress_update`,
  `orchestrator_complete`, etc.). `handle_stream_event` is untouched in this
  spec (line 1314).
- Run logs still go to `/var/log/claude-agent/run-<id>-orchestrator.stream.jsonl`.
- `max_reentries = 6` stays.

## Acceptance criteria

Lifted from the issue body, expanded for "what done looks like":

- [ ] **`agent.station_orchestrator.orchestrate` uses `ClaudeSDKClient` instead
  of `query()`.** Concretely: an `async with ClaudeSDKClient(options=options)
  as client` block surrounds the re-entry loop, and the inner loop uses
  `await client.query(...)` plus `async for message in client.receive_response()`.
  No `query(prompt=..., options=...)` calls remain in `orchestrate`.
- [ ] **`_user_prompt_stream` function deleted.** Lines 81–137 removed. Any
  test that imported the symbol updated or removed.
- [ ] **`_force_exit_with_cleanup` function deleted.** Lines 2460–2505 removed
  and `main()` returns to a plain `sys.exit(asyncio.run(...))`. The associated
  test file (`test_force_exit_cleanup.py`) is removed.
- [ ] **`work_complete` inner-loop `break` simplified to natural exit.** The
  inner `if isinstance(message, ResultMessage): ... break` remains as the
  completion gate, but the outer "did the SDK actually close stdin for us"
  workaround comment block is replaced with a one-line comment explaining
  the client owns lifetime now.
- [ ] **Zero `ERROR asyncio: unhandled exception during asyncio.run() shutdown`
  in production runs.** Verified by `grep` over a fresh smoke-test launcher
  log; CI run for the smoke test asserts the message is absent.
- [ ] **No lingering processes after orchestrator completion.** Smoke test
  reads `/proc` after `orchestrate` returns (or via the launcher's pid-watch)
  and asserts the orchestrator's PID has no surviving children.
- [ ] **Test coverage equivalent to or better than PR #381's 29 tests.** New
  `test_orchestrator_clientsdk.py` includes: client lifecycle (open→follow-up→
  close), interrupt path, hook-failure-counter-zero, no-resume-token round-trip,
  re-entry over 3 iterations with a captured `session_id` parity check.
- [ ] **Run-20260513T151408Z-equivalent smoke test passes** on `dev` after
  promotion. The smoke test wires a 2-issue Agent Teams run against a sandbox
  repo and asserts: (a) every teammate hook callback completes (audit-log row
  count > 0 and zero `hook_callback_failures`), (b) final
  `orchestrator_complete` arrives with `is_error=False`, (c) PID tree clean.

## Dependencies / blocks

- **Builds on**: PR #381 (this migration is the strangler-pattern follow-up;
  the temporary fixes are what we are removing).
- **Blocks**: [#385](https://github.com/kenhaesler/claude-agent-station/issues/385)
  — `RunComplete` tool replaces the inner-loop heuristic; it cleanly slots
  into the new `async for message in client.receive_response()` loop because
  tool-use events arrive on the same stream.
- **Blocks**: [#383](https://github.com/kenhaesler/claude-agent-station/issues/383)
  — once `orchestrate` is reliable on its own, deleting `run-manager.sh` no
  longer carries the risk of losing a bash-side safety net.
- **Unblocks**: [#387](https://github.com/kenhaesler/claude-agent-station/issues/387)
  — `CLAUDE_CODE_STREAM_CLOSE_TIMEOUT` becomes safe to remove from
  `agent/launcher.py:339`.
- **Independent of**: `agent/audit_hook.py`, `agent/run_control.py`,
  `agent/webhook_emitter.py`. These already work with `ClaudeAgentOptions`
  passed through; nothing about the migration changes their contract.

## Risks and rollback

| Risk | Mitigation |
|---|---|
| `ClaudeSDKClient`'s `interrupt()` semantics differ from what the dashboard expects when an operator clicks **Stop**. | Smoke test the Stop button against a long-running fake Agent Teams session; assert `orchestrator_complete` with `status="interrupted"` fires. If the SDK swallows messages after `interrupt()`, the outer task wrapper emits the webhook directly (mirroring the current `OrchestratorStopRequested` branch at lines 2126–2137). |
| The SDK's `receive_response()` and `query(...)` interleaving model is subtly different from `async for message in query(...)`. | Read `claude_agent_sdk/client.py` end-to-end before implementing; mirror the README's "follow-up" example verbatim for the re-entry path. Add a test that asserts a follow-up `query()` arrives at the lead after the inner `async for` exits. |
| Hooks attach to the *first* options instance but not subsequent `query()` calls if we ever rebuild the client. | We only construct the client once per project iteration, so hooks attach once. Test asserts hook callback count > 0 over a multi-iteration run. |
| Removing `_force_exit_with_cleanup` reveals a *different* lifecycle bug we have been masking. | Behind-the-flag toggle is not practical (it is a single code path), but the migration ships behind a one-release escape hatch: `STATION_ORCHESTRATOR_USE_LEGACY_QUERY=1` keeps the old `query()` path alive for one release. Removed in the release after. |

**Rollback**: revert the PR. The pre-#384 code is independent of the rest of
the epic-382 work — #385 and #383 land *after* this, not in parallel.

## Test strategy

### Unit (new file: `dashboard/backend/tests/test_orchestrator_clientsdk.py`)

- `test_client_opens_and_closes_once_per_project`: assert `ClaudeSDKClient`
  is entered exactly once per project iteration, by mocking the client and
  capturing call order.
- `test_followup_uses_same_client`: assert `client.query` is called for
  iteration 1, 2, 3 without a new client instance.
- `test_interrupt_on_stop`: set `control_flags["stop"]=True` mid-stream;
  assert `client.interrupt()` is awaited exactly once and
  `OrchestratorStopRequested` propagates.
- `test_no_resume_token_passed`: `ClaudeAgentOptions.resume` is never set
  during the run.
- `test_hook_failure_counter_zero_after_six_iterations`: simulate 6 reentries
  feeding a fake stream of `AssistantMessage` + `ResultMessage`; assert
  `get_hook_callback_failure_count()` returns 0.

### Update (`dashboard/backend/tests/test_orchestrator_wiring.py`)

- Remove `_user_prompt_stream` import assertions.
- Add assertion that the orchestrator module no longer references
  `_user_prompt_stream`, `_force_exit_with_cleanup`.

### Delete (`dashboard/backend/tests/test_force_exit_cleanup.py`)

- The whole file is obsolete. The capability it tested no longer exists.

### Integration / smoke

- Reuse the `run-20260513T151408Z` reference fixture: a 2-issue queue against
  a sandbox repo. Assert:
  1. `orchestrator_complete` fires once with `is_error=False`.
  2. Stream JSONL has at least one teammate-spawn and one teammate-complete
     event.
  3. `audit_log` rows exist for both `lead` and each teammate; zero rows
     have `status="hook_failed"`.
  4. After the orchestrator process exits, `pgrep -P <pid>` returns no
     children.
  5. `journalctl -u claude-agent-station` shows zero
     `unhandled exception during asyncio.run() shutdown` lines.

### Manual

- Deploy to staging, click Trigger Run, watch Mission Control through one
  full run, click Stop on a follow-up iteration, confirm the run ends within
  ~5 s with `status="interrupted"` and clean PID tree.

## Open questions

- Does `ClaudeSDKClient` need an explicit `await client.disconnect()` before
  the `async with` exits to guarantee teardown ordering on the bundled CLI?
  The README implies the context manager handles it; verify against the
  SDK source before implementation.
- For the re-entry path, do we need to call `client.query(...)` *before* the
  next `client.receive_response()`, or does `receive_response()` block until
  the next user message regardless? Same: verify against SDK source.

These are implementation specifics — they shape the inner loop's exact
ordering but not the design.

> **Note**: As of this writing, `agent/station_orchestrator.py` line numbers
> reference the post-PR-#381 tree. Any drift between this spec and the file
> when implementation begins should be reconciled against the current
> `orchestrate` body, not against quoted line numbers.
