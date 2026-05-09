<script lang="ts">
  import { agentPresence } from '../lib/agent-presence.svelte';
  import { messageRun, triggerRun, operatorApproveRunPlan, operatorRejectRunPlan } from '../lib/api';
  import { addToast } from '../lib/toast.svelte';
  import { formatTokens, timeAgo } from '../lib/format';
  import AgentActivityFeed from '../components/agents/AgentActivityFeed.svelte';
  import InterventionBar from '../components/mission-control/InterventionBar.svelte';

  // Mission Control targets the first active run. We deliberately do NOT
  // fall back to `latestRunId` — that fallback routed operator messages to
  // already-finished runs where the orchestrator had exited, so the message
  // row was queued forever and the agent never saw it. The header still
  // shows latestRunId for context, but interventions require a live run.
  let currentRunId = $derived(agentPresence.activeRuns[0]?.run_id ?? '');
  let headerRunId = $derived(
    currentRunId || agentPresence.latestRunId || ''
  );
  let currentRun = $derived(
    agentPresence.activeRuns.find((r) => r.run_id === currentRunId) ?? null
  );
  let isLive = $derived(agentPresence.activeRuns.length > 0);
  let runPaused = $derived(!!agentPresence.pausedRuns[currentRunId]);
  let pendingDecisions = $derived(agentPresence.pendingDecisionCount);
  // Issue #266: surface the plan-review gate so operators can see when a
  // plan_only run is waiting on the manager (or has been approved/rejected).
  let planReviewStatus = $derived<string | null>(
    currentRun && (
      currentRun.status === 'awaiting_plan_review' ||
      currentRun.status === 'plan_approved' ||
      currentRun.status === 'plan_rejected' ||
      currentRun.status === 'plan_reviewing'
    )
      ? (currentRun.status as string)
      : null
  );

  // Plan-review gate operator override (issue #266 follow-up).
  // ``planActionInFlight`` doubles as a busy flag and a label hint for
  // the button rendering 'Approving…' / 'Rejecting…'.
  let planActionInFlight = $state<'approve' | 'reject' | null>(null);

  async function approveCurrentRunPlan() {
    if (!currentRunId || planActionInFlight) return;
    planActionInFlight = 'approve';
    try {
      const result = await operatorApproveRunPlan(currentRunId);
      const n = result.enqueued.length;
      addToast(
        'success',
        n > 0
          ? `Plan approved — ${n} follow-up run${n === 1 ? '' : 's'} enqueued`
          : 'Plan approved (no follow-up enqueued — verdicts file missing)',
      );
    } catch {
      // requestWithToast already surfaced the error toast.
    } finally {
      planActionInFlight = null;
    }
  }

  async function rejectCurrentRunPlan() {
    if (!currentRunId || planActionInFlight) return;
    planActionInFlight = 'reject';
    try {
      await operatorRejectRunPlan(currentRunId);
      addToast('success', 'Plan rejected');
    } catch {
      // toast already shown
    } finally {
      planActionInFlight = null;
    }
  }

  // Operator message input — goes straight to the agent's next turn.
  let messageText = $state('');
  let sending = $state(false);

  async function handleSend() {
    const text = messageText.trim();
    if (!text || sending) return;
    if (!currentRunId) {
      addToast('error', 'No live run — trigger the agent first, then send.');
      return;
    }
    sending = true;
    try {
      await messageRun(currentRunId, text);
      addToast('success', 'Message queued for the agent');
      messageText = '';
    } catch (e) {
      // Backend returns 409 with a clear detail when the run has finished
      // between the UI's activeRuns refresh and the click. Surface that
      // exact string so the operator knows their message was NOT delivered.
      const raw = e instanceof Error ? e.message : 'Send failed';
      const friendly = raw.startsWith('409:')
        ? raw.slice(4).trim() || 'Run is no longer active — message not delivered'
        : raw;
      addToast('error', friendly);
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
      <div class="text-xs uppercase tracking-wider text-secondary font-semibold">Mission Control</div>
      <div class="text-sm font-mono text-primary" data-testid="mc-run-id">
        {#if currentRunId}
          {currentRunId}
        {:else if headerRunId}
          <span class="text-secondary">{headerRunId}</span>
          <span class="ml-1 text-[10px] uppercase text-accent-yellow">(ended — read-only)</span>
        {:else}
          no active run
        {/if}
      </div>
    </div>

    <div class="flex items-center gap-3 ml-6 text-xs">
      <div class="flex items-center gap-1">
        <span class="w-2 h-2 rounded-full {isLive ? 'bg-approve animate-pulse' : 'bg-border'}"></span>
        <span class="text-primary font-medium">{isLive ? 'live' : 'idle'}</span>
      </div>
      {#if currentRun}
        <div class="text-secondary">·</div>
        <div class="text-secondary">phase: <span class="text-primary font-medium">{agentPresence.phase}</span></div>
        <div class="text-secondary">·</div>
        <div class="text-secondary">
          tokens: <span class="text-primary font-medium">{formatTokens(currentRun.tokens_total ?? agentPresence.tokensBurned ?? 0)}</span>
        </div>
        {#if currentRun.started_at}
          <div class="text-secondary">·</div>
          <div class="text-secondary">started: <span class="text-primary font-medium">{timeAgo(currentRun.started_at)}</span></div>
        {/if}
        {#if currentRun.turns != null}
          <div class="text-secondary">·</div>
          <div class="text-secondary">turns: <span class="text-primary font-medium">{currentRun.turns}</span></div>
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

  <!-- Plan-review gate banner (issue #266) -->
  {#if planReviewStatus}
    <div
      class="px-4 py-2 text-xs border-b border-border flex items-center gap-3 flex-wrap"
      class:bg-amber-500={planReviewStatus === 'awaiting_plan_review' || planReviewStatus === 'plan_reviewing'}
      class:bg-green-600={planReviewStatus === 'plan_approved'}
      class:bg-red-600={planReviewStatus === 'plan_rejected'}
      class:text-white={true}
      data-testid="plan-review-banner"
    >
      <span class="flex-1 min-w-0">
        {#if planReviewStatus === 'awaiting_plan_review'}
          <strong>Plan awaiting review.</strong> The teammate wrote an implementation plan; approve to enqueue a follow-up <code class="font-mono">full</code> run, or reject to stop here.
        {:else if planReviewStatus === 'plan_reviewing'}
          <strong>Manager reviewing plan…</strong>
        {:else if planReviewStatus === 'plan_approved'}
          <strong>Plan approved.</strong> A follow-up full run has been enqueued.
        {:else if planReviewStatus === 'plan_rejected'}
          <strong>Plan rejected.</strong> No follow-up run will be queued.
        {/if}
      </span>
      {#if planReviewStatus === 'awaiting_plan_review'}
        <span class="flex gap-2">
          <button
            type="button"
            onclick={approveCurrentRunPlan}
            disabled={planActionInFlight}
            class="px-2 py-0.5 rounded bg-white/20 hover:bg-white/30 text-white text-xs font-semibold disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            data-testid="plan-review-approve-btn"
          >
            {planActionInFlight === 'approve' ? 'Approving…' : 'Approve'}
          </button>
          <button
            type="button"
            onclick={rejectCurrentRunPlan}
            disabled={planActionInFlight}
            class="px-2 py-0.5 rounded bg-white/20 hover:bg-white/30 text-white text-xs font-semibold disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            data-testid="plan-review-reject-btn"
          >
            {planActionInFlight === 'reject' ? 'Rejecting…' : 'Reject'}
          </button>
        </span>
      {/if}
    </div>
  {/if}

  <!-- Main content -->
  <div class="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-0">
    <!-- Activity / thinking feed -->
    <div class="relative flex flex-col min-h-0 border-r border-border">
      <div class="px-3 py-2 border-b border-border text-xs uppercase tracking-wider text-primary font-semibold bg-surface-0">
        Activity — tools, thinking, messages
      </div>
      <div class="flex-1 min-h-0 p-2">
        {#if agentPresence.conversationLog.length === 0 && !isLive}
          <div class="flex items-center justify-center h-full text-secondary text-sm">
            Idle. Trigger a run to see live agent activity here.
          </div>
        {:else}
          <AgentActivityFeed maxEntries={200} />
        {/if}
      </div>
    </div>

    <!-- Side panel: operator message -->
    <aside class="flex flex-col min-h-0 bg-surface-1">
      <div class="px-3 py-2 border-b border-border text-xs uppercase tracking-wider text-primary font-semibold">
        Send message to agent
      </div>
      <div class="flex flex-col gap-2 p-3">
        <textarea
          bind:value={messageText}
          onkeydown={handleMessageKey}
          placeholder={currentRunId
            ? 'Type a message — the agent picks it up within ~1s. Ctrl/⌘+Enter to send.'
            : 'No live run. Trigger the agent (header button) to start a run, then send.'}
          rows="5"
          data-testid="mc-message-input"
          class="bg-surface-0 text-primary text-sm px-3 py-2 rounded border border-border
                 focus:border-border-focus outline-none placeholder:text-secondary font-mono resize-y
                 disabled:opacity-50 disabled:cursor-not-allowed"
          disabled={sending || !currentRunId}
        ></textarea>
        <div class="flex items-center justify-between">
          <span class="text-[11px] text-secondary">
            {#if currentRunId}
              Delivered within ~1s (polled independently of SDK stream).
            {:else}
              Messages require a live run — the orchestrator must be active to receive them.
            {/if}
          </span>
          <button
            type="button"
            onclick={handleSend}
            data-testid="mc-send-btn"
            disabled={sending || !messageText.trim() || !currentRunId}
            title={currentRunId ? 'Send message to the running agent' : 'No live run — message would be lost'}
            class="px-3 py-1.5 rounded text-xs font-semibold bg-accent-blue/25 text-accent-blue
                   hover:bg-accent-blue/40 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {sending ? '…' : 'Send'}
          </button>
        </div>
      </div>

      <div class="px-3 py-2 border-t border-border text-xs uppercase tracking-wider text-primary font-semibold mt-2">
        Agents
      </div>
      <div class="flex flex-col gap-1 p-3 text-xs">
        {#each agentPresence.agents as agent}
          <div class="flex items-center gap-2">
            <span class="w-2 h-2 rounded-full" style="background: {agent.color}"></span>
            <span class="text-primary font-medium">{agent.name}</span>
            <span class="text-secondary">·</span>
            <span class="text-secondary">{agent.status}</span>
          </div>
        {:else}
          <span class="text-secondary italic">No active agents</span>
        {/each}
      </div>
    </aside>
  </div>
</div>
