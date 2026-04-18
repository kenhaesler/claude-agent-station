<script lang="ts">
  import { portal } from '../../lib/portal';
  import { approve, deny, loadPending, permissionTray } from '../../lib/permission-tray.svelte';
  import { addToast } from '../../lib/toast.svelte';
  import AutonomyBadge from '../badges/AutonomyBadge.svelte';
  import type { PermissionRequest } from '../../lib/api';
  import type { AutonomyLevel } from '../../lib/types';

  let expanded = $state(false);
  let resolvingId = $state<string | null>(null);

  // Hydrate once on mount — after that SSE keeps the list fresh.
  $effect(() => {
    loadPending();
  });

  let pending = $derived(permissionTray.pending);
  let count = $derived(pending.length);

  // Auto-expand whenever a new request arrives so the operator notices.
  let previousCount = 0;
  $effect(() => {
    if (pending.length > previousCount) {
      expanded = true;
    }
    previousCount = pending.length;
  });

  async function handleApprove(req: PermissionRequest) {
    resolvingId = req.request_id;
    try {
      await approve(req.request_id);
      addToast('success', `Approved ${req.tool_name}`);
    } catch (e: any) {
      addToast('error', e?.message ?? 'Approve failed');
    } finally {
      resolvingId = null;
    }
  }

  async function handleDeny(req: PermissionRequest) {
    resolvingId = req.request_id;
    try {
      await deny(req.request_id);
      addToast('success', `Denied ${req.tool_name}`);
    } catch (e: any) {
      addToast('error', e?.message ?? 'Deny failed');
    } finally {
      resolvingId = null;
    }
  }

  function summary(input: Record<string, unknown>): string {
    // Bash → command; Edit/Write → file_path; everything else → first value.
    if (typeof input.command === 'string') return input.command;
    if (typeof input.file_path === 'string') return input.file_path;
    for (const v of Object.values(input)) {
      if (typeof v === 'string') return v;
    }
    try {
      return JSON.stringify(input).slice(0, 120);
    } catch {
      return '';
    }
  }
</script>

{#if count > 0}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    use:portal
    class="fixed bottom-4 right-4 z-tray max-w-md w-full shadow-2xl"
    role="region"
    aria-label="Pending permissions"
  >
    <div class="glass rounded-xl border border-border overflow-hidden">
      <button
        class="w-full flex items-center justify-between px-4 py-2 border-b border-border text-sm font-semibold text-primary"
        onclick={() => (expanded = !expanded)}
      >
        <span>
          {count} pending permission{count === 1 ? '' : 's'}
        </span>
        <span class="text-tertiary text-xs">{expanded ? '▼' : '▲'}</span>
      </button>

      {#if expanded}
        <ul class="max-h-80 overflow-y-auto divide-y divide-border">
          {#each pending as req (req.request_id)}
            <li class="p-3 space-y-2 text-xs">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="badge badge-pending">{req.tool_name}</span>
                <AutonomyBadge level={req.autonomy_level as AutonomyLevel} size="xs" />
                <span class="font-mono text-tertiary truncate">{req.run_id}</span>
              </div>
              {#if req.reason}
                <div class="text-secondary">{req.reason}</div>
              {/if}
              <div class="font-mono text-[11px] text-primary bg-surface-1 rounded-md px-2 py-1 break-all">
                {summary(req.tool_input)}
              </div>
              <div class="flex items-center gap-2">
                <button
                  class="btn btn-primary btn-sm flex-1"
                  disabled={resolvingId === req.request_id}
                  onclick={() => handleApprove(req)}
                >
                  Approve
                </button>
                <button
                  class="btn btn-secondary btn-sm flex-1"
                  disabled={resolvingId === req.request_id}
                  onclick={() => handleDeny(req)}
                >
                  Deny
                </button>
              </div>
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  </div>
{/if}

<style>
  :global(.z-tray) { z-index: 60; }
</style>
