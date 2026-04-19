<script lang="ts">
  import { messageRun, stopRun } from '../../lib/api';
  import { toastSuccess, toastError } from '../../lib/toast.svelte';

  // employeeIndex accepted for backwards compat but unused — Mission Control
  // operates at the run level (the lead agent dispatches to teammates).
  let { runId = '' }: { runId: string; employeeIndex?: number } = $props();

  let message = $state('');
  let sending = $state(false);
  let guidanceType = $state<'info' | 'warning' | 'redirect' | 'stop'>('info');

  const types = [
    { value: 'info' as const, label: 'Info' },
    { value: 'redirect' as const, label: 'Redirect' },
    { value: 'warning' as const, label: 'Warning' },
    { value: 'stop' as const, label: 'Stop' },
  ];

  async function send() {
    if (!message.trim() && guidanceType !== 'stop') return;
    if (!runId) return;
    sending = true;
    try {
      if (guidanceType === 'stop') {
        await stopRun(runId);
        toastSuccess('Stop requested');
      } else {
        const prefix = `[operator-${guidanceType}] `;
        await messageRun(runId, prefix + message.trim());
        toastSuccess('Message queued');
      }
      message = '';
    } catch (e: any) {
      toastError(`Failed: ${e.message}`);
    } finally {
      sending = false;
    }
  }
</script>

<div class="flex items-center gap-2 p-2 border-t border-border bg-surface-0/60">
  <select
    bind:value={guidanceType}
    class="bg-surface-0 text-secondary text-xs px-2 py-1.5 rounded border border-border focus:border-border-focus outline-none"
  >
    {#each types as t}
      <option value={t.value}>{t.label}</option>
    {/each}
  </select>
  <input
    bind:value={message}
    type="text"
    placeholder="Send guidance to agent..."
    class="flex-1 bg-surface-0 text-primary text-xs px-3 py-1.5 rounded border border-border
           focus:border-border-focus outline-none placeholder:text-tertiary"
    onkeydown={(e) => e.key === 'Enter' && send()}
    disabled={sending}
  />
  <button
    onclick={send}
    disabled={sending || !message.trim()}
    class="px-3 py-1.5 rounded text-xs font-medium bg-accent-blue/20 text-accent-blue
           hover:bg-accent-blue/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
  >
    {sending ? '...' : 'Send'}
  </button>
</div>
