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
  let headerRunId = $derived(currentRunId || agentPresence.latestRunId || '');
  let currentRun = $derived(
    agentPresence.activeRuns.find((r) => r.run_id === currentRunId) ?? null,
  );
  let isLive = $derived(agentPresence.activeRuns.length > 0);
  let runPaused = $derived(!!agentPresence.pausedRuns[currentRunId]);
  let pendingDecisions = $derived(agentPresence.pendingDecisionCount);

  // Issue #266: surface the plan-review gate so operators can see when a
  // plan_only run is waiting on the manager (or has been approved/rejected).
  let planReviewStatus = $derived<string | null>(
    currentRun &&
      (currentRun.status === 'awaiting_plan_review' ||
        currentRun.status === 'plan_approved' ||
        currentRun.status === 'plan_rejected' ||
        currentRun.status === 'plan_reviewing')
      ? (currentRun.status as string)
      : null,
  );

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

<div class="mc-shell">
  <!-- Header strip — Pro -->
  <header class="mc-head">
    <div class="mc-head-left">
      <span class="mc-title">Mission Control <span class="sep">·</span> OP-7</span>
      <span class="mc-runid mono" data-testid="mc-run-id">
        {#if currentRunId}
          {currentRunId}
        {:else if headerRunId}
          <span class="muted">{headerRunId}</span>
          <span class="ended">(ended — read-only)</span>
        {:else}
          <span class="muted">no active run</span>
        {/if}
      </span>
    </div>

    <div class="mc-head-meta mono">
      <span class="live-pair">
        <span class="dot {isLive ? 'go live' : 'idle'}"></span>
        <span class="strong">{isLive ? 'live' : 'idle'}</span>
      </span>
      {#if currentRun}
        <span class="sep">·</span>
        <span>phase: <b>{agentPresence.phase}</b></span>
        <span class="sep">·</span>
        <span>tokens: <b>{formatTokens(currentRun.tokens_total ?? agentPresence.tokensBurned ?? 0)}</b></span>
        {#if currentRun.started_at}
          <span class="sep">·</span>
          <span>started: <b>{timeAgo(currentRun.started_at)}</b></span>
        {/if}
        {#if currentRun.turns != null}
          <span class="sep">·</span>
          <span>turns: <b>{currentRun.turns}</b></span>
        {/if}
      {/if}
      {#if runPaused}
        <span class="badge caution">paused</span>
      {/if}
      {#if agentPresence.globalPause}
        <span class="badge abort">global pause</span>
      {/if}
      {#if pendingDecisions > 0}
        <span class="badge data">{pendingDecisions} pending</span>
      {/if}
    </div>

    {#if !isLive}
      <button type="button" class="trigger-btn" onclick={handleTrigger}>Trigger run</button>
    {/if}
  </header>

  <!-- Intervention bar (kept as-is; styled by its own component) -->
  <InterventionBar runId={currentRunId} />

  <!-- Plan-review banner — Pro -->
  {#if planReviewStatus}
    <div
      class="plan-banner"
      class:awaiting={planReviewStatus === 'awaiting_plan_review' || planReviewStatus === 'plan_reviewing'}
      class:approved={planReviewStatus === 'plan_approved'}
      class:rejected={planReviewStatus === 'plan_rejected'}
      data-testid="plan-review-banner"
    >
      <span class="lev">
        {#if planReviewStatus === 'awaiting_plan_review'}Plan Awaiting Review
        {:else if planReviewStatus === 'plan_reviewing'}Manager Reviewing
        {:else if planReviewStatus === 'plan_approved'}Plan Approved
        {:else if planReviewStatus === 'plan_rejected'}Plan Rejected
        {/if}
      </span>
      <span class="body">
        {#if planReviewStatus === 'awaiting_plan_review'}
          The teammate wrote an implementation plan; <b>approve</b> to enqueue a follow-up <code class="mono">full</code> run, or <b>reject</b> to stop here.
        {:else if planReviewStatus === 'plan_reviewing'}
          Manager reviewing plan…
        {:else if planReviewStatus === 'plan_approved'}
          A follow-up full run has been enqueued.
        {:else if planReviewStatus === 'plan_rejected'}
          No follow-up run will be queued.
        {/if}
      </span>
      {#if planReviewStatus === 'awaiting_plan_review'}
        <span class="actions">
          <button
            type="button"
            class="banner-btn approve"
            onclick={approveCurrentRunPlan}
            disabled={planActionInFlight !== null}
            data-testid="plan-review-approve-btn"
          >
            {planActionInFlight === 'approve' ? 'Approving…' : 'Approve'}
          </button>
          <button
            type="button"
            class="banner-btn reject"
            onclick={rejectCurrentRunPlan}
            disabled={planActionInFlight !== null}
            data-testid="plan-review-reject-btn"
          >
            {planActionInFlight === 'reject' ? 'Rejecting…' : 'Reject'}
          </button>
        </span>
      {/if}
    </div>
  {/if}

  <!-- Main grid -->
  <div class="mc-main">
    <!-- Console: live activity feed -->
    <section class="console">
      <div class="section-head">
        <span>Agent Activity <span class="sep-light">·</span> Live</span>
        <span class="section-head-right mono">
          {#if isLive}<span class="dot go live"></span>streaming{:else}<span class="dot idle"></span>idle{/if}
        </span>
      </div>
      <div class="feed-wrap">
        {#if agentPresence.conversationLog.length === 0 && !isLive}
          <div class="empty-feed">Idle. Trigger a run to see live agent activity here.</div>
        {:else}
          <AgentActivityFeed maxEntries={200} />
        {/if}
      </div>
    </section>

    <!-- Side rail: TX + agents -->
    <aside class="aside">
      <section>
        <div class="section-head">
          <span>TX <span class="sep-light">·</span> Send to Agent</span>
          <span class="section-head-right mono">
            {currentRunId ? 'SSE open · ~1s' : 'no live run'}
          </span>
        </div>
        <div class="tx">
          <div class="tx-prompt mono">
            <span class="caret">&gt;</span><span>operator@station ~ </span>
          </div>
          <textarea
            bind:value={messageText}
            onkeydown={handleMessageKey}
            placeholder={currentRunId
              ? 'Type a message — agent picks it up within ~1s. Ctrl/⌘+Enter to send.'
              : 'No live run. Trigger the agent (header button) to start a run, then send.'}
            rows="4"
            data-testid="mc-message-input"
            disabled={sending || !currentRunId}
          ></textarea>
          <div class="tx-row">
            <span class="hint mono">
              {#if currentRunId}
                <b>⌘+Enter</b> send · delivered within ~1s
              {:else}
                Messages require a live run.
              {/if}
            </span>
            <button
              type="button"
              class="send-btn"
              onclick={handleSend}
              data-testid="mc-send-btn"
              disabled={sending || !messageText.trim() || !currentRunId}
              title={currentRunId ? 'Send message to the running agent' : 'No live run — message would be lost'}
            >
              {sending ? '…' : 'Send'}
            </button>
          </div>
        </div>
      </section>

      <section>
        <div class="section-head">
          <span>Agents</span>
          <span class="section-head-right mono">{agentPresence.agents.length} linked</span>
        </div>
        <div class="agents">
          {#each agentPresence.agents as agent}
            <div class="agent-row mono">
              <span class="dot" style="background: {agent.color}"></span>
              <span class="name">{agent.name}</span>
              <span class="muted sep">·</span>
              <span class="muted">{agent.status}</span>
            </div>
          {:else}
            <div class="empty-mini mono">No active agents.</div>
          {/each}
        </div>
      </section>
    </aside>
  </div>
</div>

<style>
  .mc-shell {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    background: var(--paper);
    color: var(--ink);
    font-family: var(--pro-sans);
  }

  /* ── Header strip ──────────────────────────────────── */
  .mc-head {
    display: flex; align-items: center; gap: 18px;
    padding: 10px 16px;
    border-bottom: 1px solid var(--rule);
    background: var(--paper);
    flex-shrink: 0;
  }
  .mc-head-left { display: flex; flex-direction: column; gap: 3px; }
  .mc-title {
    font-family: var(--pro-sans);
    font-weight: 700; font-size: 11px;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--ink);
  }
  .mc-title .sep { color: var(--ash); margin: 0 4px; }
  .mc-runid {
    font-family: var(--pro-mono);
    font-size: 12px;
    color: var(--ink);
  }
  .mc-runid .muted { color: var(--graphite); }
  .mc-runid .ended { color: var(--caution); font-size: 9px; text-transform: uppercase; letter-spacing: 0.14em; margin-left: 6px; }

  .mc-head-meta {
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    margin-left: 14px;
    font-family: var(--pro-mono);
    font-size: 11px;
    color: var(--graphite);
  }
  .mc-head-meta b { color: var(--ink); font-weight: 500; }
  .mc-head-meta .strong { color: var(--ink); font-weight: 500; }
  .mc-head-meta .sep { color: var(--ash); }
  .live-pair { display: inline-flex; align-items: center; gap: 6px; }

  .badge {
    font-family: var(--pro-sans);
    font-size: 9px; font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase;
    padding: 2px 6px;
    border: 1px solid currentColor;
  }
  .badge.caution { color: var(--caution); }
  .badge.abort   { color: var(--abort); }
  .badge.data    { color: var(--data); }

  .trigger-btn {
    margin-left: auto;
    font-family: var(--pro-sans);
    font-weight: 700; font-size: 10px;
    letter-spacing: 0.16em; text-transform: uppercase;
    background: transparent; color: var(--data);
    border: 1px solid color-mix(in oklab, var(--data) 50%, transparent);
    padding: 5px 12px; cursor: pointer; height: 28px;
  }
  .trigger-btn:hover {
    background: color-mix(in oklab, var(--data) 12%, var(--paper));
  }

  /* ── Dot ───────────────────────────────────────────── */
  .dot {
    display: inline-block; width: 7px; height: 7px;
    background: var(--ash); margin-right: 5px; transform: translateY(-1px);
  }
  .dot.go { background: var(--go); }
  .dot.idle { background: var(--ash); }
  .dot.live { animation: livedot 1.6s steps(2) infinite; }
  @keyframes livedot { 50% { opacity: .35; } }

  /* ── Plan-review banner ───────────────────────────── */
  .plan-banner {
    padding: 8px 16px;
    border-bottom: 1px solid var(--rule);
    border-left: 3px solid var(--caution);
    background: color-mix(in oklab, var(--caution) 12%, var(--paper));
    font-family: var(--pro-mono);
    font-size: 12px;
    color: var(--ink);
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    flex-shrink: 0;
  }
  .plan-banner.approved { border-left-color: var(--go); background: color-mix(in oklab, var(--go) 10%, var(--paper)); }
  .plan-banner.rejected { border-left-color: var(--abort); background: color-mix(in oklab, var(--abort) 10%, var(--paper)); }
  .plan-banner .lev {
    font-family: var(--pro-sans); font-weight: 700;
    font-size: 10px; letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--caution);
  }
  .plan-banner.approved .lev { color: var(--go); }
  .plan-banner.rejected .lev { color: var(--abort); }
  .plan-banner .body { flex: 1; min-width: 0; }
  .plan-banner .body b { color: var(--ink); font-weight: 500; }
  .plan-banner .actions { display: flex; gap: 6px; }
  .banner-btn {
    font-family: var(--pro-sans);
    font-weight: 700; font-size: 10px;
    letter-spacing: 0.16em; text-transform: uppercase;
    background: transparent; color: var(--ink);
    border: 1px solid var(--rule-2);
    padding: 3px 10px; cursor: pointer;
  }
  .banner-btn:hover:not(:disabled) { background: var(--paper-2); }
  .banner-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .banner-btn.approve { color: var(--go); border-color: color-mix(in oklab, var(--go) 50%, transparent); }
  .banner-btn.reject  { color: var(--abort); border-color: color-mix(in oklab, var(--abort) 50%, transparent); }

  /* ── Main grid ─────────────────────────────────────── */
  .mc-main {
    flex: 1; min-height: 0;
    display: grid;
    grid-template-columns: 1fr 360px;
  }
  .console {
    display: flex; flex-direction: column; min-height: 0;
    border-right: 1px solid var(--rule);
  }
  .aside { display: flex; flex-direction: column; min-height: 0; background: var(--paper); }
  .aside > section + section { border-top: 1px solid var(--rule); }

  /* ── Section head ─────────────────────────────────── */
  .section-head {
    height: 30px;
    display: flex; align-items: center; justify-content: space-between;
    padding: 0 14px;
    font-family: var(--pro-sans);
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.2em; text-transform: uppercase;
    color: var(--ash);
    background: var(--paper-2);
    border-bottom: 1px solid var(--rule);
    flex-shrink: 0;
  }
  .section-head .sep-light { color: var(--ash); }
  .section-head-right {
    color: var(--graphite); font-family: var(--pro-mono); font-size: 10px;
    letter-spacing: 0; text-transform: none;
    display: flex; gap: 6px; align-items: center;
  }

  /* ── Feed area ─────────────────────────────────────── */
  .feed-wrap {
    flex: 1; min-height: 0;
    padding: 4px 0;
    background: var(--paper);
  }
  .empty-feed {
    height: 100%;
    display: grid; place-items: center;
    color: var(--ash);
    font-family: var(--pro-mono);
    font-size: 12px;
    padding: 20px;
  }

  /* ── TX panel ──────────────────────────────────────── */
  .tx { padding: 12px 14px; display: flex; flex-direction: column; gap: 8px; }
  .tx-prompt {
    font-family: var(--pro-mono); font-size: 11px; color: var(--graphite);
    display: flex; align-items: baseline; gap: 8px;
  }
  .tx-prompt .caret { color: var(--ink); font-weight: 700; }
  .tx textarea {
    font-family: var(--pro-mono);
    font-size: 12px; line-height: 1.4;
    background: var(--paper-2);
    color: var(--ink);
    border: 1px solid var(--rule-2);
    padding: 10px 12px;
    resize: vertical;
    min-height: 84px;
    outline: none;
    width: 100%;
  }
  .tx textarea:focus { border-color: var(--ink); }
  .tx textarea::placeholder { color: var(--ash); font-style: italic; }
  .tx textarea:disabled { opacity: 0.5; cursor: not-allowed; }

  .tx-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .hint { font-family: var(--pro-mono); font-size: 10px; color: var(--ash); }
  .hint b { color: var(--graphite); font-weight: 500; }

  .send-btn {
    font-family: var(--pro-sans);
    font-weight: 700; font-size: 10px;
    letter-spacing: 0.16em; text-transform: uppercase;
    background: var(--data); color: var(--paper);
    border: 1px solid var(--data);
    padding: 6px 14px; cursor: pointer; height: 28px;
  }
  .send-btn:hover:not(:disabled) { filter: brightness(1.08); }
  .send-btn:disabled { opacity: 0.4; cursor: not-allowed; }

  /* ── Agents roster ─────────────────────────────────── */
  .agents { padding: 6px 0 8px; }
  .agent-row {
    display: flex; align-items: center; gap: 8px;
    padding: 5px 14px;
    font-family: var(--pro-mono); font-size: 12px;
  }
  .agent-row:hover { background: var(--paper-2); }
  .agent-row .name { color: var(--ink); }
  .agent-row .muted { color: var(--graphite); }
  .agent-row .sep { color: var(--ash); }
  .empty-mini {
    padding: 12px 14px;
    color: var(--ash); font-style: italic;
    font-family: var(--pro-mono); font-size: 12px;
  }

  @media (max-width: 1100px) {
    .mc-main { grid-template-columns: 1fr; }
    .console { border-right: none; }
    .aside { border-top: 1px solid var(--rule); }
  }
</style>
