/**
 * Permission Tray store — ADR-0001, P2.T10.
 *
 * Lifecycle:
 *  - On app boot, call `loadPending()` once to hydrate the queue.
 *  - The central SSE stream in `agent-presence` forwards `permission_request`
 *    and `permission_resolved` events here via `handleStreamEvent`.
 *  - UI components read `permissionTray.pending` and call `approve`/`deny`
 *    to act on a request.
 */

import type { PermissionRequest } from './api';
import { listPermissionRequests, resolvePermissionRequest } from './api';

interface TrayState {
  pending: PermissionRequest[];
  loading: boolean;
  error: string | null;
}

export const permissionTray = $state<TrayState>({
  pending: [],
  loading: false,
  error: null,
});

export async function loadPending(): Promise<void> {
  permissionTray.loading = true;
  try {
    const rows = await listPermissionRequests({ status: 'pending', limit: 50 });
    permissionTray.pending = rows;
    permissionTray.error = null;
  } catch (e: any) {
    permissionTray.error = e?.message ?? 'failed to load permissions';
  } finally {
    permissionTray.loading = false;
  }
}

function upsertPending(req: PermissionRequest): void {
  const idx = permissionTray.pending.findIndex(r => r.request_id === req.request_id);
  if (idx >= 0) {
    permissionTray.pending[idx] = req;
  } else {
    // Newest at the bottom — operator works through the queue top-down.
    permissionTray.pending.push(req);
  }
}

function removePending(requestId: string): void {
  const idx = permissionTray.pending.findIndex(r => r.request_id === requestId);
  if (idx >= 0) permissionTray.pending.splice(idx, 1);
}

/** Process a single SSE event. Called by agent-presence for every tick. */
export function handleStreamEvent(event: Record<string, unknown>): void {
  const type = event.type as string | undefined;
  if (type === 'permission_request') {
    const data = (event.data ?? event) as Partial<PermissionRequest>;
    if (data.request_id) {
      // Synthesize a minimal PermissionRequest row from the event payload.
      const row: PermissionRequest = {
        id: 0,
        request_id: String(data.request_id),
        run_id: String(data.run_id ?? ''),
        agent_id: String(data.agent_id ?? ''),
        tool_name: String(data.tool_name ?? ''),
        tool_input: (data.tool_input as Record<string, unknown>) ?? {},
        autonomy_level: String(data.autonomy_level ?? 'assisted'),
        reason: (data.reason ?? null) as string | null,
        status: 'pending',
        resolution_note: null,
        created_at: null,
        resolved_at: null,
      };
      upsertPending(row);
    }
    return;
  }
  if (type === 'permission_resolved') {
    const data = (event.data ?? event) as { request_id?: string };
    if (data.request_id) removePending(String(data.request_id));
    return;
  }
}

export async function approve(requestId: string, note?: string): Promise<void> {
  await resolvePermissionRequest(requestId, 'approve', note);
  removePending(requestId);
}

export async function deny(requestId: string, note?: string): Promise<void> {
  await resolvePermissionRequest(requestId, 'deny', note);
  removePending(requestId);
}

export function pendingCount(): number {
  return permissionTray.pending.length;
}
