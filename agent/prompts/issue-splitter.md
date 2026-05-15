# Issue Splitter — Prompt

## Inputs

You will receive:

- The **parent issue body** (Markdown), including its title, labels, and acceptance criteria.
- A **repo summary** (file tree depth-3, recent commits, README excerpt).
- The current **project vision** (if present in `docs/vision.md`).
- A **budget hint**: target each sub-run to complete in **4-10 minutes** of agent wall-clock.

## Task

Decide if the parent issue is decomposable into 2-5 atomic sub-issues
that can be implemented independently by a single specialist team.

If the parent is already small (about 30 minutes or less of expected
work, single acceptance criterion, scoped to one subsystem), **do not
split** — emit an empty array `[]`. The harness treats this as "run
as-is".

If the parent is decomposable, emit a JSON array of 2-5 sub-issue
proposals. **No prose around the JSON** — your entire output must be
the JSON array, parseable by `json.loads`.

## Constraints

- Each sub-issue must be **implementable end-to-end by a single
  specialist team in 15 minutes or less of agent time**.
- Each sub-issue must have **testable acceptance criteria** — no vague
  "make it work" criteria.
- Sub-issue **bodies must be self-contained** — a downstream agent
  reading only the sub-issue body must have enough context to implement
  it. Inline the relevant excerpt from the parent.
- **`depends_on`** is the index (zero-based) of a prerequisite sibling
  in this same array, or `null` if independent. Use sparingly — only
  when sub-issue B actually requires sub-issue A's branch to be merged
  before B can compile.
- **Never** propose splitting a sub-issue further (no recursive splits
  — this is single-level).
- **Never** propose sub-issues whose union is larger than the parent.
  Decomposition, not amplification.

## Output

A JSON array of objects:

```json
[
  {
    "title": "Add /api/auth/login endpoint",
    "body": "Full self-contained body. Inline parent context as needed.",
    "labels": ["backend", "auth"],
    "acceptance": [
      "POST /api/auth/login with valid credentials returns 200 + JWT.",
      "POST /api/auth/login with invalid credentials returns 401."
    ],
    "depends_on": null
  },
  {
    "title": "Add /api/me endpoint",
    "body": "...",
    "labels": ["backend", "auth"],
    "acceptance": ["GET /api/me with valid JWT returns the current user."],
    "depends_on": 0
  }
]
```

If you decide **not** to split, emit `[]`.

Output **only** the JSON array — no explanation, no commentary, no
Markdown fence around it.
