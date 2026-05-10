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
- The render layer skips empty sections, so old visions still render as 7-section markdown.

No DB schema change. No migration. `vision_cached_body` on the `Project` table continues to hold whatever the project's repo currently has; when the user refines a vision and adds the new sections, the body is rewritten end-to-end on the next commit.

## Render

`dashboard/backend/app/services/vision_render.py` iterates an ordered list of `(field_name, heading_text)` pairs to emit `## Heading\n\nbody\n\n` blocks. Two changes:

1. Insert the two new pairs at positions 4 and 5:
   ```python
   ("tech_stack", "Tech Stack"),
   ("runtime_target", "Runtime Target"),
   ```
2. Skip a section when its body is empty (`body.strip() == ""`): omit the heading entirely rather than emitting an orphan `## Tech Stack` followed by blank lines.

The skip-empty rule is the load-bearing piece for backward compatibility. Without it, every old vision suddenly grows two heading lines with no content, which looks like a regression in the Vision tab and confuses the lead/teammate prompts that read the body verbatim.

## Chat prompts

Two markdown files extend their section list. Both are pure text edits.

### `agent/prompts/vision_create.md`

The structured-interview phase currently enumerates the seven sections. Replace with the nine-section list in the order above, with one-line descriptions for the two new entries:

- **Tech Stack** — the languages, frameworks, and key libraries
- **Runtime Target** — where the application is intended to run (Linux host, container, serverless, edge, embedded)

Update "seven sections" → "nine sections" everywhere in the prose. Update the `valid section names` enumeration: `problem`, `users`, `end_state`, `tech_stack`, `runtime_target`, `non_goals`, `principles`, `horizons`, `anti_patterns`. Update the `vision-doc` example block so it shows all 9 keys.

### `agent/prompts/vision_refine.md`

Same list extension. The refine prompt handles "I want to update X" / "I want to add Y" requests. With the two new section names in its list, asking "add the tech stack" lands in the right code path automatically — no new branching or special handling.

## Parser

`dashboard/backend/app/services/vision_chat_parser.py` extracts the `vision-doc` fenced JSON block from chat output and validates against `VisionDoc`. Since the new fields are optional with defaults, the parser needs no code change. The structural-completeness check remains "Pydantic validation succeeded" — Pydantic decides what counts as valid.

## Lead and teammate consumption

Existing orchestrator and teammate prompts read the rendered vision body as prose. They already pick up Problem, Users, etc. as context. The new sections appear in that same body and flow through to the same consumers automatically. No prompt edits needed downstream.

If a future change wants programmatic access to Tech Stack (e.g. to choose a base Docker image), we can add a normalizer in v1.1 without disturbing this design. v1 stays prose-only.

## Frontend

`dashboard/frontend/src/components/vision/VisionTab.svelte:184` renders `vision.body` as raw markdown inside a `<pre>` block. New sections appear automatically. No component changes.

No `VisionRead` type-shape change either — `body: string` already covers both 7-section and 9-section payloads. No frontend types update.

## Backward compatibility

- Old `docs/vision.md` files with seven sections render unchanged in the Vision tab.
- Old `vision_cached_body` rows in the DB are valid input for the renderer and for orchestrator consumption — both treat the body as prose.
- Old `vision-doc` JSON blocks (e.g. if a chat is resumed from a stale cache) validate successfully against the extended `VisionDoc`, with the two new fields defaulting to `""`.
- Old visions get the new sections only when the user explicitly refines them. There is no auto-migration, no banner prompt, no opt-in flow.

## Testing

Three pytest cases in `dashboard/backend/tests/` (file located during implementation — either an existing `test_vision_render.py` or a new module):

1. **`test_vision_doc_optional_fields`** — `VisionDoc.model_validate(payload_without_new_fields)` succeeds; both new fields default to `""`; round-trip through `model_dump()` includes the empty values.
2. **`test_vision_render_emits_new_sections_in_order`** — given a `VisionDoc` with all 9 fields populated, the rendered markdown contains `## Tech Stack` between `## End-state` and `## Non-goals`, and `## Runtime Target` between `## Tech Stack` and `## Non-goals`. Literal-order assertion.
3. **`test_vision_render_skips_empty_new_sections`** — given a `VisionDoc` with `tech_stack=""` and `runtime_target=""`, the rendered markdown does NOT contain `## Tech Stack` or `## Runtime Target`; the seven other sections render unchanged. Regression test that locks the backward-compatibility behaviour.

No frontend tests (raw-markdown render covers this). No prompt-contract tests (`test_prompt_contracts.py:_NON_ROLE_PROMPTS` already excludes vision-chat prompts; the prompt edits don't change structural shape).

## File and component summary

**Modified files:**

- `dashboard/backend/app/schemas.py` — `VisionDoc` gains two fields.
- `dashboard/backend/app/services/vision_render.py` — two new render pairs at positions 4 and 5; empty-section skip rule.
- `dashboard/backend/tests/test_vision_render.py` (or equivalent) — 3 new test cases.
- `agent/prompts/vision_create.md` — section list extended from 7 to 9 in three places (prose, valid-names list, example block).
- `agent/prompts/vision_refine.md` — section list extended from 7 to 9.

**No changes:**

- `dashboard/backend/app/services/vision_chat_parser.py` — picks up the schema change automatically.
- `dashboard/backend/app/routers/vision.py` — passes `VisionDoc` through, unchanged.
- `dashboard/frontend/src/components/vision/VisionTab.svelte` — renders raw markdown.
- `dashboard/frontend/src/lib/types.ts` — only knows `VisionRead.body: string`.
- DB schema and migrations — no storage change.
- Lead / teammate / manager prompts — consume the rendered vision body verbatim, get new context automatically.

## Out of scope (v1)

- Auto-detection of tech stack from `package.json` / `pyproject.toml` / `Cargo.toml` / `go.mod`. Could be a v1.1.
- Canonical vocabulary or chip-style UI for either field. Free-text prose only.
- Surfacing Tech Stack as a separate badge on the Vision tab. Whole body renders as markdown.
- Normalization for downstream programmatic access (e.g. "is this a Python project").
- Migration of existing `docs/vision.md` files. User-driven backfill via the refine flow.

## Open questions

None — all decisions resolved during brainstorming.
