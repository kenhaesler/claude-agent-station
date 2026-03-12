<script lang="ts">
  import { agentPresence } from '../lib/agent-presence.svelte';
  import { sendGuidance } from '../lib/api';
  import { toastError } from '../lib/toast.svelte';

  let message = $state('');
  let guidanceType = $state<'info' | 'warning' | 'redirect' | 'stop'>('info');
  let sending = $state(false);
  let showTypeMenu = $state(false);

  let hasActiveRun = $derived(agentPresence.activeRuns.length > 0);

  async function send() {
    if (!message.trim() || !hasActiveRun) return;
    sending = true;
    try {
      const activeRun = agentPresence.activeRuns[0];
      await sendGuidance({
        run_id: activeRun.run_id,
        employee_index: 0,
        guidance_type: guidanceType,
        content: message.trim(),
      });
      // Add to conversation log locally
      agentPresence.conversationLog.push({
        id: Date.now(),
        timestamp: Date.now(),
        agentName: 'You',
        agentColor: '#9ca3af',
        type: 'guidance',
        content: message.trim(),
      });
      message = '';
      guidanceType = 'info';
    } catch (e: any) {
      toastError(`Failed to send guidance: ${e.message}`);
    } finally {
      sending = false;
    }
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  const typeLabels: Record<string, { label: string; color: string }> = {
    info: { label: 'Info', color: '#3b82f6' },
    warning: { label: 'Warn', color: '#f59e0b' },
    redirect: { label: 'Redirect', color: '#a855f7' },
    stop: { label: 'Stop', color: '#ef4444' },
  };
</script>

<div class="flex items-center gap-2 p-2 border-t border-border bg-surface">
  <!-- Type selector -->
  <div class="relative">
    <button
      onclick={() => showTypeMenu = !showTypeMenu}
      class="px-1.5 py-1 text-[10px] font-data rounded border border-border-subtle hover:border-border cursor-pointer"
      style="color: {typeLabels[guidanceType].color}"
      title="Guidance type"
    >
      {typeLabels[guidanceType].label}
    </button>
    {#if showTypeMenu}
      <div class="absolute bottom-full left-0 mb-1 glass rounded-md overflow-hidden z-10">
        {#each Object.entries(typeLabels) as [type, meta]}
          <button
            onclick={() => { guidanceType = type as any; showTypeMenu = false; }}
            class="block w-full px-3 py-1.5 text-xs text-left hover:bg-white/5 cursor-pointer"
            style="color: {meta.color}"
          >
            {meta.label}
          </button>
        {/each}
      </div>
    {/if}
  </div>

  <!-- Input -->
  <input
    type="text"
    bind:value={message}
    onkeydown={handleKeydown}
    placeholder={hasActiveRun ? 'Type guidance...' : 'No active run'}
    disabled={!hasActiveRun || sending}
    class="flex-1 bg-transparent text-sm text-text placeholder-text-muted outline-none disabled:opacity-40"
  />

  <!-- Send button -->
  <button
    onclick={send}
    disabled={!message.trim() || !hasActiveRun || sending}
    class="px-2 py-1 text-xs font-medium rounded bg-info text-white hover:opacity-90 transition-opacity cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
  >
    {sending ? '...' : 'Send'}
  </button>
</div>
