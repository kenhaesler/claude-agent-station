# Project Vision — Design Spec

## Context

Claude Agent Station coordinates autonomous agents across multiple GitHub repositories. Today the agents pick work based on issue priority labels and free-form `custom_instructions` on the project record. They have no shared model of *what the project is for* or *where it is heading*.

A "vision" is a small structured document the user authors collaboratively with Claude. Once authored, it lives in the project's own repository at `docs/vision.md` and steers three orchestrator behaviours: which issues to pick first, whether teammate plans drift from the project's intent, and what new issues should exist that don't yet.

## Goals

1. Let users co-create a project vision through chat with Claude rather than writing one cold.
2. Store the vision in the project's own repo on GitHub so it's versioned with the code and visible outside the dashboard.
3. Use the vision at three points in the agent flow:
   - **Issue prioritisation** — score open issues by alignment, sort accordingly.
   - **Misalignment flag** — reject teammate plans that violate the vision's non-goals or anti-patterns.
   - **Gap detection** — periodically propose new issues that would help reach the vision.
4. Degrade gracefully: projects without a vision behave exactly as today.

## Non-goals

- Multi-file or per-team visions. One `docs/vision.md` per project.
- A markdown editor on the dashboard. Authoring is **chat only**.
- PR-based vision flow. Direct commit on chat completion; chat is the review.
- Auto-implementation of proposed gap-detection issues. Always requires human acceptance via label removal.
- Vision templates / starter packs. The first vision is built from scratch through chat.
- Dashboard-side vision diff/history UI. Git log on `docs/vision.md` is the history; the Vision tab links out to GitHub.
- Mid-implementation drift detection. Misalignment is checked at plan-review time only.

## Vision document structure

The vision is a markdown file with seven fixed H2 sections. Each section is markdown text — typically a short paragraph, not an essay.

| Section | Purpose | Used by hooks |
|---|---|---|
| `## Problem` | What pain the tool solves | 1 (prioritisation) |
| `## Users` | Who it's for and who it's not for | 1 |
| `## End-state` | What "done" or "succeeded" looks like, concretely | 1 |
| `## Non-goals` | Things deliberately out of scope | 2 (misalignment) |
| `## Principles` | How to choose when two good options conflict | 1, 2 |
| `## Horizons` | Near-term (3 mo), mid-term (12 mo), long-term direction | 1 (weights near-term work) |
| `## Anti-patterns` | Concrete examples of *bad* outcomes | 2 |

The file always begins with an H1 title `# Vision — <repo>` and a generated metadata line: `*Last refined: {ISO timestamp} via Claude Station*`.

## Architecture

```
                ┌──────────────────────────────────────┐
                │  GitHub: project repo                │
                │    docs/vision.md  ← source of truth │
                └──────────┬───────────────────────────┘
                           │
       ┌───────────────────┼───────────────────────┐
       │ Contents API      │ Contents API          │ filesystem read
       ▼                   ▼                       ▼
┌─────────────┐     ┌─────────────┐         ┌──────────────┐
│ Dashboard   │     │ Dashboard   │         │ Orchestrator │
│  - chat UI  │     │  - read for │         │  workspace   │
│  - commits  │     │   Vision    │         │  reads file  │
│   on save   │     │   tab/wiz   │         │  for hooks   │
└─────────────┘     └─────────────┘         └──────────────┘
```

- **Source of truth** is `docs/vision.md` on the project's configured base branch (default `main`). No DB column on `Project` for the document body.
- **Dashboard reads** via the GitHub Contents API using the App installation token. A small DB cache avoids hammering GitHub on every Vision-tab render.
- **Dashboard writes** via the Contents API direct to the base branch. Commit message: `"docs(vision): refine via Claude Station"`. Optimistic concurrency on the blob sha.
- **Orchestrator reads** from the local workspace clone (`<workspace>/docs/vision.md`). Already cloned for the run; no extra API calls. Missing file → all hooks no-op.
- **Chat** runs through `claude_agent_sdk.query` (bundled native CLI) — TOS-compliant, same path the orchestrator already uses.

## Data model

Three additions to the existing `Project` table:

```python
class Project(Base):
    # ... existing fields ...
    vision_cached_sha:  Mapped[str | None]      = mapped_column(Text, nullable=True)
    vision_cached_body: Mapped[str | None]      = mapped_column(Text, nullable=True)
    vision_cached_at:   Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

`vision_cached_sha` is the GitHub blob sha used for optimistic concurrency on writes. The cache TTL is 5 minutes; after that the dashboard re-fetches on next read.

One new table for in-flight chat sessions:

```python
class VisionChatSession(Base):
    __tablename__ = "vision_chat_sessions"
    id:              Mapped[str]                    # UUID
    project_id:      Mapped[int]                    # FK Project, unique (one active per project)
    state:           Mapped[Literal["active","approved","cancelled"]]
    phase:           Mapped[Literal["freeform","structured"]]
    coverage:        Mapped[dict]                   # {"problem": True, "users": False, ...}
    sdk_session_id:  Mapped[str | None]             # Claude Agent SDK session id, used for resume
    messages:        Mapped[list[dict]]             # [{"role": "user"|"assistant", "content": "..."}]
    assembled:       Mapped[dict | None]            # the structured vision once the model emits it
    created_at:      Mapped[datetime]
    updated_at:      Mapped[datetime]
```

Sessions older than 24h with `state == "active"` are auto-cancelled by the existing periodic-cleanup loop (the same background-task surface used by `app/services/stale_run_reaper.py`).

Versioning of the vision itself is git. We deliberately do **not** create a `vision_history` table — the file's commit log is the history.

## API design

### Vision document

```
GET    /api/projects/{id}/vision
       → { sha, body, last_refined_at, last_refined_by, cache_age_seconds }
       Cache-aware. Falls through to GitHub when stale or absent.
       404 if docs/vision.md does not exist on the base branch.

POST   /api/projects/{id}/vision
       Body: { vision_doc: { problem, users, end_state, non_goals,
                             principles, horizons, anti_patterns } }
       Renders to markdown using the fixed template, commits to GitHub.
       Updates the cache, marks any open chat session as "approved".
       409 on sha mismatch (concurrent external edit).

DELETE /api/projects/{id}/vision
       Removes docs/vision.md from the base branch (commit "docs(vision): remove").
       Optional — exposed but UI doesn't surface it in V1.
```

### Vision chat

```
POST   /api/projects/{id}/vision/chat
       Body (resume):  { session_id }
       Body (turn):    { session_id, message }
       Response: text/event-stream

       Events:
         assistant_text        — { delta: "..." }            (incremental text, append to current assistant message)
         coverage_update       — { covered: [...], remaining: [...] }
         phase_change          — { phase: "freeform" | "structured" }
         vision_ready          — { vision_doc: { ... } }     (model finished assembling)
         error                 — { code, message }
         done                  — turn complete

GET    /api/projects/{id}/vision/chat
       Returns the active session if one exists, including the message
       transcript. Used by the UI to rehydrate after a reload.
       404 if no active session.

DELETE /api/projects/{id}/vision/chat
       Marks the active session "cancelled". No GitHub side-effects.
```

### Gap detection (Phase 4)

```
POST   /api/projects/{id}/vision/find-gaps
       Triggers vision_analyst as a one-shot job.
       Returns { job_id }. Status is surfaced via existing run-event webhooks.
```

## UI flow

### Add Project wizard

The existing single-step Add Project modal becomes a 2-step wizard.

- **Step 1** is the current form (repo, branch, autonomy, mode, priority). Pressing **Next** saves the project to the DB immediately so step 2 has a `project_id` to scope against. Closing the wizard at this point leaves the project saved without a vision.
- **Step 2** is the vision chat. Component reused with the Vision tab.

Step 2 surfaces:
- Streaming chat transcript.
- A coverage indicator: seven section pills, ticked as the model marks them covered.
- Two terminal actions: **Skip for now** (closes the wizard, project stays vision-less) and **Approve & commit** (enabled once `vision_ready` has fired).

### Project Detail — Vision tab

A new tab on Project Detail next to existing tabs. Three states:

- **Empty** (no `docs/vision.md` on the base branch): shows an explanation and a single CTA **Start vision chat** that opens the same chat component inline.
- **Read** (file exists): renders the cached body as markdown. Header strip shows "Last refined {date} by {author}". Two actions: **Refine via chat** (opens chat overlay pre-loaded with the current vision) and **View on GitHub** (opens `https://github.com/<repo>/blob/<base>/docs/vision.md`).
- **Refining**: chat overlay covers the read view. Model gets a different system prompt that loads the current vision and asks what the user wants to change rather than starting from scratch. **Cancel** closes without committing.

The route `/projects/<id>/vision` opens Project Detail with the Vision tab active.

## Chat protocol

The chat is driven by a model running through `claude_agent_sdk.query`. Two phases:

1. **Free-form.** User describes the project. Model listens, asks one focused follow-up at a time, does not yet structure anything.
2. **Structured interview.** Once the model has enough free-form signal (or the user asks to move on), the model walks the seven sections and asks targeted questions only for ones not yet covered.

After every assistant response the model emits a small fenced JSON block the backend parses and strips before forwarding the visible text to the client:

````
…assistant prose the user reads…

```vision-meta
{ "phase": "freeform" | "structured",
  "covered": ["problem", "users", "end_state"],
  "ready_to_assemble": false }
```
````

When the user approves and asks the model to assemble, it outputs only:

````
```vision-doc
{ "problem": "...", "users": "...", "end_state": "...", "non_goals": "...",
  "principles": "...", "horizons": "...", "anti_patterns": "..." }
```
````

The backend extracts this and emits a `vision_ready` SSE event. The frontend then enables **Approve & commit**, which POSTs the structured object to `POST /api/projects/{id}/vision`.

If the model fails to emit `vision-meta` for two turns in a row, the backend injects a single reminder turn ("Please include the `vision-meta` JSON block at the end of your reply.") rather than blocking the conversation.

### System prompts

Two system prompts ship with the feature, kept short and stored in `agent/prompts/vision_create.md` and `agent/prompts/vision_refine.md`. Both reference the seven sections, the `vision-meta` contract, and the `vision-doc` assembly contract. They differ only in opening behaviour:

- **Create** prompt opens with: "Greet the user. Ask them to describe their project in their own words. Listen first; structure later."
- **Refine** prompt opens with: "The user has an existing vision below. Ask what they want to change, then probe only the sections they want to update." Followed by the current `docs/vision.md` content inlined.

## Orchestrator hooks

All three hooks read the vision the same way: a new `agent/vision.py` module exposes `load_vision(workspace) -> dict | None`. Returns `None` when `docs/vision.md` is missing. Every hook is a no-op on `None`.

### Hook 1 — Issue prioritisation

Plugs into `agent/station_orchestrator.py:617`, after `fetch_eligible_issues()` and before the existing label-priority sort.

```python
def score_issues_against_vision(
    issues: list[dict], vision: dict, model: str
) -> list[dict]:
    """Adds 'vision_score' (0–1) and 'vision_reason' to each issue.
    Falls back to score=0.5 for all issues on any failure."""
```

One LLM call per orchestrator run using `models.analyst` (sonnet by default). Inputs: the seven vision sections + each issue's title and body (truncated to 500 chars). Output: a JSON list `[{number, score, why}]`.

Final ordering:

```
combined_rank = priority_label_rank * (1 - w) + (1 - vision_score) * w
where w = config.vision.scoring_weight (default 0.4)
```

The `why` field is included in the lead's team prompt so it can tell teammates *why* an issue was picked.

Estimated cost: ~$0.005 per run.

### Hook 2 — Misalignment flag

Plugs into the lead agent's prompt builder (`build_team_prompt` in `agent/station_orchestrator.py:199`). When a vision is loaded, the lead's prompt gains a section:

```
## Vision check (when reviewing teammate plans)

Before approving ANY teammate plan, verify it does not violate the
non-goals or anti-patterns below. If it does:

1. Reject the plan with a specific quote from the violated section.
2. `gh issue edit <number> --add-label autonomous-agent/needs-help`
3. POST to /api/webhook/run-event with event=vision_misalignment
   (run_id, issue_number, violated_section, quote, plan_excerpt)
4. Reassign the teammate to a different task or stop them.

### Vision — Non-goals
{vision.non_goals}

### Vision — Anti-patterns
{vision.anti_patterns}

(Full vision available at <workspace>/docs/vision.md if you need other context.)
```

Only `non_goals` and `anti_patterns` are inlined to keep the lead's per-turn prompt cost flat (the lead reviews many plans per run). The lead can read the full file on demand via its existing `Read` tool, and already has `Bash` in `allowed_tools` for the `gh issue edit` and `curl` calls listed above.

The dashboard handles the new webhook event:
- New event-type `vision_misalignment` recorded in `agent_events`.
- Run timeline renders it with a distinct badge.
- A notification fires.

No additional LLM calls — the lead is already reviewing plans.

### Hook 3 — Gap detection

A new module `agent/vision_analyst.py`:

```bash
python -m agent.vision_analyst --project-id <id>
```

Steps:
1. Ensure workspace clone exists (call `setup_workspace` if needed).
2. Load `docs/vision.md`. Abort with non-zero exit if missing.
3. Inventory: file tree (top 200 files), `README.md`, last 50 commits, all open + last 100 closed issues via `gh`.
4. One LLM call with `models.analyst`: "Given this vision and the current state of the repo, what's missing? Propose ≤5 issues that would close the gap. JSON list of `{title, body, labels, priority}`."
5. For each proposal: `gh issue create --title --body --label vision-suggested,<priority>`. Body is prefixed with: *"Proposed by Claude Station based on the project vision. Review and accept by removing the `vision-suggested` label, or close to reject."*
6. The orchestrator's existing `SKIP_LABELS` is extended to include `vision-suggested` so proposed issues are not autonomously implemented.

Trigger surfaces:

- **Manual** — `POST /api/projects/{id}/vision/find-gaps` from the Vision tab.
  - Compose mode: dashboard POSTs to `<launcher>/run` with a payload selecting `vision_analyst` instead of `run-manager.sh`.
  - Systemd mode: a transient unit `claude-agent-vision-analyst@<id>.service`.
- **Scheduled** — out of scope for V1. Add later via a per-project cron field.

Estimated cost: ~$0.05 per gap check.

## Phasing

Each phase is a discrete, independently-shippable deliverable. Phases 2–4 are gated by `config.vision.enabled` (default `true`) so any single hook can be disabled without code changes; Phase 1 needs no flag because it adds new surfaces without modifying agent behaviour.

### Phase 1 — Vision authoring (foundation)

| Component | Work |
|---|---|
| Schema | Alembic migration adds three columns to `projects`; creates `vision_chat_sessions`. |
| Backend | `app/services/vision_chat.py` (chat session state machine); `app/services/github_contents.py` (read/write `docs/vision.md`); `app/routers/vision.py` (the four endpoints above). |
| Prompts | `agent/prompts/vision_create.md`, `agent/prompts/vision_refine.md`. |
| Frontend types/API | `Vision`, `VisionChatSession`, `VisionDoc` in `lib/types.ts`. New API client functions in `lib/api.ts`. |
| Frontend UI | `AddProjectModal.svelte` becomes 2-step. New `VisionChat.svelte` (shared between wizard step 2 and tab). New `VisionTab.svelte` on Project Detail. |
| Tests | Backend: chat-session state machine; `vision-meta` parser; GitHub Contents service with VCR fixtures; commit endpoint with sha conflict. Frontend: SSE handling; coverage update events; markdown rendering. |

**Done when:** a fresh project flows through the wizard, the user finishes the chat, `docs/vision.md` appears on GitHub on the base branch, the Vision tab shows it.

### Phase 2 — Issue prioritisation (Hook 1)

| Component | Work |
|---|---|
| Agent | `agent/vision.py:load_vision`. `agent/scoring.py:score_issues_against_vision`. Wire into `orchestrate()` after `fetch_eligible_issues`. |
| Config | `config.vision.scoring_weight` (default 0.4). Document in `ARCHITECTURE.md`. |
| Prompt | Inject per-issue "why this was picked" into `build_team_prompt`. |
| Tests | No vision = identity sort. Malformed scoring output = fallback. Vision present + contrived issues = expected reorder. |

**Done when:** with a vision in place, the run log shows "Picked #N because it advances <end_state>" for each issue, and the order matches the alignment scores.

### Phase 3 — Misalignment flag (Hook 2)

| Component | Work |
|---|---|
| Prompt | `## Vision check` section in `build_team_prompt`. |
| Webhook | `vision_misalignment` event-type in `app/routers/webhook.py`; persists to `agent_events`. |
| Frontend | Misalignment badge on run timeline; notification. |
| Tests | E2E: contrived issue that violates a stated non-goal → lead rejects + label gets applied + webhook fires. |

**Done when:** a teammate plan that violates the vision causes a `vision_misalignment` event in the run timeline and an `autonomous-agent/needs-help` label on the issue.

### Phase 4 — Gap detection (Hook 3)

| Component | Work |
|---|---|
| Agent | `agent/vision_analyst.py` + CLI entry point. |
| Backend | `POST /api/projects/{id}/vision/find-gaps` triggers it via launcher (compose) or transient unit (systemd). |
| Frontend | "Find gaps" button on Vision tab. Toast on success listing proposed issue numbers. |
| Orchestrator | Add `vision-suggested` to `SKIP_LABELS`. |
| Tests | Mock GitHub responses → analyst proposes N issues → labels applied → SKIP_LABELS prevents pickup. |

**Done when:** on a project with a vision and a real codebase, clicking **Find gaps** opens 1–5 new issues on GitHub labelled `vision-suggested`, and the next orchestrator run skips them.

## Failure modes & mitigations

| Failure | Behaviour | Mitigation |
|---|---|---|
| `docs/vision.md` missing | Hooks 1, 2, 3 silently no-op | No mitigation needed — projects without a vision behave exactly as today |
| Vision file present but malformed (missing required H2 section) | Hooks log a warning and proceed with whatever sections parsed | Tolerant parser; log identifies missing sections |
| `vision-meta` not emitted for >2 turns | Backend injects one reminder turn | Soft contract; conversation continues |
| GitHub Contents API write fails | Chat session stays `active`; UI shows error toast | User can retry from the chat |
| Concurrent external edit (sha mismatch) | API returns 409; UI prompts "external edit detected, please reload" | One-session-per-project mitigates the dashboard side; remaining race is GitHub-side, rare |
| Repo has no `docs/` directory | Contents API auto-creates path on PUT | Smoke test in Phase 1 confirms |
| Hook 1 LLM call fails | All issues get `score=0.5`; falls back to label-priority sort | Run continues |
| Hook 2 false positives (over-eager rejection) | Lead requires a *specific quote* from the violated section before rejecting | Tunable via prompt |
| Hook 3 LLM call fails | "Find gaps" surfaces the error in the dashboard toast | User can retry; no partial state to clean up |

## Compliance

All inference for the chat backend, Hook 1, Hook 2, and Hook 3 routes through `claude_agent_sdk.query`, which spawns the bundled native `claude` CLI. No direct calls to `/v1/messages` or any raw Anthropic API. Same path the existing orchestrator already uses, consistent with the project's TOS posture.

## Out of scope (deliberately)

- Multi-file or per-team visions. One `docs/vision.md` per project.
- Markdown editor on the dashboard. Authoring is chat only.
- PR-based vision flow. Direct commit on chat completion.
- Auto-implementation of proposed gap-detection issues.
- Vision templates / starter packs.
- Vision diff/history UI in the dashboard. Git is the history.
- Mid-implementation drift detection. Plan-time only.
- Stale-vision warnings ("Last refined 6 months ago"). Possible follow-up if visions accumulate without refinement in practice.
- Scheduled gap-detection cron. Manual trigger only in V1.
- "Soft" misalignment (warning instead of stop). Either the plan violates a non-goal/anti-pattern or it doesn't.

## Open follow-ups (post-V1)

These are tracked here so they don't get lost but are not part of the spec's commitments:

- Per-project gap-detection cron schedule.
- Vision staleness indicator.
- Allow users to cite the vision in custom GitHub issue comments via a slash command.
- Multi-vision per project for sub-systems (only if a real use case appears).
