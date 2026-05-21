# Vision Reference Files — Design Spec

## Context

The Vision feature ([spec](2026-05-07-project-vision-design.md)) lets users co-author a `docs/vision.md` for a project through a guided chat with Claude. Today the chat is text-only: the user describes the project in words, and Claude assembles the seven-section vision document.

Many real projects are anchored to external artefacts — a pricing spreadsheet, a brand PDF, a CSV of seed data, a screenshot of a competing UI. Users currently have no way to ground the vision conversation in those artefacts; they have to translate them into prose. That loses fidelity and creates extra work.

This spec adds the ability to attach reference files to the vision chat, persist them with the project's repo, and make them available to downstream agents at implementation time.

## Goals

1. Let users upload reference files during the vision chat (paperclip / drag-drop), with PDFs and images grounded natively by Claude and Excel / CSV / docx grounded via server-side text extraction.
2. Persist uploaded files to the target project's repository under `docs/vision-refs/` on commit, so they ship with the vision and are available to teammate worktrees automatically via `git clone`.
3. Surface the reference files in the rendered `docs/vision.md` as a `## References` section so humans and the vision-analyst can see what backs the document.
4. Keep the rest of the vision flow unchanged: same chat session model, same SSE turn streaming, same Approve & commit terminal action.

## Non-goals

- A standalone reference-management UI separate from the vision chat. To add or remove a reference after commit, the user starts a new vision chat session.
- AI-generated descriptions of each reference at render time. The `## References` section lists filenames and sizes only; no second model call at commit.
- Deeper analyst behaviour on references in v1 (e.g., the vision-analyst parsing an xlsx to derive column-level proposals). The analyst sees filenames and notes them; deeper parsing is the teammates' job at implementation time.
- The Anthropic Code Execution tool / sandbox. Excel parsing is done server-side with `openpyxl`. (Re-evaluate if users hit limits with formulas / charts.)
- File-type negotiation beyond the v1 allowlist (no audio, no video, no archives).
- Per-reference access control. Anyone with access to the project's repo gets the references.

## File-type matrix

| Type | MIME | Sent to Claude as | Persisted to repo |
|---|---|---|---|
| PDF | `application/pdf` | `document` block (native) | yes — binary |
| PNG | `image/png` | `image` block (native) | yes — binary |
| JPEG | `image/jpeg`, `image/jpg` | `image` block (native) | yes — binary |
| GIF | `image/gif` | `image` block (native) | yes — binary |
| WebP | `image/webp` | `image` block (native) | yes — binary |
| Plain text | `text/plain` | `document` block (native) | yes — utf-8 |
| Markdown | `text/markdown` | text block, raw | yes — utf-8 |
| CSV | `text/csv` | text block, raw | yes — utf-8 |
| Excel | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` (xlsx) | text block, extracted via `openpyxl` as markdown tables | yes — binary |
| Word | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | text block, extracted via `python-docx` | yes — binary |

MIME is sniffed server-side with `python-magic` (not trusted from the client). If the sniffed MIME isn't in this table, the upload is rejected.

## Size limits

- **Per file:** 10 MB at upload time (HTTP 413 if exceeded).
- **Per session total:** 40 MB across all attachments in the session (sum of `size_bytes`). Enforced at upload time; uploads are only accepted against `active` sessions.
- **Per extraction:** 200 KB of text post-extraction. If the extraction yields more, truncate with a `\n\n[truncated — N more bytes]` marker. The original binary is still committed in full; only the in-context text excerpt is truncated.

## User-visible behaviour

The Vision chat (`dashboard/frontend/src/components/vision/VisionChat.svelte`) gains:

- A paperclip button next to **Send**, opening a multi-file picker filtered to the allowlist extensions.
- A dropzone covering the transcript card (`ondragover` / `ondrop` handlers).
- A chip strip above the input listing each uploaded-but-not-yet-sent attachment as `📎 filename.xlsx · 12 KB · ×`. The `×` deletes the attachment (only allowed before send).
- During upload, the chip shows a spinner; on failure, an error chip with retry.
- **Send** is disabled while any upload is in flight. On send, the chip strip clears once the SSE turn starts.
- Past messages in the transcript show their attachments as read-only chips beneath the bubble. Session resume rehydrates these chips from the server.

Validation toasts:
- Oversize file → "`filename` is 12 MB — max 10 MB per file".
- Oversize session → "Adding this would exceed the 40 MB session limit".
- Unsupported type → "`filename` (`mime/type`) isn't a supported reference type. Supported: PDF, images, Excel, CSV, Word, txt, md".

On **Approve & commit**, the vision commit flow writes `docs/vision.md` and each attachment to `docs/vision-refs/<filename>` in the same target branch. A success toast names how many references were committed. If any reference upload fails, the toast lists the failures and a "Retry uploads" button stays in the UI.

## Architecture

### New table — `vision_chat_attachments`

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PRIMARY KEY | uuid4 string |
| `session_id` | TEXT NOT NULL | FK → `vision_chat_sessions.id`; index |
| `filename` | TEXT NOT NULL | sanitised, collision-suffixed at upload |
| `mime_type` | TEXT NOT NULL | sniffed |
| `size_bytes` | INTEGER NOT NULL | |
| `disk_path` | TEXT NOT NULL | absolute path under `VISION_UPLOAD_DIR` |
| `extracted_text` | TEXT | nullable; non-null only for non-native types |
| `sent_at` | TIMESTAMP | nullable; set when first included in a chat turn — DELETE refused once non-null |
| `created_at` | TIMESTAMP NOT NULL | default now() |

`vision_chat_sessions.messages` JSON shape gains an optional per-message `attachments` array:

```json
{"role": "user", "content": "...", "attachments": [
  {"id": "...", "filename": "...", "mime_type": "...", "size_bytes": 1234}
]}
```

Existing rows have no `attachments` key — forward-compatible, no backfill.

### Storage layout

```
/var/lib/claude-agent-station/vision-chat-uploads/
  <session_id>/
    <uuid>-<sanitized-filename>
```

Configurable via env `VISION_UPLOAD_DIR` (default as above). The `<uuid>-` prefix guarantees uniqueness on disk independent of the user-visible filename (which can be collision-suffixed).

### New / changed endpoints

All under the existing `/api/projects/{project_id}/vision` router, same auth as the rest of the router.

**`POST /api/projects/{project_id}/vision/chat/attachments`** — multipart upload.
- Body: `file` (single file per request; the frontend issues N parallel requests for N files).
- Validates: MIME (via `python-magic`), per-file size, per-session total size.
- Sanitises filename: strips `<>:"|?*\/`, leading `.`, path traversal; if collision within the session, suffixes `-2`, `-3`, …
- Stores to disk; for non-native types, runs extraction synchronously (the request blocks until extraction completes — bounded by the 10 MB cap, this is acceptable).
- Creates a session lazily if none exists for the project (mirrors `chat_turn`).
- Returns `{id, filename, mime_type, size_bytes}`.

**`DELETE /api/projects/{project_id}/vision/chat/attachments/{attachment_id}`**
- 409 if `sent_at IS NOT NULL`.
- 404 if the attachment belongs to a different session or different project.
- On success: deletes the disk file and the row.

**`POST /api/projects/{project_id}/vision/chat`** (existing) — body adds optional `attachment_ids: list[str]`.
- The handler validates that all IDs belong to the resolved session and have `sent_at IS NULL`.
- The SDK call assembles a multi-block user message:
  - Text block: the user's typed message.
  - For each attachment, in upload order:
    - PDF → `document` block with base64 source.
    - Image (jpeg/png/gif/webp) → `image` block with base64 source.
    - Plain text → `document` block with `text/plain` source.
    - Other → text block prefixed with `--- Attached file: <filename> (<mime>) ---\n` and the (possibly-truncated) extracted text.
- Marks the attachments `sent_at = now()` once the turn starts.

**`POST /api/projects/{project_id}/vision`** (commit, existing) — after writing `docs/vision.md`:
1. For each attachment in the session where `sent_at IS NOT NULL` (i.e., actually included in at least one chat turn — uploaded-but-never-sent attachments are not committed), read from disk and PUT to `docs/vision-refs/<filename>` in the project's branch via `github_contents.write_file`.
2. Collisions in the repo: if the existing SHA doesn't match what we wrote, suffix `-2`, `-3` and retry once.
3. The rendered `docs/vision.md` includes a `## References` section listing each committed file with a relative link and size.
4. If `docs/vision.md` writes but one or more reference uploads fail, return HTTP 207-style payload: `{vision_sha, html_url, refs_committed: [...], refs_failed: [{filename, error}]}`. Disk files for failed refs are retained until success or session cancel.
5. On full success, delete the upload dir for the session.

**`DELETE /api/projects/{project_id}/vision/chat`** (cancel, existing) — also `rm -rf` the upload dir for the session.

### `github_contents.write_file` extension

Currently text-only. Extend to accept either `body: str` (existing) or `body_bytes: bytes` (new, base64-encoded for the GitHub Contents API). Existing callers unaffected.

### Frontend

**`dashboard/frontend/src/lib/types.ts`** — add:

```ts
export type Attachment = {
  id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
};
```

Extend `VisionMessage` with `attachments?: Attachment[]`.

**`dashboard/frontend/src/lib/api.ts`** — add:

```ts
export async function uploadVisionAttachment(projectId: number, file: File): Promise<Attachment>;
export async function deleteVisionAttachment(projectId: number, attachmentId: string): Promise<void>;
```

Extend the SSE chat-turn payload to include `attachment_ids?: string[]`.

**`dashboard/frontend/src/components/vision/VisionChat.svelte`** — new state, UI elements, and handlers per the user-visible behaviour above. Test IDs: `vision-chat-attach-btn`, `vision-chat-attachment-chip`, `vision-chat-dropzone`.

### Vision document rendering

`render_vision_doc` (in `dashboard/backend/app/services/vision_render.py`) gains an optional `references: list[{filename, size_bytes}]` argument. When provided and non-empty, appends:

```markdown
## References

Reference files for this vision are in [`vision-refs/`](vision-refs/):

- [`pricing-model.xlsx`](vision-refs/pricing-model.xlsx) — 12 KB
- [`brand-guide.pdf`](vision-refs/brand-guide.pdf) — 480 KB
```

When the list is empty, the section is omitted entirely (no header).

The commit handler passes the session's attachments into `render_vision_doc` before writing.

### Downstream agents

- **Teammates (backend / frontend / qa)** — no workspace-setup changes; `git clone` already brings `docs/vision-refs/` into each worktree. One-line addition to `agent/prompts/employee.md` (or relevant role briefs): *"The project may have reference files under `docs/vision-refs/`. Read them when relevant to the task (csv/xlsx may be tabular data the issue depends on)."*
- **Vision analyst (`agent/vision_analyst.py`)** — enumerate `docs/vision-refs/` (filenames + sizes only, no extraction) and inject as a `## Reference files` block in the analyst's prompt. The analyst then knows what's available when proposing issues; deeper inspection is left to the teammates that pick up those issues.

### Lifecycle & cleanup

- **Pre-send DELETE** — explicit user action, sync.
- **Session cancel** — `DELETE .../vision/chat` removes disk files.
- **Successful commit** — disk files removed after all refs uploaded.
- **Partial commit failure** — failed-ref disk files retained; succeed-on-retry or are removed on cancel.
- **Orphan cleanup** — startup task scans `VISION_UPLOAD_DIR` and removes directories whose `session_id` has no corresponding row, or whose corresponding session is `cancelled` / `approved` and older than 24h.

## Migration

One Alembic migration:
- Create `vision_chat_attachments` table.
- Index on `(session_id)`.

No backfill: existing sessions have no attachments; `vision_chat_sessions.messages` JSON shape is forward-compatible.

New env var documented in `docs/configuration.md`:
- `VISION_UPLOAD_DIR` (default `/var/lib/claude-agent-station/vision-chat-uploads`).

New dependencies in `dashboard/backend/requirements.txt`:
- `openpyxl` (xlsx extraction)
- `python-docx` (docx extraction)
- `python-magic` (MIME sniffing; requires system `libmagic` — note in deploy docs)

Docs to update per CLAUDE.md sync rule:
- `docs/configuration.md` — env var, new table, new dependencies.
- `docs/architecture.md` — short Vision-section note about attachment storage path and lifecycle.

## Testing

**Backend unit:**
- Extraction roundtrip per non-native MIME: xlsx → markdown table (single sheet, multi-sheet, empty cells, formulas → values); csv → text; docx → text; empty / malformed inputs.
- Filename sanitisation: Anthropic's forbidden chars, leading dots, traversal `../`, unicode 0-31, name length 1-255.
- Size cap enforcement at upload (per-file and per-session), with response code 413.
- Extracted-text truncation at 200 KB.
- DELETE while `sent_at IS NULL` → 204; after → 409.
- Commit endpoint: vision.md + each ref file written; collision suffixing in repo; partial-failure 207 payload shape.

**Backend integration:**
- Full chat-turn flow with one PDF + one xlsx + one image, asserting the SDK request body has the right block types and base64 sources.
- Session cancel removes the upload directory.
- Orphan cleanup task removes directories for non-existent / stale sessions.

**Frontend unit (Vitest):**
- Upload happy path (single + multiple).
- Drag-drop dispatches the same handler.
- Chip removal during pending state.
- Oversize and unsupported-MIME rejection toasts.
- Send disabled while uploading.
- Session resume rehydrates attachment chips on past messages.

**No Playwright e2e for v1.** The existing vision flow has no e2e coverage; this feature isn't the place to introduce it. If a regression motivates one later, the test IDs are already in place.

## Open questions

None blocking. Two items to revisit after release:
1. If users frequently hit the 200 KB extracted-text cap on xlsx, consider opt-in code-execution-tool deep mode.
2. If the vision-analyst would benefit from extracted text (not just filenames), pipe `extracted_text` from `vision_chat_attachments` into its prompt context.
