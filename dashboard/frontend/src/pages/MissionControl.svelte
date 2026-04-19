<script lang="ts">
  import { agentPresence } from '../lib/agent-presence.svelte';
  import { messageRun, triggerRun } from '../lib/api';
  import { addToast } from '../lib/toast.svelte';
  import { formatTokens, timeAgo } from '../lib/format';
  import AgentActivityFeed from '../components/agents/AgentActivityFeed.svelte';
  import InterventionBar from '../components/mission-control/InterventionBar.svelte';

  // Prefer the first active run; fall back to the latest run record so the
  // page is still useful between runs (intervention buttons disable themselves
  // when no run is live).
  let currentRunId = $derived(
    agentPresence.activeRuns[0]?.run_id ?? agentPresence.latestRunId ?? ''
  );
  let currentRun = $derived(
    agentPresence.activeRuns.find((r) => r.run_id === currentRunId) ?? null
  );
  let isLive = $derived(agentPresence.activeRuns.length > 0);
  let runPaused = $derived(agentPresence.pausedRuns.has(currentRunId));
  let pendingDecisions = $derived(agentPresence.pendingDecisionCount);

  // Operator message input — goes straight to the agent's next turn.
  let messageText = $state('');
  let sending = $state(false);

  async function handleSend() {
    const text = messageText.trim();
    if (!text || !currentRunId || sending) return;
    sending = true;
    try {
      await messageRun(currentRunId, text);
      addToast('success', 'Message queued for the agent');
      messageText = '';
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Send failed';
      addToast('error', msg);
    } finally {
      sending = false;
    }
  }

  async function handleTrigger() {
    try {
      await triggerRun();
      addToast('success', 'Run triggered');
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Trigger failed';
      addToast('error', msg);
    }
  }

  function handleMessageKey(e: KeyboardEvent) {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSend();
    }
  }
</script>

<div class="flex flex-col h-full min-h-0">
  <!-- Header strip -->
  <div class="flex items-center gap-4 px-4 py-3 border-b border-border bg-surface-1 shrink-0">
    <div class="flex flex-col">
      <div class="text-xs uppercase tracking-wider text-tertiary">Mission Control</div>
      <div class="text-sm font-mono text-primary">
        {currentRunId || 'no active run'}
      </div>
    </div>

    <div class="flex items-center gap-3 ml-6 text-xs">
      <div class="flex items-center gap-1">
        <span class="w-2 h-2 rounded-full {isLive ? 'bg-approve animate-pulse' : 'bg-border'}"></span>
        <span class="text-secondary">{isLive ? 'live' : 'idle'}</span>
      </div>
      {#if currentRun}
        <div class="text-tertiary">·</div>
        <div class="text-secondary">phase: <span class="text-primary">{agentPresence.phase}</span></div>
        <div class="text-tertiary">·</div>
        <div class="text-secondary">
          tokens: <span class="text-primary">{formatTokens(currentRun.tokens_total ?? agentPresence.tokensBurned ?? 0)}</span>
        </div>
        {#if currentRun.started_at}
          <div class="text-tertiary">·</div>
          <div class="text-secondary">started: <span class="text-primary">{timeAgo(currentRun.started_at)}</span></div>
        {/if}
        {#if currentRun.turns != null}
          <div class="text-tertiary">·</div>
          <div class="text-secondary">turns: <span class="text-primary">{currentRun.turns}</span></div>
        {/if}
      {/if}
      {#if runPaused}
        <span class="px-2 py-0.5 rounded bg-accent-yellow/20 text-accent-yellow text-[10px] uppercase">paused</span>
      {/if}
      {#if agentPresence.globalPause}
        <span class="px-2 py-0.5 rounded bg-reject/20 text-reject text-[10px] uppercase">global pause</span>
      {/if}
      {#if pendingDecisions > 0}
        <span class="px-2 py-0.5 rounded bg-accent-blue/20 text-accent-blue text-[10px] uppercase">
          {pendingDecisions} pending decisions
        </span>
      {/if}
    </div>

    <div class="flex-1"></div>

    {#if !isLive}
      <button
        type="button"
        onclick={handleTrigger}
        class="px-3 py-1.5 rounded text-xs font-medium bg-accent-blue/20 text-accent-blue hover:bg-accent-blue/30"
      >
        Trigger run
      </button>
    {/if}
  </div>

  <!-- Intervention bar -->
  <InterventionBar runId={currentRunId} />

  <!-- Main content -->
  <div class="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-0">
    <!-- Activity / thinking feed -->
    <div class="relative flex flex-col min-h-0 border-r border-border">
      <div class="px-3 py-2 border-b border-border text-xs uppercase tracking-wider text-tertiary bg-surface-0">
        Activity — tools, thinking, messages
      </div>
      <div class="flex-1 min-h-0 p-2">
        {#if agentPresence.conversationLog.length === 0 && !isLive}
          <div class="flex items-center justify-center h-full text-tertiary text-sm">
            Idle. Trigger a run to see live agent activity here.
          </div>
        {:else}
          <AgentActivityFeed maxEntries={200} />
        {/if}
      </div>
    </div>

    <!-- Side panel: operator message -->
    <aside class="flex flex-col min-h-0 bg-surface-1">
      <div class="px-3 py-2 border-b border-border text-xs uppercase tracking-wider text-tertiary">
        Send message to agent
      </div>
      <div class="flex flex-col gap-2 p-3">
        <textarea
          bind:value={messageText}
          onkeydown={handleMessageKey}
          placeholder={isLive
            ? 'Type a message — the agent receives it on its next turn. Ctrl/⌘+Enter to send.'
            : 'No active run — messages queue and will be delivered when a run starts.'}
          rows="5"
          class="bg-surface-0 text-primary text-xs px-3 py-2 rounded border border-border
                 focus:border-border-focus outline-none placeholder:text-tertiary font-mono resize-y"
          disabled={sending || !currentRunId}
        ></textarea>
        <div class="flex items-center justify-between">
          <span class="text-[10px] text-tertiary">
            Delivered at the start of the agent's next iteration.
          </span>
          <button
            type="button"
            onclick={handleSend}
            disabled={sending || !messageText.trim() || !currentRunId}
            class="px-3 py-1.5 rounded text-xs font-medium bg-accent-blue/20 text-accent-blue
                   hover:bg-accent-blue/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {sending ? '…' : 'Send'}
          </button>
        </div>
      </div>

      <div class="px-3 py-2 border-t border-border text-xs uppercase tracking-wider text-tertiary mt-2">
        Agents
      </div>
      <div class="flex flex-col gap-1 p-3 text-xs">
        {#each agentPresence.agents as agent}
          <div class="flex items-center gap-2">
            <span class="w-2 h-2 rounded-full" style="background: {agent.color}"></span>
            <span class="text-primary">{agent.name}</span>
            <span class="text-tertiary">·</span>
            <span class="text-secondary">{agent.status}</span>
          </div>
        {:else}
          <span class="text-tertiary italic">No active agents</span>
        {/each}
      </div>
    </aside>
  </div>
</div>
