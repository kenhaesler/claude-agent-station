# Vision: Tech Stack + Runtime Target Design

**Date:** 2026-05-10
**Status:** Draft — pending user review
**Issue:** #335

## Problem

The Project Vision document (`docs/vision.md`) today captures seven narrative sections about a project — Problem, Users, End-state, Non-goals, Principles, Horizons, Anti-patterns — but says nothing about *what the project is built with* or *where it's intended to run*. Both materially shape what the lead and teammate agents should generate, scaffold, and validate. Without them, downstream agents either re-ask the user or guess (often badly).

Issue #335 asks the Vision wizard to also collect Tech Stack and Runtime Target so the information is captured up front, versioned with the code, and consumed by every agent that reads the vision.

## Goals

- Extend the Vision schema from 7 to 9 sections.
- Collect the new sections through the existing chat-driven wizard. No new UI, no new flow, no DB schema change.
- Existing visions keep working without the new sections; backfill is user-driven through the regular refine flow.
- Downstream agents (lead, teammates) get the new context automatically because they already read the rendered vision body.

## Non-goals

- Auto-detecting tech stack from existing repo contents. The issue body punts on this — could be a follow-up.
- Multi-select chips or canonical-vocabulary normalization. Both sections are free-text markdown, same as the existing seven.
- Surfacing the fields as separate first-class metadata on the `Project` table or anywhere outside the vision body.
- Migrating existing `docs/vision.md` files. Old visions stay 7-section until the user refines them.
- Auto-rendering a structured Tech-Stack badge on the Vision tab. The tab renders raw markdown today; new sections appear in that prose without special treatment.

## Storage

Continues to be `docs/vision.md` in the project's repo — the same source-of-truth contract defined in `2026-05-07-project-vision-design.md`. Two new H2 sections are inserted at positions 4 and 5:

```
1. ## Problem
2. ## Users
3. ## End-state
4. ## Tech Stack          ← new
5. ## Runtime Target      ← new
6. ## Non-goals
7. ## Principles
8. ## Horizons
9. ## Anti-patterns
```

Reasoning for placement: tech stack and runtime are about the *shape of the work* — they fit naturally between "what success looks like" and "what to avoid." Putting them last would feel like an afterthought; putting them first would dilute the narrative opening.

## Data model

`VisionDoc` Pydantic model grows two optional fields with empty-string defaults:

```python
class VisionDoc(BaseModel):
    problem: str
    users: str
    end_state: str
    tech_stack: str = ""        # new
    runtime_target: str = ""    # new
    non_goals: str
    principles: str
    horizons: str
    anti_patterns: str
```

Why optional with default `""`:

- Old `vision-doc` JSON blocks (from before this change) don't include the new keys. Pydantic fills the empty string; validation passes.
- New chat sessions produce all 9 keys; the prompt is what guarantees that the new fields are non-empty in the canonical case.
- The render layer treats empty new fields the same way it already treats empty existing fields: emits the heading with a `_(not specified)_` placeholder body. After a user refines an old vision, the file gains two new H2 sections (possibly with the placeholder). Old visions that have never been refined since this change continue to render with seven sections — the renderer only runs at commit time.

No DB schema change. No migration. `vision_cached_body` on the `Project` table continues to hold whatever the project's repo currently has; when the user refines a vision and adds the new sections, the body is rewritten end-to-end on the next commit.

## Render

`dashboard/backend/app/services/vision_render.py` turns the dict into markdown of the form `## Section\n\nbody\n\n` by iterating an ordered list of `(field_name, heading_text)` pairs. **One change**:

Insert the two new pairs at positions 4 and 5:

```python
("tech_stack", "Tech Stack"),
("runtime_target", "Runtime Target"),
```

Empty sections keep the existing `_(not specified)_` placeholder rule (`vision_render.py:31-32`) — same as the existing seven sections today. Old visions, after the user refines them through the chat once, render with 9 H2 headings (two of which may say `_(not specified)_` if the user skipped them). Keeping the rule uniform preserves the "orchestrator hooks rely on a consistent shape" contract documented in the existing render docstring.

## Chat prompts

Two markdown files extend their section list. Both are pure text edits.

### `agent/prompts/vision_create.md`

The structured-interview phase currently enumerates the seven sections. Replace with the nine-section list in the order above, with one-line descriptions for the two new entries:

- **Tech Stack** — the languages, frameworks, and key libraries
- **Runtime Target** — where the application is intended to run (Linux host, container, serverless, edge, embedded)

Update "seven sections" → "nine sections" everywhere in the prose. Update the `valid section names` enumeration: `problem`, `users`, `end_state`, `tech_stack`, `runtime_target`, `non_goals`, `principles`, `horizons`, `anti_patterns`. Update the `vision-doc` example block so it shows all 9 keys.

### `agent/prompts/vision_refine.md`

Same list extension. The refine prompt handles "I want to update X" / "I want to add Y" requests. With the two new section names in its list, asking "add the tech stack" lands in the right code path automatically — no new branching or special handling.

## Parser and section lists in the dashboard

The dashboard has two other places that enumerate the section set:

- **`dashboard/backend/app/services/vision_chat_parser.py:21-24`** — `_REQUIRED_DOC_KEYS` is a `set[str]` used to validate the chat's `vision-doc` JSON for structural completeness.
- **`dashboard/backend/app/services/vision_chat.py:144-147`** — `_SECTIONS` is a list used by the chat lifecycle.

**Important constraint**: `_REQUIRED_DOC_KEYS` must NOT gain the two new entries. If it does, chats predating this change (e.g. resumed from cache) fail validation. The optional-field decision in the schema is what allows the parser to keep its 7-key required set while accepting 9-key payloads — Pydantic fills in the empty defaults.

**`_SECTIONS` in `vision_chat.py`** does get the two new entries (positions 4 and 5). This list drives the chat's lifecycle (which sections to track coverage on), and we want the chat to ask about them.

## Orchestrator-side parser

**`agent/vision.py:17-25`** has its own `SECTIONS` list. This is the orchestrator-side parser (`load_vision()`) that reads `docs/vision.md` back into a dict for lead/teammate consumption. Without updating this list, the orchestrator silently drops `## Tech Stack` and `## Runtime Target` from the parsed dict and the new sections never reach any agent. **Adding the two pairs here is load-bearing for the issue's stated goal**: "downstream agents (planning, implementation, QA) can tailor their behavior."

## Agent prompts that already consume the parsed dict

Three orchestrator-side files read fields from the parsed vision dict by key. Each needs the two new keys plumbed through:

- **`agent/vision_analyst.py`** — format-string template at lines 124-142 has `{problem}`, `{users}`, …, `{anti_patterns}`. Lines 216-218 pass them as kwargs to `.format(...)`. Add `{tech_stack}` and `{runtime_target}` to the template (between `{end_state}` and `{non_goals}`) and pass `tech_stack=vision["tech_stack"]`, `runtime_target=vision["runtime_target"]` in the kwargs call.
- **`agent/vision_scoring.py`** — format-string template at lines 21-39. Same shape edit.
- **`agent/station_orchestrator.py:778-779`** — the lead's prompt assembly currently exposes `non_goals` and `anti_patterns` to the lead under "Vision check" headings (used for plan misalignment review). Add a small additional context block under the lead prompt:

  ```python
  tech_stack_text = (vision.get("tech_stack") or "").strip() or "_(not specified)_"
  runtime_target_text = (vision.get("runtime_target") or "").strip() or "_(not specified)_"
  ```

  And append to `vision_section`:

  ```markdown
  ### Vision — Tech Stack
  {tech_stack_text}

  ### Vision — Runtime Target
  {runtime_target_text}
  ```

  This is what gives the lead — and therefore the spawned teammates — programmatic access to the new context. The earlier draft of this spec claimed "no orchestrator changes needed" — that was wrong.

## Frontend

`dashboard/frontend/src/components/vision/VisionTab.svelte:184` displays `vision.body` literally inside a `<pre class="whitespace-pre-wrap font-mono text-xs">` block — that is, the markdown *source* renders verbatim (hashes and all), not as styled HTML. The new sections appear automatically in that source text. No component changes.

No `VisionRead` type-shape change either — `body: string` already covers both 7-section and 9-section payloads. No frontend types update.

## Backward compatibility

- Old `docs/vision.md` files with seven sections render unchanged in the Vision tab.
- Old `vision_cached_body` rows in the DB are valid input for the renderer and for orchestrator consumption — both treat the body as prose.
- Old `vision-doc` JSON blocks (e.g. if a chat is resumed from a stale cache) validate successfully against the extended `VisionDoc`, with the two new fields defaulting to `""`.
- Old visions get the new sections only when the user explicitly refines them. There is no auto-migration, no banner prompt, no opt-in flow.

## Testing

Updates to `dashboard/backend/tests/test_vision_render.py`:

1. **Update `test_render_includes_all_seven_sections_in_order`** — currently asserts exactly 7 headings in order. Rename to `..._all_nine_sections_...` and add the two new headings in positions 4 and 5. The existing test fixture also needs the two new keys.
2. **Update `test_render_handles_empty_section_with_placeholder`** — extend the fixture so it includes empty `tech_stack` and `runtime_target`. The placeholder assertion still passes because the rule applies uniformly to all 9 sections.

Add new tests:

3. **`test_vision_doc_optional_fields`** — `VisionDoc.model_validate(payload_without_new_fields)` succeeds (old chat output validates); both new fields default to `""`; round-trip through `model_dump()` includes the empty values. Located in `dashboard/backend/tests/test_vision_render.py` or a new `test_vision_schemas.py`.

Add a new test file `dashboard/backend/tests/test_vision_chat_parser_required_keys.py`:

4. **`test_required_keys_unchanged`** — `_REQUIRED_DOC_KEYS` from `vision_chat_parser.py` is exactly the original 7 strings. Locks down the constraint that new fields are NOT added to the required set (so old chat payloads keep validating).

Add tests on the orchestrator side at `dashboard/backend/tests/test_agent_vision.py` (existing file):

5. **`test_load_vision_parses_new_sections`** — given a vision file with `## Tech Stack` and `## Runtime Target` content, `agent.vision.load_vision()` returns a dict with `tech_stack` and `runtime_target` populated.
6. **`test_load_vision_old_file_defaults_new_keys_to_empty`** — given a vision file with only the original 7 sections, `load_vision()` returns a dict whose `tech_stack` and `runtime_target` keys exist and equal `""`. Regression test for the parser fallback.

No frontend tests (the Vision tab shows raw markdown text — visual diff is unchanged for old visions and self-evident for new ones). No prompt-contract tests (the vision-chat prompts are already excluded from `test_prompt_contracts._NON_ROLE_PROMPTS`; the prompt edits don't change structural shape).

## File and component summary

**Modified files (dashboard):**

- `dashboard/backend/app/schemas.py` — `VisionDoc` gains two fields with default `""`.
- `dashboard/backend/app/services/vision_render.py` — two new render pairs at positions 4 and 5. No change to the placeholder behaviour.
- `dashboard/backend/app/services/vision_chat.py` — `_SECTIONS` list at lines 144-147 gets the two new entries.
- `dashboard/backend/tests/test_vision_render.py` — update existing 7-section tests to 9 sections; add `test_vision_doc_optional_fields`.

**Modified files (orchestrator):**

- `agent/vision.py` — `SECTIONS` list at lines 17-25 gets the two new pairs. Load_vision returns a dict with the new keys (empty string when absent from the file).
- `agent/vision_analyst.py` — format-string template (lines 124-142) gets `{tech_stack}` and `{runtime_target}` placeholders between `{end_state}` and `{non_goals}`; kwargs at lines 216-218 pass the values.
- `agent/vision_scoring.py` — same shape edit on its format-string template.
- `agent/station_orchestrator.py` — `vision_section` assembly (around line 778) gains two `### Vision — Tech Stack` / `### Vision — Runtime Target` blocks so the lead prompt — and via spawn-instructions, the teammates — see the new context.
- `agent/prompts/vision_create.md` — section list extended from 7 to 9 in three places (prose, valid-names list, example block).
- `agent/prompts/vision_refine.md` — section list extended from 7 to 9.

**New files:**

- `dashboard/backend/tests/test_vision_chat_parser_required_keys.py` — pinning test that `_REQUIRED_DOC_KEYS` does NOT include the new fields.

**Modified existing test files:**

- `dashboard/backend/tests/test_agent_vision.py` — two new cases for `load_vision()`.

**Explicit no-change with rationale:**

- `dashboard/backend/app/services/vision_chat_parser.py` — `_REQUIRED_DOC_KEYS` must stay at 7 strings (Pydantic accepts the missing fields via defaults, so old chats keep working).
- `dashboard/backend/app/routers/vision.py` — passes `VisionDoc` through unchanged.
- `dashboard/frontend/src/components/vision/VisionTab.svelte` — displays markdown source verbatim.
- `dashboard/frontend/src/lib/types.ts` — `VisionRead.body: string` covers both 7-section and 9-section payloads.
- DB schema and migrations — vision body lives in the project's repo, not the DB.
- Manager prompt — the manager review prompt reads run output, not the vision body, so it does not need updating.

## Out of scope (v1)

- Auto-detection of tech stack from `package.json` / `pyproject.toml` / `Cargo.toml` / `go.mod`. Could be a v1.1.
- Canonical vocabulary or chip-style UI for either field. Free-text prose only.
- Surfacing Tech Stack as a separate badge on the Vision tab. Whole body renders as markdown.
- Normalization for downstream programmatic access (e.g. "is this a Python project").
- Migration of existing `docs/vision.md` files. User-driven backfill via the refine flow.

## Open questions

None — all decisions resolved during brainstorming.
