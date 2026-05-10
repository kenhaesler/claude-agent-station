<script lang="ts">
  import {
    getRunFullContext,
    getRunDiff,
    getCoordinatorMessages,
    pauseRun,
    resumeRun,
    stopRun,
    messageRun,
    operatorApproveRunPlan,
    operatorRejectRunPlan,
  } from '../lib/api';
  import {
    formatTokens,
    formatDuration,
    timeAgo,
    formatRunMode,
    formatSkipReason,
  } from '../lib/format';
  import { getAgentName, getAgentColor, agentPresence } from '../lib/agent-presence.svelte';
  import { addToast, toastSuccess, toastError } from '../lib/toast.svelte';
  import type {
    RunFullContext,
    DiffResult,
    CoordinatorMessage,
    CoordinatorTask,
  } from '../lib/types';
  import { navigate } from '../lib/router.svelte';
  import LogViewer from '../components/data-display/LogViewer.svelte';

  let { runId }: { runId: string } = $props();

  let ctx = $state<RunFullContext | null>(null);
  let diff = $state<DiffResult | null>(null);
  let allMessages = $state<CoordinatorMessage[]>([]);
  let loading = $state(true);
  let activeTab = $state<
    'overview' | 'dag' | 'team' | 'conversation' | 'diff' | 'logs' | 'intelligence'
  >('overview');
  let error = $state<string | null>(null);

  // Control state
  let pausing = $state(false);
  let stopping = $state(false);
  let approving = $state(false);
  let rejecting = $state(false);
  let messageText = $state('');
  let sendingMessage = $state(false);

  async function loadRun() {
    loading = true;
    error = null;
    try {
      ctx = await getRunFullContext(runId);
      // Load diff and messages in background
      getRunDiff(runId).then((d) => (diff = d)).catch(() => {});
      getCoordinatorMessages(runId)
        .then((m) => (allMessages = m))
        .catch(() => {});
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : 'Failed to load run';
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    runId;
    loadRun();
  });

  let run = $derived(ctx?.run);
  let isLive = $derived(run?.status === 'started' || run?.status === 'running');
  let runPaused = $derived(!!agentPresence.pausedRuns[runId]);
  let hasDag = $derived((ctx?.coordinator_tasks?.length ?? 0) > 0);
  let hasTeam = $derived(ctx?.team_summary != null);
  let hasIntelligence = $derived((ctx?.intelligence_decisions?.length ?? 0) > 0);
  let hasConversation = $derived(
    allMessages.length > 0 || (ctx?.coordinator_messages?.length ?? 0) > 0,
  );
  let conversationMessages = $derived(
    allMessages.length > 0 ? allMessages : (ctx?.coordinator_messages ?? []),
  );

  let hasDiff = $derived((diff?.files?.length ?? 0) > 0 || !!run?.branch);
  let hasLogs = $derived(!!run?.log_file);
  let planAwaitingApproval = $derived(
    run?.status === 'awaiting_plan_review' || run?.status === 'plan_reviewing',
  );

  type TabId =
    | 'overview'
    | 'dag'
    | 'team'
    | 'conversation'
    | 'diff'
    | 'logs'
    | 'intelligence';

  let tabs = $derived.by<{ id: TabId; label: string; count?: number; disabled?: boolean }[]>(() => {
    const t: { id: TabId; label: string; count?: number; disabled?: boolean }[] = [
      { id: 'overview', label: 'Overview' },
    ];
    t.push({
      id: 'dag',
      label: 'DAG',
      count: ctx?.coordinator_tasks?.length ?? 0,
      disabled: !hasDag,
    });
    t.push({
      id: 'team',
      label: 'Team',
      count: ctx?.team_summary?.teammates?.length,
      disabled: !hasTeam,
    });
    t.push({
      id: 'conversation',
      label: 'Conversation',
      count: conversationMessages.length || undefined,
      disabled: !hasConversation,
    });
    t.push({
      id: 'diff',
      label: 'Diff',
      count: diff?.files?.length,
      disabled: !hasDiff,
    });
    t.push({ id: 'logs', label: 'Logs', disabled: !hasLogs });
    t.push({
      id: 'intelligence',
      label: 'Intelligence',
      count: ctx?.intelligence_decisions?.length ?? 0,
      disabled: !hasIntelligence,
    });
    return t;
  });

  function getInitials(name: string): string {
    return name
      .split(' ')
      .map((w) => w[0])
      .join('')
      .slice(0, 2)
      .toUpperCase();
  }

  function parseReport(report: string | null): Record<string, unknown> | null {
    if (!report) return null;
    try {
      return JSON.parse(report) as Record<string, unknown>;
    } catch {
      return null;
    }
  }

  function shortRunId(id: string): string {
    if (id.length <= 12) return id;
    return id.slice(-12);
  }

  function shortTaskId(id: string): string {
    if (!id) return '—';
    return id.length <= 12 ? id : '…' + id.slice(-9);
  }

  function taskRoleFromTitle(t: CoordinatorTask): string {
    const s = (t.title ?? '').toLowerCase();
    if (s.includes('backend')) return 'backend';
    if (s.includes('frontend')) return 'frontend';
    if (s.includes('qa') || s.includes('test')) return 'qa';
    return 'lead';
  }

  function taskDuration(t: CoordinatorTask): string {
    if (!t.started_at) return '—';
    const start = new Date(
      t.started_at.endsWith('Z') ? t.started_at : t.started_at + 'Z',
    ).getTime();
    const end = t.finished_at
      ? new Date(
          t.finished_at.endsWith('Z') ? t.finished_at : t.finished_at + 'Z',
        ).getTime()
      : Date.now();
    return formatDuration(end - start);
  }

  function taskStatusClass(s: string): string {
    if (s === 'completed') return 'done';
    if (s === 'running') return 'run';
    if (s === 'failed') return 'planx';
    return 'idle';
  }

  function friendly(e: unknown, fallback: string): string {
    const raw = e instanceof Error ? e.message : fallback;
    return raw.startsWith('409:') ? raw.slice(4).trim() || fallback : raw;
  }

  async function onPauseRun() {
    if (!runId || pausing) return;
    pausing = true;
    try {
      if (runPaused) {
        await resumeRun(runId);
        addToast('success', `Resume requested for ${runId}`);
      } else {
        await pauseRun(runId);
        addToast('success', `Pause requested for ${runId}`);
      }
    } catch (e) {
      addToast('error', friendly(e, 'Pause/resume failed'));
    } finally {
      pausing = false;
    }
  }

  async function onStopRun() {
    if (!runId || stopping) return;
    const ok = confirm(
      `Stop run ${runId}? The agent will halt after its next tool call and finish with status=interrupted.`,
    );
    if (!ok) return;
    stopping = true;
    try {
      await stopRun(runId);
      addToast('success', `Stop requested for ${runId}`);
    } catch (e) {
      addToast('error', friendly(e, 'Stop failed'));
    } finally {
      stopping = false;
    }
  }

  async function onSendMessage() {
    const text = messageText.trim();
    if (!text || sendingMessage) return;
    sendingMessage = true;
    try {
      await messageRun(runId, text);
      messageText = '';
      toastSuccess('Message delivered');
    } catch (e) {
      toastError(friendly(e, 'Message failed'));
    } finally {
      sendingMessage = false;
    }
  }

  async function onApprovePlan() {
    if (approving) return;
    approving = true;
    try {
      await operatorApproveRunPlan(runId);
      await loadRun();
    } catch (e) {
      toastError(friendly(e, 'Approve failed'));
    } finally {
      approving = false;
    }
  }

  async function onRejectPlan() {
    if (rejecting) return;
    if (!confirm('Reject this plan? The run will end with verdict=REJECT.')) return;
    rejecting = true;
    try {
      await operatorRejectRunPlan(runId);
      await loadRun();
    } catch (e) {
      toastError(friendly(e, 'Reject failed'));
    } finally {
      rejecting = false;
    }
  }

  // Hotkeys 1..7 for tabs
  function onKey(e: KeyboardEvent) {
    const target = e.target as HTMLElement | null;
    const tag = target?.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA') return;
    const n = parseInt(e.key, 10);
    if (!Number.isNaN(n) && n >= 1 && n <= tabs.length) {
      const t = tabs[n - 1];
      if (!t.disabled) activeTab = t.id;
    }
  }
</script>

<svelte:window on:keydown={onKey} />

{#if loading}
  <div class="rd-loading animate-fade-in">
    <div class="skeleton h-8 w-64"></div>
    <div class="skeleton h-48 w-full"></div>
  </div>
{:else if error}
  <div class="rd-empty">
    <p class="rd-empty-msg">{error}</p>
    <button onclick={() => navigate('/runs')} class="rd-action">Back to Dispatch</button>
  </div>
{:else if run}
  <div class="rd animate-fade-in">
    <!-- ── Crumb ─────────────────────────────────────────── -->
    <div class="crumb">
      <a href="/runs" onclick={(e) => { e.preventDefault(); navigate('/runs'); }}>← Dispatch</a>
      <span class="sep">/</span>
      {#if ctx?.project_repo}
        <span>{ctx.project_repo}</span>
        <span class="sep">/</span>
      {/if}
      <b>{run.run_id}</b>
    </div>

    <!-- ── Page header ───────────────────────────────────── -->
    <div class="page-head">
      <div class="page-head-left">
        <h1>
          <span>{run.run_id}</span>
          <span class="badges">
            {#if isLive}
              <span class="status run"><span class="run-tick"></span><span class="lab">Run</span></span>
            {:else if run.verdict === 'APPROVE'}
              <span class="status planok">Approved</span>
            {:else if run.verdict === 'REJECT'}
              <span class="status planx">Rejected</span>
            {:else if run.verdict === 'PR'}
              <span class="status stop">PR</span>
            {:else if run.verdict === 'SKIP'}
              <span class="status idle">Skip</span>
            {:else if run.status === 'reviewing'}
              <span class="status stop">Reviewing</span>
            {:else}
              <span class="status done">{run.status}</span>
            {/if}
            {#if run.mode}
              {@const m = formatRunMode(run.mode)}
              <span class="mode {run.mode === 'full' ? 'full' : run.mode === 'plan_only' || run.mode === 'plan' ? 'plan' : (run.mode as string) === 'vision-bootstrap' ? 'vision' : ''}">
                {m.label}
              </span>
            {/if}
            {#if run.autonomy_level}
              <span class="aut {run.autonomy_level === 'auto' ? 'auto' : run.autonomy_level === 'manual' ? 'manual' : 'assist'}">
                {run.autonomy_level}
              </span>
            {/if}
            {#if runPaused}
              <span class="status stop">Paused</span>
            {/if}
          </span>
        </h1>
        <div class="meta">
          {#if ctx?.project_repo}
            <span>Project <b>{ctx.project_repo}</b></span>
          {/if}
          <span class="sep">·</span>
          <span>
            Issue
            {#if run.issue_number}<b>#{run.issue_number}</b>{:else}<b class="nu">— none —</b>{/if}
          </span>
          <span class="sep">·</span>
          <span>
            Branch
            {#if run.branch}<b>{run.branch}</b>{:else}<b class="nu">— none —</b>{/if}
          </span>
          {#if run.started_at}
            <span class="sep">·</span>
            <span>Started <b>{timeAgo(run.started_at)}</b></span>
          {/if}
          {#if run.skip_reason}
            <span class="sep">·</span>
            <span class="skip">{formatSkipReason(run.skip_reason)}</span>
          {/if}
        </div>
      </div>

      <!-- Run controls -->
      <div class="rd-controls">
        {#if isLive}
          <button class="rd-action" onclick={onPauseRun} disabled={pausing}
            title={runPaused ? 'Resume this run' : 'Route this run\'s next tool call to the tray'}>
            {#if pausing}…{:else if runPaused}▶ Resume{:else}⏸ Pause{/if}
          </button>
          <button class="rd-action danger" onclick={onStopRun} disabled={stopping}
            title="Interrupt this run cooperatively">
            {stopping ? '…' : '⏹ Stop'}
          </button>
        {/if}
        {#if planAwaitingApproval}
          <button class="rd-action go" onclick={onApprovePlan} disabled={approving}>
            {approving ? '…' : '✓ Approve plan'}
          </button>
          <button class="rd-action danger" onclick={onRejectPlan} disabled={rejecting}>
            {rejecting ? '…' : '✗ Reject plan'}
          </button>
        {/if}
      </div>
    </div>

    <!-- ── Quick stats ───────────────────────────────────── -->
    <div class="qstats">
      <div class="qstat">
        <span class="k">Tokens</span>
        {#if run.tokens_total}
          <span class="v go">{formatTokens(run.tokens_total)}</span>
          <span class="sub">{formatTokens(run.tokens_input)} in / {formatTokens(run.tokens_output)} out</span>
        {:else}
          <span class="v nu">—</span><span class="sub">no usage yet</span>
        {/if}
      </div>
      <div class="qstat">
        <span class="k">Turns</span>
        <span class="v">{run.turns ?? 0}</span>
        <span class="sub">{(run.turns ?? 0) === 0 ? 'no tool calls' : 'tool calls'}</span>
      </div>
      <div class="qstat">
        <span class="k">Cost</span>
        {#if run.cost_usd}
          <span class="v">${run.cost_usd.toFixed(2)}</span><span class="sub">USD</span>
        {:else}
          <span class="v nu">—</span><span class="sub">awaiting usage</span>
        {/if}
      </div>
      <div class="qstat">
        <span class="k">Tasks</span>
        {#if ctx?.team_summary}
          <span class="v">{ctx.team_summary.tasks_completed}<span class="vsub">/{ctx.team_summary.tasks_total}</span></span>
          <span class="sub">{ctx.team_summary.tasks_in_progress} active</span>
        {:else if hasDag}
          <span class="v">{ctx?.coordinator_tasks?.length ?? 0}</span><span class="sub">total</span>
        {:else}
          <span class="v nu">—</span><span class="sub">no DAG</span>
        {/if}
      </div>
      <div class="qstat">
        <span class="k">Files</span>
        {#if diff?.files?.length}
          <span class="v">{diff.files.length}</span>
          <span class="sub" style="color:var(--go)">+{diff.total_additions}</span>
        {:else}
          <span class="v nu">0</span><span class="sub">none touched</span>
        {/if}
      </div>
      <div class="qstat">
        <span class="k">Duration</span>
        {#if run.duration_ms}
          <span class="v">{formatDuration(run.duration_ms)}</span><span class="sub">total</span>
        {:else if run.started_at && isLive}
          <span class="v">{timeAgo(run.started_at).replace(' ago', '')}</span>
          <span class="sub">in flight</span>
        {:else}
          <span class="v nu">—</span><span class="sub">not started</span>
        {/if}
      </div>
      <div class="qstat">
        <span class="k">Verdict</span>
        {#if run.verdict}
          <span class="v {run.verdict === 'APPROVE' ? 'go' : run.verdict === 'REJECT' ? 'abort' : 'caution'}" style="font-size:14px">{run.verdict}</span>
          <span class="sub">final</span>
        {:else}
          <span class="v nu">—</span><span class="sub">in progress</span>
        {/if}
      </div>
      <div class="qstat">
        <span class="k">Budget</span>
        {#if run.max_budget_usd != null}
          <span class="v">≤ ${run.max_budget_usd.toFixed(2)}</span>
          <span class="sub">cap</span>
        {:else}
          <span class="v nu">—</span><span class="sub">no cap</span>
        {/if}
      </div>
    </div>

    <!-- ── Tabs ───────────────────────────────────────────── -->
    <div class="tabs">
      {#each tabs as tab}
        <button
          onclick={() => { if (!tab.disabled) activeTab = tab.id; }}
          class="tab"
          class:active={activeTab === tab.id}
          class:disabled={tab.disabled}
          disabled={tab.disabled}
          title={tab.disabled ? `No ${tab.label.toLowerCase()} data yet` : ''}
        >
          {tab.label}{#if tab.count != null}<span class="count">{tab.count}</span>{/if}
        </button>
      {/each}
    </div>

    <!-- ── Tab body ───────────────────────────────────────── -->
    <div class="tab-body">

      <!-- OVERVIEW -->
      {#if activeTab === 'overview'}
        <section class="tab-pane">
          {#if (run.mode as string) === 'vision-bootstrap'}
            <div class="card-block" style="margin-bottom: 16px" data-testid="vision-bootstrap-summary">
              <h3>Vision bootstrap</h3>
              <p>
                {#if run.status === 'completed'}
                  {run.vision_bootstrap_count ?? 0} issue{(run.vision_bootstrap_count ?? 0) === 1 ? '' : 's'} proposed.
                {:else if run.status === 'failed' || run.status === 'error'}
                  The vision analyst could not complete this run. Check the logs tab for the underlying error.
                {:else if (run.vision_bootstrap_count ?? 0) === 0 && run.status === 'completed'}
                  The analyst found no gaps to propose. The current repo state already covers the vision's near-term horizons.
                {:else if run.status === 'started' || run.status === 'running'}
                  Analyst is running — proposed issues will appear here once the run finishes.
                {/if}
              </p>
              {#if run.vision_bootstrap_proposals && run.vision_bootstrap_proposals.length > 0}
                <p style="margin-top: 6px">
                  These issues carry the <code>vision-suggested</code> label and are skipped by the orchestrator until you accept one — remove the label to allow autonomous implementation, or close to reject.
                </p>
                {#each run.vision_bootstrap_proposals as p}
                  <div class="mono-line">
                    <span class="nu">#{p.number}</span> &nbsp;
                    <a href={p.url} target="_blank" rel="noopener" style="color: var(--data)">{p.title}</a>
                  </div>
                {/each}
              {/if}
            </div>
          {/if}

          <div class="cards">
            <!-- Left: progress + recent activity -->
            <div class="card-block">
              <h3>
                {#if isLive}
                  In Progress · {ctx?.team_summary ? `Team ${ctx.team_summary.team_name}` : 'Single Run'}
                {:else if run.verdict}
                  Finished · Verdict {run.verdict}
                {:else}
                  Status · {run.status}
                {/if}
              </h3>

              {#if parseReport(run.employee_report)}
                {@const report = parseReport(run.employee_report)!}
                {#each Object.entries(report) as [key, val]}
                  <p>
                    <b style="font-family: var(--pro-sans); font-size: 9px; letter-spacing: 0.16em; text-transform: uppercase; color: var(--ash)">{key.replace(/_/g, ' ')}</b><br />
                    {#if typeof val === 'string'}
                      {val}
                    {:else if Array.isArray(val)}
                      {val.map((v) => (typeof v === 'string' ? v : JSON.stringify(v))).join(' · ')}
                    {:else}
                      {JSON.stringify(val)}
                    {/if}
                  </p>
                {/each}
              {:else if run.employee_report}
                <p>{run.employee_report}</p>
              {:else if isLive}
                <p>
                  Lead is coordinating
                  {#if ctx?.team_summary}<b>{ctx.team_summary.teammates.length} specialists</b> on team <b>{ctx.team_summary.team_name}</b>.{:else}the run.{/if}
                </p>
              {:else if run.status === 'reviewing'}
                <p>Run finished — awaiting manager review.</p>
              {:else}
                <p>This run did not produce a report.</p>
              {/if}

              {#if ctx?.team_summary && ctx.team_summary.tasks_total > 0}
                {@const pct =
                  (ctx.team_summary.tasks_completed / ctx.team_summary.tasks_total) * 100}
                <div class="progress"><span style="width: {pct}%"></span></div>
                <div class="progress-meta">
                  <span>
                    {ctx.team_summary.tasks_completed} done · {ctx.team_summary.tasks_in_progress} active
                    {#if ctx.team_summary.conflicts > 0} · <b style="color:var(--caution)">{ctx.team_summary.conflicts} conflicts</b>{/if}
                  </span>
                  <span>~{Math.round(pct)}% complete</span>
                </div>
              {/if}

              {#if run.verdict_detail}
                <h3 style="margin-top: 18px">Verdict Detail</h3>
                <p style="white-space: pre-wrap">{run.verdict_detail}</p>
              {/if}

              {#if conversationMessages.length > 0}
                <h3 style="margin-top: 18px">Recent Activity</h3>
                {#each conversationMessages.slice(-5).reverse() as msg}
                  {@const agentName = getAgentName(
                    msg.employee_index,
                    msg.direction === 'system' ? 'coordinator' : null,
                  )}
                  {@const color = getAgentColor(agentName.toLowerCase())}
                  <div class="mono-line">
                    <span class="nu">{timeAgo(msg.created_at)}</span> &nbsp;
                    <span style="color: {color}">{agentName}</span> &nbsp;
                    <span>{msg.message_type === 'guidance' ? '@' : msg.message_type === 'error' ? '!' : '→'}</span>
                    <span>{msg.content?.slice(0, 80) ?? ''}</span>
                  </div>
                {/each}
              {/if}

              <!-- Operator message input -->
              {#if isLive}
                <div class="rd-message-row">
                  <input
                    type="text"
                    class="rd-message-input"
                    placeholder="Send a message to the agent…"
                    bind:value={messageText}
                    onkeydown={(e) => { if (e.key === 'Enter') onSendMessage(); }}
                    disabled={sendingMessage}
                  />
                  <button class="rd-action" onclick={onSendMessage} disabled={sendingMessage || !messageText.trim()}>
                    {sendingMessage ? '…' : 'Send'}
                  </button>
                </div>
              {/if}
            </div>

            <!-- Right: metadata -->
            <div class="card-block">
              <h3>Run Metadata</h3>
              <div class="key-row"><label>Run ID</label><val>{run.run_id}</val></div>
              {#if run.trace_id}
                <div class="key-row"><label>Trace</label><val class="dim">{run.trace_id}</val></div>
              {/if}
              {#if run.concurrent_group_id}
                <div class="key-row"><label>Group</label><val class="dim">{run.concurrent_group_id}</val></div>
              {/if}
              {#if run.team_name}
                <div class="key-row"><label>Team</label><val>{run.team_name}</val></div>
              {/if}
              <div class="key-row"><label>Mode</label><val>{run.mode ?? '—'}</val></div>
              <div class="key-row"><label>Autonomy</label><val>{run.autonomy_level ?? 'assisted'}</val></div>
              <div class="key-row">
                <label>Status</label>
                <val style="color: {isLive ? 'var(--go)' : run.status === 'failed' ? 'var(--abort)' : 'var(--ink)'}">{run.status}</val>
              </div>
              <div class="key-row">
                <label>Verdict</label>
                <val class={run.verdict ? '' : 'dim'}>{run.verdict ?? '—'}</val>
              </div>
              <div class="key-row"><label>Model</label><val class={run.model ? '' : 'dim'}>{run.model ?? '— (defaults)'}</val></div>
              <div class="key-row"><label>Branch</label><val class={run.branch ? '' : 'dim'}>{run.branch ?? '—'}</val></div>
              <div class="key-row"><label>Issue</label><val class={run.issue_number ? '' : 'dim'}>{run.issue_number ? '#' + run.issue_number : '—'}</val></div>
              <div class="key-row"><label>Started</label><val>{run.started_at ? new Date(run.started_at + (run.started_at.endsWith('Z') ? '' : 'Z')).toLocaleString() : '—'}</val></div>
              <div class="key-row"><label>Finished</label>
                <val class={run.finished_at ? '' : 'dim'}>
                  {#if run.finished_at}{new Date(run.finished_at + (run.finished_at.endsWith('Z') ? '' : 'Z')).toLocaleString()}{:else}in flight{/if}
                </val>
              </div>
              {#if run.log_file}
                <div class="key-row"><label>Log</label><val style="color: var(--data); font-size: 10px; word-break: break-all">{run.log_file}</val></div>
              {/if}

              {#if ctx?.queue_items && ctx.queue_items.length > 0}
                <h3 style="margin-top: 14px">Queue Items ({ctx.queue_items.length})</h3>
                {#each ctx.queue_items as qi}
                  <div class="mono-line">
                    {#if qi.issue_number}<b>#{qi.issue_number}</b> {/if}
                    <span class="dim">[{qi.state}]</span>
                    {#if qi.issue_title} {qi.issue_title}{/if}
                  </div>
                {/each}
              {:else if ctx?.queue_item}
                <h3 style="margin-top: 14px">Queue Item</h3>
                <div class="mono-line">
                  <span class="dim">[{ctx.queue_item.state}]</span>
                  {#if ctx.queue_item.issue_title} {ctx.queue_item.issue_title}{/if}
                </div>
              {/if}
            </div>
          </div>
        </section>

      <!-- DAG -->
      {:else if activeTab === 'dag'}
        <section class="tab-pane">
          {#if hasDag}
            <div class="dag">
              {#each ctx?.coordinator_tasks ?? [] as task, i}
                {@const role = taskRoleFromTitle(task)}
                <div class="dag-row {task.status === 'running' ? 'run' : ''}">
                  <span class="ix">{String(i + 1).padStart(2, '0')}</span>
                  <span class="id">{shortTaskId(task.id)}</span>
                  <span class="title">
                    {task.title ?? task.id}
                    {#if task.description}<span style="color: var(--graphite)"> · {task.description}</span>{/if}
                    <span style="color: var(--role-{role}); font-family: var(--pro-mono); font-size: 10px; margin-left: 6px">[{role}]</span>
                  </span>
                  <span>
                    <span class="status {taskStatusClass(task.status)}">
                      {#if task.status === 'running'}<span class="run-tick"></span>{/if}
                      <span class="lab">{task.status}</span>
                    </span>
                  </span>
                  <span class="num {task.touched_files ? '' : 'nu'}">
                    {#if task.touched_files}
                      {(() => { try { return (JSON.parse(task.touched_files) as unknown[]).length + ' files'; } catch { return '— files'; } })()}
                    {:else}
                      — files
                    {/if}
                  </span>
                  <span class="num">{taskDuration(task)}</span>
                </div>
              {/each}
            </div>
          {:else}
            <div class="empty">No DAG tasks recorded for this run.</div>
          {/if}
        </section>

      <!-- TEAM -->
      {:else if activeTab === 'team'}
        <section class="tab-pane">
          {#if ctx?.team_summary}
            <div class="card-block">
              <h3>Team {ctx.team_summary.team_name}</h3>
              <p>
                <b>{ctx.team_summary.teammates.length}</b> agents linked. Lead coordinates
                {ctx.team_summary.teammates.length - 1} role-specialists.
                {ctx.team_summary.tasks_completed} of {ctx.team_summary.tasks_total} tasks complete;
                {ctx.team_summary.tasks_in_progress} in flight.
                {#if ctx.team_summary.conflicts > 0}<b style="color:var(--caution)">{ctx.team_summary.conflicts} conflicts.</b>{/if}
              </p>
              {#if ctx.team_summary.tasks_total > 0}
                {@const pct = (ctx.team_summary.tasks_completed / ctx.team_summary.tasks_total) * 100}
                <div class="progress"><span style="width: {pct}%"></span></div>
                <div class="progress-meta">
                  <span>{ctx.team_summary.tasks_completed} done · {ctx.team_summary.tasks_in_progress} active</span>
                  <span>~{Math.round(pct)}% complete</span>
                </div>
              {/if}
            </div>

            <div class="team-grid">
              {#each ctx.team_summary.teammates as member}
                {@const memberColor = getAgentColor(member.name.toLowerCase())}
                <div class="card-block">
                  <div class="team-head">
                    <div class="team-avatar" style="background: {memberColor}15; color: {memberColor}; border-color: {memberColor}">
                      {getInitials(member.name)}
                    </div>
                    <div>
                      <div class="team-name">{member.name}</div>
                      <div class="team-status">
                        <span class="run-tick" style="background: {member.status === 'completed' ? 'var(--graphite)' : member.status === 'stuck' ? 'var(--abort)' : 'var(--go)'}; animation: {member.status === 'completed' || member.status === 'stuck' ? 'none' : ''}"></span>
                        {member.status}
                      </div>
                    </div>
                  </div>
                  <div class="mono-line">
                    {#if member.turns_used}{member.turns_used} turns · {/if}
                    {#if member.tokens_used}{formatTokens(member.tokens_used)} tokens{/if}
                  </div>
                  {#if member.files_touched && member.files_touched.length > 0}
                    <div class="mono-line nu">Files touched:</div>
                    {#each member.files_touched.slice(0, 3) as file}
                      <div class="mono-line" style="color: var(--graphite)">{file}</div>
                    {/each}
                    {#if member.files_touched.length > 3}
                      <div class="mono-line nu">+{member.files_touched.length - 3} more</div>
                    {/if}
                  {/if}
                </div>
              {/each}
            </div>
          {:else}
            <div class="empty">No team data for this run.</div>
          {/if}
        </section>

      <!-- CONVERSATION -->
      {:else if activeTab === 'conversation'}
        <section class="tab-pane">
          {#if conversationMessages.length === 0}
            <div class="empty">No conversation data for this run</div>
          {:else}
            <div class="conv">
              {#each conversationMessages as msg}
                {@const agentName = getAgentName(
                  msg.employee_index,
                  msg.direction === 'system' ? 'coordinator' : null,
                )}
                {@const role = agentName.toLowerCase().includes('lead')
                  ? 'lead'
                  : agentName.toLowerCase().includes('back')
                    ? 'backend'
                    : agentName.toLowerCase().includes('front')
                      ? 'frontend'
                      : agentName.toLowerCase().includes('qa')
                        ? 'qa'
                        : 'operator'}
                {@const glyph =
                  msg.message_type === 'guidance'
                    ? '@'
                    : msg.message_type === 'error'
                      ? '!'
                      : msg.message_type === 'progress'
                        ? '→'
                        : msg.message_type === 'conflict'
                          ? '!'
                          : '>'}
                <div class="conv-row">
                  <span class="t">{timeAgo(msg.created_at)}</span>
                  <span class="agent" data-role={role}>{agentName}</span>
                  <span class="glyph">{glyph}</span>
                  <span class="body"><em>{msg.message_type}</em>{msg.content ?? ''}</span>
                </div>
              {/each}
            </div>
          {/if}
        </section>

      <!-- DIFF -->
      {:else if activeTab === 'diff'}
        <section class="tab-pane">
          {#if diff?.files && diff.files.length > 0}
            <div class="diff-stat">
              <span><b>{diff.files.length} files</b> changed</span>
              <span class="add">+{diff.total_additions}</span>
              <span class="del">−{diff.total_deletions}</span>
              {#if run.branch}<span class="dim">vs <code>{run.branch}</code></span>{/if}
            </div>
            {#each diff.files as file}
              <div class="diff-file">
                <div class="file-head">
                  <span class="path">{file.path}</span>
                  <span class="meta">
                    <span class="add">+{file.additions}</span>
                    &nbsp;
                    <span class="del">−{file.deletions}</span>
                  </span>
                </div>
                <div class="hunk">
                  {#each file.hunks as hunk}
                    <div class="hunk-line hd">
                      @@ -{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count} @@
                    </div>
                    {#each hunk.lines as line}
                      <div class="hunk-line {line.type === 'add' ? 'add' : line.type === 'delete' ? 'del' : ''}">
                        <span class="ln">{line.old_line ?? ''}</span>
                        <span class="ln">{line.new_line ?? ''}</span>
                        <span class="sg">{line.type === 'add' ? '+' : line.type === 'delete' ? '−' : ''}</span>
                        <span class="src">{line.content}</span>
                      </div>
                    {/each}
                  {/each}
                </div>
              </div>
            {/each}
          {:else}
            <div class="empty">
              {#if isLive}
                Diff will be available after the run completes.
              {:else if !run.branch}
                No branch recorded for this run.
              {:else}
                No code changes detected.
              {/if}
            </div>
          {/if}
        </section>

      <!-- LOGS -->
      {:else if activeTab === 'logs'}
        <section class="tab-pane">
          {#if run.log_file}
            <div class="card-block" style="margin-bottom: 12px">
              <div style="display: flex; justify-content: space-between; align-items: center; gap: 14px">
                <span class="mono-line" style="margin: 0">{run.log_file}</span>
                <span style="font-family: var(--pro-mono); font-size: 11px; color: var(--graphite)">tail -F</span>
              </div>
            </div>
            <LogViewer runId={run.run_id} logFile={run.log_file ?? null} />
          {:else}
            <div class="empty">No log file recorded for this run.</div>
          {/if}
        </section>

      <!-- INTELLIGENCE -->
      {:else if activeTab === 'intelligence'}
        <section class="tab-pane">
          {#if hasIntelligence}
            <div class="conv">
              {#each ctx?.intelligence_decisions ?? [] as event}
                {#if event.event_type === 'vision_misalignment'}
                  <div class="card-block" style="border-left: 3px solid var(--caution); margin-bottom: 8px">
                    <h3 style="color: var(--caution)">⚠ Vision misalignment — issue #{event.event_data?.issue_number}</h3>
                    <p>Violated: <code>{event.event_data?.violated_section}</code></p>
                    <p style="font-style: italic; color: var(--graphite); border-left: 2px solid var(--rule); padding-left: 8px">"{event.event_data?.quote}"</p>
                    <p class="mono-line nu">{timeAgo(event.created_at)}</p>
                  </div>
                {:else}
                  <div class="card-block" style="margin-bottom: 6px">
                    <div class="mono-line" style="display: flex; justify-content: space-between">
                      <span style="color: var(--data)">{event.event_type}</span>
                      <span class="nu">{timeAgo(event.created_at)}</span>
                    </div>
                    <pre style="font-family: var(--pro-mono); font-size: 11px; color: var(--graphite); margin: 4px 0; overflow-x: auto">{JSON.stringify(event.event_data, null, 2)}</pre>
                  </div>
                {/if}
              {/each}
            </div>
          {:else}
            <div class="empty">No autonomy decisions recorded for this run.</div>
          {/if}
        </section>
      {/if}
    </div>
  </div>
{/if}

<style>
  /* Edge-to-edge layout — bypass Shell padding (which Shell doesn't apply
     anyway, so we just use full width). */

  .rd {
    display: flex;
    flex-direction: column;
    min-height: 0;
  }

  .rd-loading,
  .rd-empty {
    padding: 32px 16px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    align-items: center;
  }

  .rd-empty-msg {
    font-family: var(--pro-mono);
    font-size: 13px;
    color: var(--abort);
  }

  /* Crumb */
  .rd :global(.crumb) {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 6px 16px;
    border-bottom: 1px solid var(--rule);
    background: var(--paper);
    flex-shrink: 0;
    font-family: var(--pro-mono);
    font-size: 11px;
    color: var(--graphite);
  }
  .rd :global(.crumb a) {
    color: var(--graphite);
    text-decoration: none;
  }
  .rd :global(.crumb a:hover) {
    color: var(--ink);
  }
  .rd :global(.crumb b) {
    color: var(--ink);
    font-weight: 500;
  }
  .rd :global(.crumb .sep) {
    color: var(--ash);
  }

  /* Page header */
  .rd :global(.page-head) {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 18px;
    align-items: end;
    padding: 16px 16px 12px;
    border-bottom: 1px solid var(--rule);
    flex-shrink: 0;
  }
  .rd :global(.page-head h1) {
    margin: 0;
    font-family: var(--pro-mono);
    font-size: 22px;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: var(--ink);
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    word-break: break-all;
  }
  .rd :global(.page-head h1 .badges) {
    display: inline-flex;
    gap: 6px;
    flex-wrap: wrap;
  }
  .rd :global(.page-head .meta) {
    font-family: var(--pro-mono);
    font-size: 11px;
    color: var(--graphite);
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 6px;
  }
  .rd :global(.page-head .meta b) {
    color: var(--ink);
    font-weight: 500;
  }
  .rd :global(.page-head .meta b.nu) {
    color: var(--ash);
    font-weight: 400;
  }
  .rd :global(.page-head .meta .sep) {
    color: var(--ash);
  }
  .rd :global(.page-head .meta .skip) {
    color: var(--caution);
    font-style: italic;
  }

  /* Controls */
  .rd :global(.rd-controls) {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    justify-self: end;
  }
  .rd :global(.rd-action) {
    font-family: var(--pro-sans);
    font-weight: 700;
    font-size: 10px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    background: transparent;
    color: var(--ink);
    border: 1px solid var(--rule-2);
    padding: 5px 10px;
    cursor: pointer;
    height: 26px;
    line-height: 1;
  }
  .rd :global(.rd-action:hover:not(:disabled)) {
    background: var(--paper-2);
  }
  .rd :global(.rd-action:disabled) {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .rd :global(.rd-action.danger) {
    color: var(--abort);
    border-color: color-mix(in oklab, var(--abort) 50%, transparent);
  }
  .rd :global(.rd-action.danger:hover:not(:disabled)) {
    background: color-mix(in oklab, var(--abort) 10%, var(--paper));
  }
  .rd :global(.rd-action.go) {
    color: var(--go);
    border-color: color-mix(in oklab, var(--go) 50%, transparent);
  }
  .rd :global(.rd-action.go:hover:not(:disabled)) {
    background: color-mix(in oklab, var(--go) 10%, var(--paper));
  }

  /* Quick stats */
  .rd :global(.qstats) {
    display: grid;
    grid-template-columns: repeat(8, 1fr);
    border-bottom: 1px solid var(--rule);
  }
  .rd :global(.qstat) {
    padding: 10px 14px;
    border-right: 1px solid var(--rule);
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }
  .rd :global(.qstat:last-child) {
    border-right: none;
  }
  .rd :global(.qstat .k) {
    font-family: var(--pro-sans);
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--graphite);
  }
  .rd :global(.qstat .v) {
    font-family: var(--pro-mono);
    font-size: 18px;
    font-weight: 600;
    color: var(--ink);
    line-height: 1;
    font-variant-numeric: tabular-nums;
  }
  .rd :global(.qstat .v.go) {
    color: var(--go);
  }
  .rd :global(.qstat .v.caution) {
    color: var(--caution);
  }
  .rd :global(.qstat .v.abort) {
    color: var(--abort);
  }
  .rd :global(.qstat .v.nu) {
    color: var(--ash);
  }
  .rd :global(.qstat .vsub) {
    font-size: 0.55em;
    color: var(--ash);
    margin-left: 4px;
  }
  .rd :global(.qstat .sub) {
    font-family: var(--pro-mono);
    font-size: 9px;
    color: var(--ash);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* Tabs */
  .rd :global(.tabs) {
    display: flex;
    gap: 0;
    align-items: center;
    border-bottom: 1px solid var(--rule);
    background: var(--paper);
    padding: 0 16px;
    flex-shrink: 0;
    overflow-x: auto;
  }
  .rd :global(.tab) {
    font-family: var(--pro-sans);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: var(--graphite);
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 10px 14px;
    cursor: pointer;
    white-space: nowrap;
  }
  .rd :global(.tab.active) {
    color: var(--ink);
    border-bottom-color: var(--ink);
  }
  .rd :global(.tab:hover:not(.disabled)) {
    color: var(--ink);
  }
  .rd :global(.tab .count) {
    font-family: var(--pro-mono);
    font-size: 10px;
    color: var(--ash);
    margin-left: 4px;
    font-weight: 500;
  }
  .rd :global(.tab.active .count) {
    color: var(--graphite);
  }
  .rd :global(.tab.disabled) {
    color: var(--ash);
    cursor: not-allowed;
  }

  .rd :global(.tab-body) {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
  }
  .rd :global(.tab-pane) {
    padding: 16px;
  }

  /* Cards */
  .rd :global(.cards) {
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 16px;
  }
  .rd :global(.card-block) {
    background: var(--paper-2);
    border: 1px solid var(--rule);
    padding: 14px 16px;
  }
  .rd :global(.card-block h3) {
    margin: 0 0 8px;
    font-family: var(--pro-sans);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--ash);
    border-bottom: 1px solid var(--rule);
    padding-bottom: 6px;
  }
  .rd :global(.card-block p) {
    margin: 6px 0;
    font-family: var(--pro-sans);
    font-size: 13px;
    line-height: 1.5;
    color: var(--ink);
  }
  .rd :global(.card-block .mono-line) {
    font-family: var(--pro-mono);
    font-size: 11px;
    color: var(--graphite);
    margin: 4px 0;
  }
  .rd :global(.card-block .mono-line.nu) {
    color: var(--ash);
  }
  .rd :global(.card-block .key-row) {
    display: grid;
    grid-template-columns: 90px 1fr;
    gap: 8px;
    padding: 3px 0;
    border-bottom: 1px dashed var(--rule);
    font-family: var(--pro-mono);
    font-size: 11px;
  }
  .rd :global(.card-block .key-row:last-child) {
    border-bottom: none;
  }
  .rd :global(.card-block .key-row label) {
    color: var(--ash);
    font-family: var(--pro-sans);
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    align-self: center;
  }
  .rd :global(.card-block .key-row val) {
    color: var(--ink);
    word-break: break-all;
  }
  .rd :global(.card-block .key-row val.dim) {
    color: var(--ash);
  }

  /* Progress */
  .rd :global(.progress) {
    height: 6px;
    background: var(--paper-3);
    position: relative;
    margin: 4px 0;
  }
  .rd :global(.progress > span) {
    position: absolute;
    left: 0;
    top: 0;
    bottom: 0;
    background: var(--go);
  }
  .rd :global(.progress-meta) {
    font-family: var(--pro-mono);
    font-size: 10px;
    color: var(--ash);
    display: flex;
    justify-content: space-between;
    margin-top: 2px;
  }

  /* Message input */
  .rd :global(.rd-message-row) {
    display: flex;
    gap: 6px;
    margin-top: 14px;
    padding-top: 12px;
    border-top: 1px dashed var(--rule);
  }
  .rd :global(.rd-message-input) {
    flex: 1;
    font-family: var(--pro-mono);
    font-size: 12px;
    background: var(--paper);
    color: var(--ink);
    border: 1px solid var(--rule-2);
    padding: 5px 8px;
    height: 26px;
  }
  .rd :global(.rd-message-input:focus) {
    outline: none;
    border-color: var(--data);
  }

  /* DAG */
  .rd :global(.dag) {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .rd :global(.dag-row) {
    display: grid;
    grid-template-columns: 24px 110px 1fr 80px 90px 70px;
    gap: 14px;
    align-items: center;
    padding: 10px 14px;
    border: 1px solid var(--rule);
    background: var(--paper-2);
    font-family: var(--pro-mono);
    font-size: 12px;
  }
  .rd :global(.dag-row .ix) {
    color: var(--ash);
    font-size: 11px;
  }
  .rd :global(.dag-row .id) {
    color: var(--graphite);
  }
  .rd :global(.dag-row .title) {
    font-family: var(--pro-sans);
    font-size: 13px;
    font-weight: 500;
    color: var(--ink);
  }
  .rd :global(.dag-row .num) {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .rd :global(.dag-row .num.nu) {
    color: var(--ash);
  }
  .rd :global(.dag-row.run) {
    border-left: 3px solid var(--go);
  }

  /* Conversation */
  .rd :global(.conv) {
    display: flex;
    flex-direction: column;
  }
  .rd :global(.conv-row) {
    display: grid;
    grid-template-columns: 78px 110px 16px 1fr;
    gap: 8px;
    padding: 4px 14px;
    align-items: baseline;
    font-family: var(--pro-mono);
    font-size: 12px;
  }
  .rd :global(.conv-row + .conv-row) {
    border-top: 1px dashed var(--rule);
  }
  .rd :global(.conv-row .t) {
    color: var(--ash);
    font-size: 11px;
  }
  .rd :global(.conv-row .agent) {
    color: var(--ink);
    font-weight: 500;
  }
  .rd :global(.conv-row .agent[data-role="lead"]) {
    color: var(--role-lead);
  }
  .rd :global(.conv-row .agent[data-role="backend"]) {
    color: var(--role-backend);
  }
  .rd :global(.conv-row .agent[data-role="frontend"]) {
    color: var(--role-frontend);
  }
  .rd :global(.conv-row .agent[data-role="qa"]) {
    color: var(--role-qa);
  }
  .rd :global(.conv-row .agent[data-role="operator"]) {
    color: var(--role-operator);
  }
  .rd :global(.conv-row .glyph) {
    color: var(--graphite);
  }
  .rd :global(.conv-row .body) {
    color: var(--ink);
    word-break: break-word;
  }
  .rd :global(.conv-row .body em) {
    font-style: normal;
    color: var(--data);
    margin-right: 5px;
  }

  /* Team grid */
  .rd :global(.team-grid) {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 12px;
    margin-top: 12px;
  }
  .rd :global(.team-head) {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
  }
  .rd :global(.team-avatar) {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    border: 2px solid;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: var(--pro-mono);
    font-size: 10px;
    font-weight: 700;
    flex-shrink: 0;
  }
  .rd :global(.team-name) {
    font-family: var(--pro-sans);
    font-size: 13px;
    font-weight: 500;
    color: var(--ink);
  }
  .rd :global(.team-status) {
    font-family: var(--pro-mono);
    font-size: 11px;
    color: var(--graphite);
    text-transform: capitalize;
  }

  /* Diff */
  .rd :global(.diff-stat) {
    display: flex;
    gap: 14px;
    padding: 10px 14px;
    border: 1px solid var(--rule);
    background: var(--paper-2);
    font-family: var(--pro-mono);
    font-size: 12px;
    margin-bottom: 12px;
    color: var(--graphite);
  }
  .rd :global(.diff-stat b) {
    color: var(--ink);
    font-weight: 500;
  }
  .rd :global(.diff-stat .add) {
    color: var(--go);
  }
  .rd :global(.diff-stat .del) {
    color: var(--abort);
  }
  .rd :global(.diff-stat .dim) {
    color: var(--ash);
  }
  .rd :global(.diff-file) {
    border: 1px solid var(--rule);
    background: var(--paper-2);
    margin-bottom: 8px;
  }
  .rd :global(.diff-file .file-head) {
    font-family: var(--pro-mono);
    font-size: 12px;
    padding: 8px 12px;
    border-bottom: 1px solid var(--rule);
    display: flex;
    justify-content: space-between;
    gap: 14px;
    background: var(--paper-3);
  }
  .rd :global(.diff-file .file-head .path) {
    color: var(--ink);
    word-break: break-all;
  }
  .rd :global(.diff-file .file-head .meta) {
    color: var(--graphite);
    flex-shrink: 0;
  }
  .rd :global(.diff-file .file-head .add) {
    color: var(--go);
  }
  .rd :global(.diff-file .file-head .del) {
    color: var(--abort);
  }
  .rd :global(.diff-file .hunk) {
    padding: 8px 0;
    font-family: var(--pro-mono);
    font-size: 11px;
    line-height: 1.45;
    overflow-x: auto;
  }
  .rd :global(.diff-file .hunk-line) {
    display: grid;
    grid-template-columns: 36px 36px 16px 1fr;
    gap: 0;
  }
  .rd :global(.diff-file .hunk-line .ln) {
    color: var(--ash);
    padding: 0 8px;
    text-align: right;
    user-select: none;
  }
  .rd :global(.diff-file .hunk-line .sg) {
    padding: 0 4px;
    text-align: center;
    user-select: none;
  }
  .rd :global(.diff-file .hunk-line .src) {
    padding: 0 8px;
    white-space: pre;
    color: var(--ink);
  }
  .rd :global(.diff-file .hunk-line.add) {
    background: color-mix(in oklab, var(--go) 9%, transparent);
  }
  .rd :global(.diff-file .hunk-line.add .sg) {
    color: var(--go);
  }
  .rd :global(.diff-file .hunk-line.del) {
    background: color-mix(in oklab, var(--abort) 8%, transparent);
  }
  .rd :global(.diff-file .hunk-line.del .sg) {
    color: var(--abort);
  }
  .rd :global(.diff-file .hunk-line.hd) {
    color: var(--graphite);
    padding: 0 8px;
    font-style: italic;
    grid-column: 1 / -1;
  }

  /* Empty state */
  .rd :global(.empty) {
    font-family: var(--pro-mono);
    font-size: 12px;
    color: var(--ash);
    padding: 32px 14px;
    text-align: center;
    border: 1px dashed var(--rule);
  }

  @media (max-width: 1180px) {
    .rd :global(.qstats) {
      grid-template-columns: repeat(4, 1fr);
    }
    .rd :global(.cards) {
      grid-template-columns: 1fr;
    }
  }
  @media (max-width: 760px) {
    .rd :global(.qstats) {
      grid-template-columns: repeat(2, 1fr);
    }
    .rd :global(.dag-row) {
      grid-template-columns: 24px 1fr 80px;
    }
    .rd :global(.dag-row .id),
    .rd :global(.dag-row .num) {
      display: none;
    }
  }
</style>
