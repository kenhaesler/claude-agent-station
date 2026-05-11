<script lang="ts">
  import { agentPresence, type ConversationEntry } from '../lib/agent-presence.svelte';
  import {
    messageRun,
    triggerRun,
    operatorApproveRunPlan,
    operatorRejectRunPlan,
    pauseRun,
    resumeRun,
    stopRun,
    pauseAll,
    resumeAll,
    getGlobalPause,
  } from '../lib/api';
  import { addToast } from '../lib/toast.svelte';
  import { formatTokens, timeAgo } from '../lib/format';

  // ──────────────────────────────────────────────────────────
  // Active run wiring (unchanged from prior implementation)
  // ──────────────────────────────────────────────────────────
  // Mission Control targets the first active run. We deliberately do NOT
  // fall back to `latestRunId` for interventions — that fallback routed
  // operator messages to already-finished runs where the orchestrator had
  // exited. The header still surfaces latestRunId for context.
  let currentRunId = $derived(agentPresence.activeRuns[0]?.run_id ?? '');
  let headerRunId = $derived(currentRunId || agentPresence.latestRunId || '');
  let currentRun = $derived(
    agentPresence.activeRuns.find((r) => r.run_id === currentRunId) ?? null,
  );
  let isLive = $derived(agentPresence.activeRuns.length > 0);
  let runPaused = $derived(!!agentPresence.pausedRuns[currentRunId]);
  let globalPause = $derived(agentPresence.globalPause);
  let pendingDecisions = $derived(agentPresence.pendingDecisionCount);

  // Issue #266 — surface the plan-review gate so operators can see when a
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

  // Display helpers ------------------------------------------------------
  function shortRunId(id: string): string {
    if (!id) return '';
    if (id.length <= 14) return id;
    return id.slice(0, 4) + '…' + id.slice(-10);
  }

  function autonomyLabel(mode: string | null | undefined): string {
    if (!mode) return 'MANUAL';
    if (mode === 'agent-teams') return 'AUTO';
    if (mode === 'plan' || mode === 'planner') return 'ASSIST';
    if (mode === 'manager') return 'MANAGER';
    return mode.toUpperCase();
  }

  // ──────────────────────────────────────────────────────────
  // Plan-review actions
  // ──────────────────────────────────────────────────────────
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

  // ──────────────────────────────────────────────────────────
  // Intervention actions (pause / stop / global pause)
  // ──────────────────────────────────────────────────────────
  let pausing = $state(false);
  let stopping = $state(false);
  let globalPausing = $state(false);

  // Hydrate global-pause state once on mount.
  $effect(() => {
    (async () => {
      try {
        const s = await getGlobalPause();
        agentPresence.globalPause = s.global_pause;
      } catch {
        /* ignore */
      }
    })();
  });

  function friendly(e: unknown, fallback: string): string {
    const raw = e instanceof Error ? e.message : fallback;
    return raw.startsWith('409:') ? raw.slice(4).trim() || fallback : raw;
  }

  async function onPauseRun() {
    if (!currentRunId || pausing) return;
    pausing = true;
    try {
      if (runPaused) {
        await resumeRun(currentRunId);
        addToast('success', `Resume requested for ${currentRunId}`);
      } else {
        await pauseRun(currentRunId);
        addToast('success', `Pause requested for ${currentRunId}`);
      }
    } catch (e) {
      addToast('error', friendly(e, 'Pause/resume failed'));
    } finally {
      pausing = false;
    }
  }

  async function onStopRun() {
    if (!currentRunId || stopping) return;
    const ok = confirm(
      `Stop run ${currentRunId}? The agent will halt after its next tool call and finish with status=interrupted.`,
    );
    if (!ok) return;
    stopping = true;
    try {
      await stopRun(currentRunId);
      addToast('success', `Stop requested for ${currentRunId}`);
    } catch (e) {
      addToast('error', friendly(e, 'Stop failed'));
    } finally {
      stopping = false;
    }
  }

  async function onPauseAll() {
    if (globalPausing) return;
    const ok = globalPause
      ? true
      : confirm(
          'Pause ALL runs? Every next tool call on every run will block until you approve it in the permission tray.',
        );
    if (!ok) return;
    globalPausing = true;
    try {
      if (globalPause) {
        const s = await resumeAll();
        agentPresence.globalPause = s.global_pause;
        addToast('success', 'Global pause cleared');
      } else {
        const s = await pauseAll();
        agentPresence.globalPause = s.global_pause;
        addToast('success', 'All agents paused — approve each tool call via the tray');
      }
    } finally {
      globalPausing = false;
    }
  }

  // ──────────────────────────────────────────────────────────
  // Operator → agent message
  // ──────────────────────────────────────────────────────────
  let messageText = $state('');
  let sending = $state(false);
  let lastSent = $state('');

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
      lastSent = text;
      messageText = '';
    } catch (e) {
      const raw = e instanceof Error ? e.message : 'Send failed';
      const out = raw.startsWith('409:')
        ? raw.slice(4).trim() || 'Run is no longer active — message not delivered'
        : raw;
      addToast('error', out);
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
      return;
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      messageText = '';
      return;
    }
    if (e.key === 'ArrowUp' && !messageText && lastSent) {
      e.preventDefault();
      messageText = lastSent;
    }
  }

  // ──────────────────────────────────────────────────────────
  // Activity feed (dense console rows)
  // ──────────────────────────────────────────────────────────
  // Map ConversationEntry types to draft glyphs/types.
  const TYPE_GLYPH: Record<string, string> = {
    text: '>',
    thinking: '*',
    tool_use: '→',
    tool: '→',
    result: '←',
    system: '#',
    phase: '#',
    guidance: '@',
    error: '!',
  };

  // Map agent name → role tint key matched in CSS via `data-role`.
  function roleFor(name: string): string {
    const n = name.toLowerCase();
    if (n === 'operator') return 'operator';
    if (n === 'lead' || n === 'manager' || n === 'coordinator') return 'lead';
    if (n.includes('backend')) return 'backend';
    if (n.includes('frontend')) return 'frontend';
    if (n.includes('qa')) return 'qa';
    // Teammate N — distribute across role tints by index for visual variety.
    const m = n.match(/teammate\s*(\d+)/);
    if (m) {
      const idx = parseInt(m[1], 10);
      const tints = ['backend', 'frontend', 'qa'];
      return tints[(idx - 1) % tints.length] ?? 'backend';
    }
    return 'lead';
  }

  function rowKind(e: ConversationEntry): string {
    if (e.isError) return 'error';
    return e.type === 'tool_use' ? 'tool' : (e.type as string);
  }

  function fmtTime(ts: number): string {
    const d = new Date(ts);
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    const ss = String(d.getSeconds()).padStart(2, '0');
    return `${hh}:${mm}:${ss}`;
  }

  // Filter selector
  let filterValue = $state('');
  let entries = $derived.by(() => {
    const log = agentPresence.conversationLog;
    if (!filterValue) return log.slice(-200);
    return log
      .filter((e) => {
        const k = rowKind(e);
        if (filterValue === 'tool') return k === 'tool';
        return k === filterValue;
      })
      .slice(-200);
  });

  // Auto-scroll
  let feedEl: HTMLDivElement | undefined = $state();
  let autoScroll = $state(true);
  $effect(() => {
    // Re-run when entries change.
    void entries.length;
    if (autoScroll && feedEl) {
      requestAnimationFrame(() => {
        if (feedEl) feedEl.scrollTop = feedEl.scrollHeight;
      });
    }
  });
  function onFeedScroll() {
    if (!feedEl) return;
    const dist = feedEl.scrollHeight - feedEl.scrollTop - feedEl.clientHeight;
    autoScroll = dist < 40;
  }
  function jumpToLive() {
    autoScroll = true;
    if (feedEl) feedEl.scrollTop = feedEl.scrollHeight;
  }

  // ──────────────────────────────────────────────────────────
  // Hotkeys: p = pause/resume run, s = stop run, / = focus input
  // ──────────────────────────────────────────────────────────
  let txInputEl: HTMLTextAreaElement | undefined = $state();
  function onWindowKey(e: KeyboardEvent) {
    const t = e.target as HTMLElement | null;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT')) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === 'p') {
      e.preventDefault();
      onPauseRun();
    } else if (e.key === 's') {
      e.preventDefault();
      onStopRun();
    } else if (e.key === '/') {
      e.preventDefault();
      txInputEl?.focus();
    }
  }
</script>

<svelte:window onkeydown={onWindowKey} />

<div class="mc-shell">
  <!-- Page head ─────────────────────────────────────────────── -->
  <div class="page-head">
    <h1>Mission Control <span class="br">·</span> OP-7</h1>
    <div class="runid mono" data-testid="mc-run-id">
      {#if currentRunId}
        Run <b>{shortRunId(currentRunId)}</b>
        {#if currentRun}
          <span class="br">·</span> phase <b>{agentPresence.phase}</b>
        {/if}
      {:else if headerRunId}
        Run <b class="muted">{shortRunId(headerRunId)}</b>
        <span class="ended">(ended — read-only)</span>
      {:else}
        <span class="muted">no active run</span>
      {/if}
    </div>
    <div class="meta mono">
      {#if currentRun}
        <span>Tokens <b>{formatTokens(currentRun.tokens_total ?? agentPresence.tokensBurned ?? 0)}</b></span>
        <span class="br">·</span>
        <span>Turns <b>{currentRun.turns ?? 0}</b></span>
        {#if currentRun.started_at}
          <span class="br">·</span>
          <span>Started <b>{timeAgo(currentRun.started_at)}</b></span>
        {/if}
        {#if currentRun?.last_event_at}
          {@const ageSec = (Date.now() - new Date(currentRun.last_event_at).getTime()) / 1000}
          <span class="heartbeat-badge"
                class:warn={ageSec > 60 && ageSec <= 180}
                class:stale={ageSec > 180}>
            active {Math.round(ageSec)}s ago
          </span>
        {/if}
        <span class="br">·</span>
        <span>Aut <b>{autonomyLabel(currentRun.mode)}</b></span>
      {:else}
        <span><span class="dot idle"></span><b>idle</b></span>
        <button type="button" class="trigger-btn" onclick={handleTrigger}>Trigger Run</button>
      {/if}
      {#if pendingDecisions > 0}
        <span class="br">·</span>
        <span class="badge data">{pendingDecisions} pending</span>
      {/if}
    </div>
  </div>

  <!-- Intervention bar ──────────────────────────────────────── -->
  <div class="intervene">
    <span class="label">Intervene</span>

    <button
      type="button"
      class="opbtn caution"
      class:active={runPaused}
      onclick={onPauseRun}
      disabled={pausing || !currentRunId}
      title={runPaused ? 'Resume this run' : "Route this run's next tool call to the tray"}
    >
      <span class="glyph">{runPaused ? '▶' : '⏸'}</span>
      {pausing ? '…' : runPaused ? 'Resume Run' : 'Pause Run'}
    </button>

    <button
      type="button"
      class="opbtn abort"
      onclick={onStopRun}
      disabled={stopping || !currentRunId}
      title="Interrupt this run cooperatively (finishes with status=interrupted)"
    >
      <span class="glyph">⏹</span>
      {stopping ? '…' : 'Stop Run'}
    </button>

    <span class="sep"></span>

    <button
      type="button"
      class="opbtn abort"
      class:active={globalPause}
      onclick={onPauseAll}
      disabled={globalPausing}
      title={globalPause ? 'Release the global kill-switch' : 'Freeze every tool call on every run'}
    >
      <span class="glyph">{globalPause ? '▶' : '⚠'}</span>
      {globalPausing ? '…' : globalPause ? 'Resume All' : 'Pause All'}
    </button>

    {#if globalPause}
      <span class="pause-note global">Global pause active — approve every tool call in the tray.</span>
    {:else if runPaused}
      <span class="pause-note">Run paused — next tool call will wait for approval.</span>
    {/if}
  </div>

  <!-- Plan-review banner ────────────────────────────────────── -->
  {#if planReviewStatus}
    <div
      class="banner"
      class:ok={planReviewStatus === 'plan_approved'}
      class:no={planReviewStatus === 'plan_rejected'}
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
          The teammate wrote an implementation plan. <b>Approve</b> to enqueue a follow-up <code>full</code> run, or <b>reject</b> to stop here.
        {:else if planReviewStatus === 'plan_reviewing'}
          Manager reviewing plan…
        {:else if planReviewStatus === 'plan_approved'}
          A follow-up full run has been enqueued.
        {:else if planReviewStatus === 'plan_rejected'}
          No follow-up run will be queued.
        {/if}
      </span>
      {#if planReviewStatus === 'awaiting_plan_review'}
        <button
          type="button"
          class="go"
          onclick={approveCurrentRunPlan}
          disabled={planActionInFlight !== null}
          data-testid="plan-review-approve-btn"
        >
          {planActionInFlight === 'approve' ? 'Approving…' : 'Approve'}
        </button>
        <button
          type="button"
          class="no"
          onclick={rejectCurrentRunPlan}
          disabled={planActionInFlight !== null}
          data-testid="plan-review-reject-btn"
        >
          {planActionInFlight === 'reject' ? 'Rejecting…' : 'Reject'}
        </button>
      {/if}
    </div>
  {/if}

  <!-- Main grid ─────────────────────────────────────────────── -->
  <div class="main">
    <!-- Console: dense activity feed -->
    <section class="console">
      <div class="section-head">
        <span>Agent Activity · Live</span>
        <span class="right">
          <span class="count">{entries.length}</span>
          <select bind:value={filterValue} aria-label="Filter feed">
            <option value="">all</option>
            <option value="tool">tools</option>
            <option value="thinking">thinking</option>
            <option value="text">text</option>
            <option value="result">results</option>
            <option value="system">system</option>
            <option value="error">errors</option>
          </select>
        </span>
      </div>
      <div
        class="feed"
        bind:this={feedEl}
        onscroll={onFeedScroll}
      >
        {#if entries.length === 0}
          <div class="empty-feed mono">
            {isLive ? 'Waiting for first event…' : 'Idle. Trigger a run to see live agent activity here.'}
          </div>
        {:else}
          {#each entries as e (e.id)}
            {@const kind = rowKind(e)}
            <div class="feed-row {kind}" data-type={kind}>
              <span class="t">{fmtTime(e.timestamp)}</span>
              <span class="agent" data-role={roleFor(e.agentName)}>{e.agentName}</span>
              <span class="glyph">{TYPE_GLYPH[kind] ?? '·'}</span>
              <span class="body">
                {#if e.toolName}<em>{e.toolName}</em>{/if}{e.content}
              </span>
            </div>
          {/each}
        {/if}
        {#if !autoScroll && entries.length > 0}
          <button type="button" class="scroll-pin visible" onclick={jumpToLive}>↓ jump to live</button>
        {/if}
      </div>
    </section>

    <!-- Side rail: TX + agents -->
    <aside class="aside">
      <section>
        <div class="section-head">
          <span>TX · Send to Agent</span>
          <span class="right">
            {#if sending}sending…
            {:else if currentRunId}SSE open · ~1s delivery
            {:else}no live run
            {/if}
          </span>
        </div>
        <div class="tx">
          <div class="prompt mono">
            <span class="caret">&gt;</span><span>operator@station ~ </span>
          </div>
          <textarea
            bind:value={messageText}
            bind:this={txInputEl}
            onkeydown={handleMessageKey}
            rows="4"
            placeholder={currentRunId
              ? 'Type a message — agent picks it up within ~1s. Ctrl/⌘+Enter to send.'
              : 'No live run. Trigger the agent (header button) to start a run, then send.'}
            data-testid="mc-message-input"
            disabled={sending || !currentRunId}
          ></textarea>
          <div class="row">
            <span class="hint mono">
              {#if currentRunId}
                <b>⌘+Enter</b> send · <b>Esc</b> clear · <b>↑</b> last
              {:else}
                Messages require a live run.
              {/if}
            </span>
            <button
              type="button"
              onclick={handleSend}
              disabled={sending || !messageText.trim() || !currentRunId}
              data-testid="mc-send-btn"
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
          <span class="right">{agentPresence.agents.length} linked</span>
        </div>
        <div class="agents">
          {#each agentPresence.agents as agent}
            {@const role = roleFor(agent.name)}
            <div class="agent-row" data-role={role}>
              <span class="dot" style="background: var(--role-{role}, {agent.color})"></span>
              <span class="name">{agent.name}</span>
              <span class="stat">{agent.status}</span>
              <span class="role">{agent.role}</span>
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
  .heartbeat-badge {
    margin-left: 12px;
    padding: 2px 8px;
    border-radius: 10px;
    background: var(--paper-3);
    color: var(--graphite);
    font-size: 0.8em;
  }
  .heartbeat-badge.warn { background: rgba(251, 202, 4, 0.15); color: var(--abort); }
  .heartbeat-badge.stale { background: rgba(182, 2, 5, 0.15); color: var(--abort); }

  /* ──────────────────────────────────────────────────────────
     Mission Control · Pro dense-console layout.
     Mirrors design-drafts/mission.html. Strip / ticker / footer
     are owned by Shell — this page only renders its own content.
     ────────────────────────────────────────────────────────── */
  .mc-shell {
    display: flex;
    flex-direction: column;
    /* Match AgentTeamsCanvas: pin to viewport so .main can flex-grow into the
       remaining space below the page-head + intervene + (optional) banner.
       Without this, height:100% never resolves (App's wrapper isn't a flex
       container) and the .main grid collapses to its content height,
       leaving a tall blank gap above the global footer. */
    min-height: calc(100vh - 40px);
    background: var(--paper);
    color: var(--ink);
    font-family: var(--pro-sans);
  }
  .mono { font-family: var(--pro-mono); }

  /* Page head ──────────────────────────────────────────────── */
  .page-head {
    display: grid;
    grid-template-columns: auto 1fr auto;
    align-items: center;
    gap: 18px;
    padding: 10px 16px;
    border-bottom: 1px solid var(--rule);
    background: var(--paper);
    flex-shrink: 0;
  }
  .page-head h1 {
    margin: 0;
    font-family: var(--pro-sans);
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--ink);
  }
  .page-head h1 .br { color: var(--ash); margin: 0 4px; font-weight: 400; }
  .page-head .runid {
    font-family: var(--pro-mono);
    font-size: 11px;
    color: var(--graphite);
  }
  .page-head .runid b { color: var(--ink); font-weight: 500; }
  .page-head .runid .muted { color: var(--graphite); }
  .page-head .runid .br { color: var(--ash); margin: 0 6px; }
  .page-head .runid .ended {
    color: var(--caution);
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    margin-left: 6px;
  }
  .page-head .meta {
    font-family: var(--pro-mono);
    font-size: 11px;
    color: var(--graphite);
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    justify-content: flex-end;
    align-items: center;
  }
  .page-head .meta b { color: var(--ink); font-weight: 500; }
  .page-head .meta .br { color: var(--ash); }

  .trigger-btn {
    margin-left: 6px;
    font-family: var(--pro-sans);
    font-weight: 700;
    font-size: 10px;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    background: transparent;
    color: var(--data);
    border: 1px solid color-mix(in oklab, var(--data) 50%, transparent);
    padding: 4px 12px;
    cursor: pointer;
    height: 26px;
  }
  .trigger-btn:hover { background: color-mix(in oklab, var(--data) 12%, var(--paper)); }

  .badge {
    font-family: var(--pro-sans);
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    padding: 2px 6px;
    border: 1px solid currentColor;
    line-height: 1.3;
  }
  .badge.data { color: var(--data); }

  /* Dot ────────────────────────────────────────────────────── */
  .dot {
    display: inline-block;
    width: 7px;
    height: 7px;
    background: var(--ash);
    margin-right: 5px;
    transform: translateY(-1px);
  }
  .dot.idle { background: var(--ash); }

  /* Intervention bar ───────────────────────────────────────── */
  .intervene {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    padding: 8px 16px;
    border-bottom: 1px solid var(--rule);
    background: var(--paper);
    flex-shrink: 0;
  }
  .intervene .label {
    font-family: var(--pro-sans);
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--ash);
    margin-right: 4px;
  }
  .opbtn {
    font-family: var(--pro-sans);
    font-weight: 700;
    font-size: 10px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    background: transparent;
    color: var(--ink);
    border: 1px solid var(--rule-2);
    padding: 5px 11px;
    cursor: pointer;
    height: 28px;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .opbtn:hover:not(:disabled) { background: var(--paper-2); }
  .opbtn:disabled { opacity: 0.4; cursor: not-allowed; }
  .opbtn .glyph { font-family: var(--pro-mono); font-size: 12px; line-height: 1; color: var(--graphite); }
  .opbtn.caution { color: var(--caution); border-color: color-mix(in oklab, var(--caution) 50%, transparent); }
  .opbtn.caution:hover:not(:disabled) { background: color-mix(in oklab, var(--caution) 12%, var(--paper)); }
  .opbtn.caution .glyph { color: var(--caution); }
  .opbtn.abort { color: var(--abort); border-color: color-mix(in oklab, var(--abort) 50%, transparent); }
  .opbtn.abort:hover:not(:disabled) { background: color-mix(in oklab, var(--abort) 12%, var(--paper)); }
  .opbtn.abort .glyph { color: var(--abort); }
  .opbtn.active { background: color-mix(in oklab, var(--abort) 18%, var(--paper)); }
  .intervene .sep { width: 1px; height: 20px; background: var(--rule); margin: 0 4px; }
  .intervene .pause-note { font-family: var(--pro-mono); font-size: 11px; color: var(--caution); }
  .intervene .pause-note.global { color: var(--abort); }

  /* Banner ─────────────────────────────────────────────────── */
  .banner {
    padding: 6px 16px;
    border-bottom: 1px solid var(--rule);
    font-family: var(--pro-mono);
    font-size: 12px;
    display: flex;
    align-items: center;
    gap: 12px;
    background: color-mix(in oklab, var(--caution) 12%, var(--paper));
    border-left: 3px solid var(--caution);
    flex-shrink: 0;
  }
  .banner.ok {
    background: color-mix(in oklab, var(--go) 10%, var(--paper));
    border-left-color: var(--go);
  }
  .banner.no {
    background: color-mix(in oklab, var(--abort) 10%, var(--paper));
    border-left-color: var(--abort);
  }
  .banner .lev {
    font-family: var(--pro-sans);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--caution);
  }
  .banner.ok .lev { color: var(--go); }
  .banner.no .lev { color: var(--abort); }
  .banner .body { flex: 1; min-width: 0; }
  .banner .body b { color: var(--ink); font-weight: 500; }
  .banner button {
    font-family: var(--pro-sans);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    background: transparent;
    color: var(--ink);
    border: 1px solid var(--rule-2);
    padding: 3px 10px;
    cursor: pointer;
  }
  .banner button:hover:not(:disabled) { background: var(--paper-2); }
  .banner button:disabled { opacity: 0.5; cursor: not-allowed; }
  .banner button.go { color: var(--go); border-color: color-mix(in oklab, var(--go) 50%, transparent); }
  .banner button.no { color: var(--abort); border-color: color-mix(in oklab, var(--abort) 50%, transparent); }

  /* Main grid ──────────────────────────────────────────────── */
  .main {
    display: grid;
    grid-template-columns: 1fr 360px;
    flex: 1;
    min-height: 0;
  }
  .console {
    display: flex;
    flex-direction: column;
    min-height: 0;
    border-right: 1px solid var(--rule);
  }
  .aside {
    display: flex;
    flex-direction: column;
    min-height: 0;
    background: var(--paper);
  }
  .aside > section + section { border-top: 1px solid var(--rule); }

  .section-head .count {
    font-family: var(--pro-mono);
    color: var(--graphite);
    font-size: 10px;
  }
  .section-head select {
    font-family: var(--pro-mono);
    font-size: 11px;
    background: var(--paper);
    color: var(--ink);
    border: 1px solid var(--rule-2);
    padding: 1px 4px;
  }

  /* Feed ──────────────────────────────────────────────────── */
  .feed {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: 8px 0;
    position: relative;
    font-family: var(--pro-mono);
    font-size: 12px;
  }
  .empty-feed {
    height: 100%;
    display: grid;
    place-items: center;
    color: var(--ash);
    font-size: 12px;
    padding: 20px;
  }
  .feed-row {
    display: grid;
    grid-template-columns: 78px 110px 16px 1fr;
    gap: 8px;
    padding: 1px 14px;
    align-items: baseline;
  }
  .feed-row:hover { background: var(--paper-2); }
  .feed-row .t { color: var(--ash); font-size: 11px; }
  .feed-row .agent {
    color: var(--ink);
    font-weight: 500;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .feed-row .agent[data-role="lead"]      { color: var(--role-lead); }
  .feed-row .agent[data-role="backend"]   { color: var(--role-backend); }
  .feed-row .agent[data-role="frontend"]  { color: var(--role-frontend); }
  .feed-row .agent[data-role="qa"]        { color: var(--role-qa); }
  .feed-row .agent[data-role="operator"]  { color: var(--role-operator); }
  .feed-row .glyph { color: var(--graphite); font-size: 12px; text-align: center; }
  .feed-row .body {
    color: var(--ink);
    white-space: pre-wrap;
    word-break: break-word;
  }
  .feed-row .body em { font-style: normal; color: var(--data); margin-right: 5px; }
  .feed-row.thinking .body { color: var(--graphite); font-style: italic; }
  .feed-row.thinking .glyph { color: var(--caution); }
  .feed-row.tool .glyph { color: var(--data); }
  .feed-row.result .glyph { color: var(--go); }
  .feed-row.system .glyph { color: var(--ash); }
  .feed-row.system .body { color: var(--graphite); }
  .feed-row.phase .glyph { color: var(--ash); }
  .feed-row.phase .body { color: var(--graphite); }
  .feed-row.guidance { background: color-mix(in oklab, var(--data) 8%, transparent); }
  .feed-row.guidance .glyph { color: var(--data); }
  .feed-row.guidance .body { color: var(--ink); }
  .feed-row.error { background: color-mix(in oklab, var(--abort) 7%, transparent); }
  .feed-row.error .glyph { color: var(--abort); }
  .feed-row.error .body { color: var(--abort); }

  .scroll-pin {
    position: absolute;
    right: 14px;
    bottom: 12px;
    font-family: var(--pro-sans);
    font-size: 9px;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: var(--ink);
    background: var(--paper-2);
    border: 1px solid var(--rule-2);
    padding: 4px 10px;
    cursor: pointer;
  }

  /* TX panel ──────────────────────────────────────────────── */
  .tx { padding: 12px 14px; display: flex; flex-direction: column; gap: 8px; }
  .tx .prompt {
    font-size: 11px;
    color: var(--graphite);
    display: flex;
    align-items: baseline;
    gap: 8px;
  }
  .tx .prompt .caret { color: var(--ink); font-weight: 700; }
  .tx textarea {
    font-family: var(--pro-mono);
    font-size: 12px;
    line-height: 1.4;
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
  .tx textarea:disabled { opacity: 0.55; cursor: not-allowed; }
  .tx .row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .tx .hint { font-size: 10px; color: var(--ash); }
  .tx .hint b { color: var(--graphite); font-weight: 500; }
  .tx button {
    font-family: var(--pro-sans);
    font-weight: 700;
    font-size: 10px;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    background: var(--data);
    color: var(--paper);
    border: 1px solid var(--data);
    padding: 6px 14px;
    cursor: pointer;
    height: 28px;
  }
  .tx button:hover:not(:disabled) { filter: brightness(1.1); }
  .tx button:disabled { opacity: 0.4; cursor: not-allowed; }

  /* Agents roster ──────────────────────────────────────────── */
  .agents { padding: 6px 0 4px; }
  .agent-row {
    display: grid;
    grid-template-columns: 14px 1fr auto auto;
    gap: 10px;
    align-items: center;
    padding: 6px 14px;
    font-family: var(--pro-mono);
    font-size: 12px;
  }
  .agent-row:hover { background: var(--paper-2); }
  .agent-row .name { color: var(--ink); }
  .agent-row[data-role="lead"]     .name { color: var(--role-lead); }
  .agent-row[data-role="backend"]  .name { color: var(--role-backend); }
  .agent-row[data-role="frontend"] .name { color: var(--role-frontend); }
  .agent-row[data-role="qa"]       .name { color: var(--role-qa); }
  .agent-row .stat { color: var(--go); font-size: 11px; }
  .agent-row .role { color: var(--ash); font-size: 11px; }
  .empty-mini {
    padding: 12px 14px;
    color: var(--ash);
    font-style: italic;
    font-size: 12px;
  }

  @media (max-width: 1180px) {
    .main { grid-template-columns: 1fr; }
    .console { border-right: none; }
    .aside { border-top: 1px solid var(--rule); }
  }
</style>
