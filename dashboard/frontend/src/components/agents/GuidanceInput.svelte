<script lang="ts">
  import { sendGuidance } from '../../lib/api';
  import { toastSuccess, toastError } from '../../lib/toast.svelte';

  let {
    runId = '',
    employeeIndex = 0,
  }: {
    runId: string;
    employeeIndex?: number;
  } = $props();

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
    if (!message.trim() || !runId) return;
    sending = true;
    try {
      await sendGuidance({
        run_id: runId,
        employee_index: employeeIndex,
        guidance_type: guidanceType,
        content: message.trim(),
      });
      toastSuccess('Guidance sent');
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
