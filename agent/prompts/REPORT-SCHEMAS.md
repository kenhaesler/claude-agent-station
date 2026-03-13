# Report Schemas Reference

Human-readable reference for all agent report/output schemas. Each prompt embeds its own schema inline — this file is the single source of truth to prevent drift. **Not loaded at runtime.**

---

## Employee Report (`employee.md`)

Written to the file path specified in the user prompt.

```json
{
  "status": "success|partial|failure",
  "mode": "full",
  "issue_number": 42,
  "issue_title": "Fix login button",
  "branch": "autonomous/issue-42",
  "base_branch": "<from project CLAUDE.md, default main>",
  "requirements": [
    {"description": "Fix button color", "source": "issue body", "completed": true},
    {"description": "Add hover state", "source": "comment by @user", "completed": true}
  ],
  "files_changed": ["src/login.tsx", "src/login.test.tsx"],
  "commits": ["abc1234"],
  "tests_run": true,
  "tests_passed": true,
  "test_output_summary": "14 tests passed, 0 failed",
  "notes": "Any additional context for the manager"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `status` | `"success"\|"partial"\|"failure"` | yes | |
| `mode` | `"full"\|"plan_only"` | yes | `"full"` for implementation, `"plan_only"` for plan phase |
| `issue_number` | int | yes | |
| `issue_title` | string | yes | |
| `branch` | string | yes | e.g. `autonomous/issue-42` |
| `base_branch` | string | yes | From project CLAUDE.md; default `main` |
| `requirements` | array | yes | Each has `description`, `source`, `completed` |
| `files_changed` | string[] | yes | |
| `commits` | string[] | yes | Short SHAs |
| `tests_run` | bool | yes | |
| `tests_passed` | bool | yes | |
| `test_output_summary` | string | yes | |
| `notes` | string | no | |

---

## Employee Plan (`employee.md` — plan_only mode)

Written to the plan output file path specified in the user prompt (`.claude-employee-plan-{index}.json`).

```json
{
  "issue_number": 42,
  "issue_title": "Fix login button",
  "summary": "2-3 sentence summary of what needs to be done",
  "approach": "High-level description of the technical approach",
  "steps": [
    {
      "order": 1,
      "description": "Read and understand current login component",
      "files": ["src/login.tsx"],
      "type": "analysis"
    },
    {
      "order": 2,
      "description": "Add hover state styling",
      "files": ["src/login.tsx", "src/login.css"],
      "type": "implementation"
    }
  ],
  "files_to_modify": ["src/login.tsx", "src/login.css"],
  "files_to_create": ["src/login.test.tsx"],
  "testing_strategy": "Unit tests for hover state, integration test for form submission",
  "risks": ["May affect existing button styles"],
  "estimated_scope": "small"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `issue_number` | int | yes | |
| `issue_title` | string | yes | |
| `summary` | string | yes | 2-3 sentences |
| `approach` | string | yes | Technical approach description |
| `steps` | array | yes | Each has `order`, `description`, `files`, `type` |
| `steps[].type` | `"analysis"\|"implementation"\|"testing"\|"verification"` | yes | |
| `files_to_modify` | string[] | yes | |
| `files_to_create` | string[] | yes | |
| `testing_strategy` | string | yes | |
| `risks` | string[] | yes | |
| `estimated_scope` | `"small"\|"medium"\|"large"` | yes | |

---

## Manager Plan Verdict (`manager.md` — plan review mode)

Written to the verdict file path provided in the user prompt.

```json
{
  "run_id": "<provided>",
  "timestamp": "<ISO 8601>",
  "plan_verdicts": [
    {
      "project": "owner/repo",
      "verdict": "APPROVE_PLAN|REVISE_PLAN|REJECT_PLAN",
      "employee_index": 0,
      "issue_number": 42,
      "plan_quality_score": 85,
      "feedback": "Specific feedback for the employee",
      "missing_requirements": [],
      "suggested_changes": []
    }
  ]
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `verdict` | `"APPROVE_PLAN"\|"REVISE_PLAN"\|"REJECT_PLAN"` | yes | |
| `employee_index` | int | yes | 0-based |
| `issue_number` | int | yes | |
| `plan_quality_score` | int | yes | 0-100 |
| `feedback` | string | yes | Required for REVISE_PLAN; specific and actionable |
| `missing_requirements` | string[] | yes | Requirements not covered by the plan |
| `suggested_changes` | string[] | yes | Specific changes to make |

---

## Analyst Report (`analyst.md`)

Written to the file path specified in the user prompt.

```json
{
  "status": "success|partial|failure",
  "mode": "analyze",
  "issues_created": [
    {"number": 45, "title": "bug: Fix null check in auth middleware", "type": "bug"},
    {"number": 46, "title": "perf: Add database index for user lookup", "type": "performance"}
  ],
  "issues_refined": [
    {"number": 12, "title": "Original title", "additions": "Added acceptance criteria and file references"}
  ],
  "findings_summary": "Found 3 bugs, 2 performance issues, 1 security concern",
  "files_analyzed_count": 42,
  "notes": "Additional context"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `status` | `"success"\|"partial"\|"failure"` | yes | |
| `mode` | `"analyze"` | yes | Always `"analyze"` |
| `issues_created` | array | yes | Each has `number`, `title`, `type` |
| `issues_refined` | array | yes | Each has `number`, `title`, `additions` |
| `findings_summary` | string | yes | |
| `files_analyzed_count` | int | yes | Number of distinct source files read |
| `notes` | string | no | |

---

## Planner Report (`planner.md`)

Written to the file path specified in the user prompt.

```json
{
  "status": "success|partial|failure",
  "mode": "plan",
  "plans_created": [
    {"issue_number": 42, "title": "Plan: Add login button hover state", "scope": "small"},
    {"issue_number": 45, "title": "Plan: Refactor auth middleware", "scope": "medium"}
  ],
  "issues_analyzed": 5,
  "notes": "Additional context"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `status` | `"success"\|"partial"\|"failure"` | yes | |
| `mode` | `"plan"` | yes | Always `"plan"` |
| `plans_created` | array | yes | Each has `issue_number`, `title`, `scope` |
| `issues_analyzed` | int | yes | |
| `notes` | string | no | |

---

## Manager Verdict (`manager.md`)

Written to the file path provided in the user prompt.

```json
{
  "run_id": "<provided>",
  "timestamp": "<ISO 8601>",
  "verdicts": [
    {
      "project": "owner/repo",
      "verdict": "APPROVE|PR|REJECT|SKIP",
      "mode": "full",
      "issue_number": 42,
      "branch": "autonomous/issue-42",
      "base_branch": "<from employee report>",
      "reasoning": "Brief explanation of your decision",
      "requirements_met": ["req1", "req2"],
      "requirements_missing": [],
      "feedback_to_employee": "What was done well or what needs improvement",
      "push_approved": true
    }
  ],
  "summary": "One paragraph overview of this run's results"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `verdict` | `"APPROVE"\|"PR"\|"REJECT"\|"SKIP"` | yes | |
| `mode` | `"full"\|"analyze"\|"plan"` | yes | Mode from employee report |
| `issue_number` | int\|null | yes | null for analyze/skip |
| `branch` | string\|null | yes | null for analyze/skip |
| `base_branch` | string | yes | From employee report (never hardcode) |
| `reasoning` | string | yes | |
| `requirements_met` | string[] | yes | |
| `requirements_missing` | string[] | yes | |
| `feedback_to_employee` | string | yes | |
| `push_approved` | bool | yes | |

---

## Assigner Output (`assigner.md`)

Returned as raw JSON (no file write — parsed by orchestrator).

```json
{
  "assignments": [
    {
      "employee_index": 0,
      "issue_number": 42,
      "issue_title": "Fix login button",
      "instructions": "Brief specific instructions for this employee"
    }
  ],
  "unassigned_employees": [2]
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `assignments[].employee_index` | int | yes | 0-based |
| `assignments[].issue_number` | int | yes | |
| `assignments[].issue_title` | string | yes | |
| `assignments[].instructions` | string | yes | |
| `unassigned_employees` | int[] | yes | Indices with no work |

No `reasoning` field — contradicts the "ONLY valid JSON, no explanation" rule and wastes tokens.

---

## Decomposer Output (`decomposer.py`)

Returned as raw JSON from Claude CLI.

```json
{
  "tasks": [
    {
      "title": "Short task title",
      "description": "What this employee should do",
      "depends_on": [],
      "expected_files": ["path/to/file1.py"]
    }
  ]
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `tasks[].title` | string | yes | |
| `tasks[].description` | string | yes | |
| `tasks[].depends_on` | int[] | yes | 0-based task indices |
| `tasks[].expected_files` | string[] | yes | Max ~5 files per task |
