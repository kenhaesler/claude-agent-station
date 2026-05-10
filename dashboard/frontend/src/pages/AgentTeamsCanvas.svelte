<script lang="ts">
  import { agentPresence } from '../lib/agent-presence.svelte';
  import {
    getCoordinatorTasks,
    getCoordinatorMessages,
    triggerRun,
    getActiveEmployees,
    operatorApproveRunPlan,
    operatorRejectRunPlan,
  } from '../lib/api';
  import { addToast } from '../lib/toast.svelte';
  import { formatTokens, timeAgo } from '../lib/format';
  import { flap } from '../lib/design/flap';
  import type { CoordinatorTask, CoordinatorMessage, ActiveEmployee } from '../lib/types';

  let tasks = $state<CoordinatorTask[]>([]);
  let messages = $state<CoordinatorMessage[]>([]);
  let employees = $state<ActiveEmployee[]>([]);
  let triggering = $state(false);

  let latestRunId = $derived(agentPresence.latestRunId);
  let agents = $derived(agentPresence.agents);
  let isActive = $derived(agents.length > 0 || employees.length > 0 || tasks.length > 0);

  // Issue #266: surface the plan-review gate so the team canvas shows
  // when a plan_only run is awaiting manager review.
  let activeRun = $derived(
    agentPresence.activeRuns.find((r) => r.run_id === latestRunId) ?? null,
  );
  let planReviewStatus = $derived<string | null>(
    activeRun &&
      (activeRun.status === 'awaiting_plan_review' ||
        activeRun.status === 'plan_approved' ||
        activeRun.status === 'plan_rejected' ||
        activeRun.status === 'plan_reviewing')
      ? (activeRun.status as string)
      : null,
  );

  let planActionInFlight = $state<'approve' | 'reject' | null>(null);

  async function approveCanvasRunPlan() {
    if (!latestRunId || planActionInFlight) return;
    planActionInFlight = 'approve';
    try {
      const result = await operatorApproveRunPlan(latestRunId);
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

  async function rejectCanvasRunPlan() {
    if (!latestRunId || planActionInFlight) return;
    planActionInFlight = 'reject';
    try {
      await operatorRejectRunPlan(latestRunId);
      addToast('success', 'Plan rejected');
    } catch {
      // toast already shown
    } finally {
      planActionInFlight = null;
    }
  }

  // Fetch coordinator data
  $effect(() => {
    loadEmployees();
    if (!latestRunId) return;
    loadData(latestRunId);
    const interval = setInterval(() => {
      loadEmployees();
      if (latestRunId) loadData(latestRunId);
    }, 5000);
    return () => clearInterval(interval);
  });

  async function loadEmployees() {
    try { employees = await getActiveEmployees(); } catch { /* silent */ }
  }

  async function loadData(runId: string) {
    try {
      const [t, m] = await Promise.allSettled([
        getCoordinatorTasks(runId),
        getCoordinatorMessages(runId),
      ]);
      if (t.status === 'fulfilled') tasks = t.value;
      if (m.status === 'fulfilled') messages = m.value;
    } catch { /* silent */ }
  }

  async function handleTrigger() {
    if (triggering) return;
    triggering = true;
    try {
      await triggerRun();
      addToast('success', 'Run triggered');
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Trigger failed';
      addToast('error', msg);
    } finally {
      triggering = false;
    }
  }

  // ── Helpers ──────────────────────────────────────────────
  // Active Employees endpoint sometimes carries the freshest model + token
  // counts per teammate keyed off the run + employee_index. Treat it as a
  // soft enhancement: missing rows just mean the dashboard renders
  // CoordinatorTask data alone.
  function empFor(t: CoordinatorTask): ActiveEmployee | undefined {
    return employees.find(
      (e) => e.run_id === t.run_id && e.employee_index === t.employee_index,
    );
  }

  // Match the backend role-tag substring inference (see runs.py:_ROLE_TAGS).
  // Same brittleness caveat: a teammate named `ui-spright` won't match
  // `frontend`. TODO(#311): role becomes a first-class column.
  const ROLE_TAGS = ['backend', 'frontend', 'qa'] as const;
  type RoleTag = (typeof ROLE_TAGS)[number];

  function roleFor(t: CoordinatorTask): RoleTag | 'other' {
    const name = (t.claimed_by ?? t.teammate_agent_id ?? '').toLowerCase();
    for (const tag of ROLE_TAGS) {
      if (name.includes(tag)) return tag;
    }
    return 'other';
  }

  // Status mapping for the cell border accent.
  type StatusKind = 'run' | 'idle' | 'stop' | 'abort' | 'done';
  function statusKind(t: CoordinatorTask): StatusKind {
    const s = (t.status ?? '').toLowerCase();
    if (s === 'running') return 'run';
    if (s === 'completed') return 'done';
    if (s === 'blocked') return 'stop';
    if (s === 'failed') return 'abort';
    return 'idle';
  }

  function statusLabel(t: CoordinatorTask): string {
    const s = (t.status ?? '').toLowerCase();
    if (s === 'running') return 'RUN';
    if (s === 'completed') return 'DONE';
    if (s === 'blocked') return 'STOP';
    if (s === 'failed') return 'FAIL';
    if (s === 'pending') return 'IDLE';
    if (s === 'ready') return 'IDLE';
    return (s || 'idle').toUpperCase().slice(0, 6);
  }

  function teamSlug(runId: string | null | undefined): string {
    if (!runId) return '—';
    return runId.replace(/^run-(vb-)?/, '').slice(0, 14) + '…';
  }

  function shortRunId(runId: string | null | undefined): string {
    if (!runId) return '—';
    return runId.replace(/^run-(vb-)?/, '…');
  }

  // Pull a short string out of a coordinator message's `content` blob.
  function stripMd(s: string | null | undefined): string {
    if (!s) return '';
    return s
      .replace(/\*\*([^*]+)\*\*/g, '$1')   // **bold** → bold
      .replace(/\*([^*]+)\*/g, '$1')        // *em* → em
      .replace(/`([^`]+)`/g, '$1');         // `code` → code
  }

  function messagePreview(raw: string | null): { verb: string; body: string } {
    if (!raw) return { verb: '', body: '—' };
    if (typeof raw !== 'string') {
      try {
        const obj = raw as Record<string, unknown>;
        if (obj.tool) return { verb: 'Use', body: `${obj.tool}` };
        return { verb: '', body: JSON.stringify(raw).slice(0, 80) };
      } catch {
        return { verb: '', body: String(raw).slice(0, 80) };
      }
    }
    try {
      const parsed = JSON.parse(raw) as Record<string, unknown>;
      if (parsed.tool) return { verb: 'Use', body: `${parsed.tool}` };
      if (typeof parsed.text === 'string') return { verb: '', body: stripMd(parsed.text).slice(0, 80) };
      return { verb: '', body: stripMd(raw).slice(0, 80) };
    } catch {
      return { verb: '', body: stripMd(raw).slice(0, 80) };
    }
  }

  // ── Derived: per-role teammate cells ────────────────────
  type TeammateCell = {
    role: RoleTag | 'other';
    name: string;
    model: string;
    task: string;
    taskIsNul: boolean;
    statusKind: StatusKind;
    statusLabel: string;
    tokens: string;
    turns: string;
    files: string;
    duration: string;
    aut: string;
    latest?: { dir: 'down' | 'up' | 'peer'; from: string; to: string; verb: string; body: string };
  };

  function durationFor(t: CoordinatorTask, e?: ActiveEmployee): string {
    const start = t.started_at ?? e?.started_at ?? t.created_at;
    if (!start) return '—';
    return timeAgo(start).replace(' ago', '');
  }

  let teammateCells = $derived.by<Record<RoleTag, TeammateCell | null>>(() => {
    const out: Record<RoleTag, TeammateCell | null> = {
      backend: null,
      frontend: null,
      qa: null,
    };
    for (const t of tasks) {
      const role = roleFor(t);
      if (role === 'other') continue;
      // Prefer the most recent / running task per role; first-write-wins
      // otherwise so we don't churn the cell as polling refreshes.
      if (out[role] && out[role]!.statusKind === 'run') continue;
      const e = empFor(t);
      const name = t.claimed_by ?? t.teammate_agent_id ?? `${role}-?`;
      const taskTitle = stripMd(t.title)?.trim();
      const taskIsNul = !taskTitle;
      const taskMessages = messages.filter((m) => m.task_id === t.id).slice(-1);
      const latest = taskMessages.length
        ? (() => {
            const m = taskMessages[0];
            const p = messagePreview(m.content);
            const dir: 'down' | 'up' | 'peer' =
              m.direction === 'to_employee' ? 'down' : m.direction === 'from_monitor' ? 'up' : 'peer';
            return {
              dir,
              from: dir === 'down' ? 'lead' : name,
              to: dir === 'down' ? name : 'lead',
              verb: p.verb || (m.message_type ?? 'msg'),
              body: p.body,
            };
          })()
        : undefined;

      let files = '0';
      try {
        if (typeof t.touched_files === 'string') {
          const parsed = JSON.parse(t.touched_files);
          if (Array.isArray(parsed)) files = String(parsed.length);
          else if (parsed?.turns != null) files = String(parsed.turns);
        }
      } catch { /* ignore */ }

      out[role] = {
        role,
        name,
        model: (e?.model ?? 'claude-opus-4-7').toUpperCase().replace('CLAUDE-', ''),
        task: taskIsNul ? 'scope: untitled / no issue' : taskTitle ?? '',
        taskIsNul,
        statusKind: statusKind(t),
        statusLabel: statusLabel(t),
        tokens: formatTokens(e?.tokens_total ?? null),
        turns: e?.turns != null ? String(e.turns) : (t.status === 'completed' ? '—' : '0'),
        files,
        duration: durationFor(t, e),
        aut: 'ASSIST',
        latest,
      };
    }
    return out;
  });

  // ── Lead summary ────────────────────────────────────────
  let completedCount = $derived(tasks.filter((t) => t.status === 'completed').length);
  let runningCount = $derived(tasks.filter((t) => t.status === 'running').length);
  let leadActivity = $derived(
    runningCount > 0
      ? `Coordinating ${runningCount} specialist${runningCount === 1 ? '' : 's'} across team ${teamSlug(latestRunId)}`
      : tasks.length > 0
        ? `Monitoring ${tasks.length} task${tasks.length === 1 ? '' : 's'} on team ${teamSlug(latestRunId)}`
        : `Team ${teamSlug(latestRunId)} on standby`,
  );
  let totalTokens = $derived(
    employees.reduce((s, e) => s + (e.tokens_total ?? 0), 0),
  );
  let leadElapsed = $derived(
    employees[0]?.started_at ? timeAgo(employees[0].started_at).replace(' ago', '') : '—',
  );
  let leadStatusKind = $derived<StatusKind>(
    runningCount > 0 ? 'run' : tasks.length > 0 ? 'idle' : 'idle',
  );

  // ── Right rail: messages feed (newest first) ────────────
  type MsgRow = { dir: 'down' | 'up' | 'peer'; from: string; to: string; verb: string; body: string; t: string };
  let msgRows = $derived<MsgRow[]>(
    messages
      .slice()
      .reverse()
      .slice(0, 30)
      .map((m) => {
        const p = messagePreview(m.content);
        const dir: 'down' | 'up' | 'peer' =
          m.direction === 'to_employee' ? 'down' : m.direction === 'from_monitor' ? 'up' : 'peer';
        const taskName = (() => {
          const tk = tasks.find((x) => x.id === m.task_id);
          return tk?.claimed_by ?? tk?.teammate_agent_id ?? 'teammate';
        })();
        return {
          dir,
          from: dir === 'down' ? 'lead' : taskName,
          to: dir === 'down' ? taskName : 'lead',
          verb: p.verb || (m.message_type ?? 'msg').toString(),
          body: p.body,
          t: timeAgo(m.created_at).replace(' ago', ''),
        };
      }),
  );

  // Run mode (best-effort) for the page header meta. Project repo is not
  // resolved here — the lead summary shows team slug instead, and the
  // station footer carries the live project context.
  let modeLabel = $derived<string>(
    activeRun?.mode ? activeRun.mode.toUpperCase() : '—',
  );
</script>

<div class="fleet-pro animate-fade-in">
  {#if !isActive && tasks.length === 0}
    <!-- Idle state — keep "off-duty" heading text for the e2e check. -->
    <div class="page-head">
      <h1>Fleet <span class="br">·</span> <span class="team">No team</span></h1>
      <div class="meta">
        <span>Run <b>—</b></span><span>·</span>
        <span>Mode <b>—</b></span><span>·</span>
        <span>Aut <b>—</b></span>
      </div>
    </div>

    <div class="idle-wrap">
      <div class="idle-mark" aria-hidden="true">◆</div>
      <h2>The Team is Off-Duty</h2>
      <p>
        When an agent team is running, this becomes mission control —
        teammates, tasks, and lead/peer messaging at a glance.
      </p>
      <button class="trigger" onclick={handleTrigger} disabled={triggering}>
        {triggering ? 'Triggering…' : '▶ Trigger a Run'}
      </button>
    </div>
  {:else}
    {#if planReviewStatus}
      <div
        class="plan-banner {planReviewStatus}"
        data-testid="plan-review-banner"
      >
        <span class="msg">
          {#if planReviewStatus === 'awaiting_plan_review'}
            <b>Plan awaiting review.</b> Approve to enqueue a follow-up <code>full</code> run, or reject to stop here.
          {:else if planReviewStatus === 'plan_reviewing'}
            <b>Manager reviewing plan…</b>
          {:else if planReviewStatus === 'plan_approved'}
            <b>Plan approved.</b> A follow-up full run has been enqueued.
          {:else if planReviewStatus === 'plan_rejected'}
            <b>Plan rejected.</b> No follow-up run will be queued.
          {/if}
        </span>
        {#if planReviewStatus === 'awaiting_plan_review'}
          <span class="actions">
            <button
              type="button"
              class="b approve"
              onclick={approveCanvasRunPlan}
              disabled={planActionInFlight !== null}
              data-testid="plan-review-approve-btn"
            >
              {planActionInFlight === 'approve' ? 'Approving…' : 'Approve'}
            </button>
            <button
              type="button"
              class="b reject"
              onclick={rejectCanvasRunPlan}
              disabled={planActionInFlight !== null}
              data-testid="plan-review-reject-btn"
            >
              {planActionInFlight === 'reject' ? 'Rejecting…' : 'Reject'}
            </button>
          </span>
        {/if}
      </div>
    {/if}

    <!-- Page head -->
    <div class="page-head">
      <h1>
        Fleet <span class="br">·</span>
        <span class="team">Team {teamSlug(latestRunId)}</span>
      </h1>
      <div class="meta">
        <span>Run <b>{shortRunId(latestRunId)}</b></span>
        <span>·</span><span>Mode <b>{modeLabel}</b></span>
        <span>·</span><span>Aut <b>ASSIST</b></span>
        <span>·</span><span>Elapsed <b>{leadElapsed}</b></span>
      </div>
    </div>

    <section class="fleet">
      <div class="fleet-main">
        <div class="map">
          <!-- LEAD -->
          <div class="cell lead {leadStatusKind}">
            <div class="head">
              <span class="role"><span class="role-name">Team Lead</span> <span class="br">·</span> <span class="role-tag">Commander</span></span>
              <div class="head-right">
                <span class="aut assist">ASSIST</span>
                <span class="head-meta">claude-sonnet-4-6</span>
                {#if leadStatusKind === 'run'}
                  <span class="status run"><span class="run-tick"></span><span class="lab">Run</span></span>
                {:else}
                  <span class="status idle">Idle</span>
                {/if}
              </div>
            </div>
            <div class="lead-body">
              <div class="lead-summary">
                <span use:flap={{ text: leadActivity, baseDelay: 40, charSpacingMs: 6 }}></span>
                <div class="sub">
                  tasks: <em>{completedCount}/{tasks.length}</em>
                  · {agents.length} live session{agents.length === 1 ? '' : 's'}
                </div>
              </div>
              <div class="stats s5">
                <div class="stat"><span class="k">Tok</span>
                  <span class="v {totalTokens > 0 ? 'go' : 'nu'}">{formatTokens(totalTokens)}</span>
                </div>
                <div class="stat"><span class="k">Turns</span>
                  <span class="v">{employees.reduce((s, e) => s + (e.turns ?? 0), 0)}</span>
                </div>
                <div class="stat"><span class="k">Tasks</span>
                  <span class="v">{completedCount}<span class="frac">/{tasks.length}</span></span>
                </div>
                <div class="stat"><span class="k">Plans</span><span class="v nu">—</span></div>
                <div class="stat"><span class="k">Budget</span><span class="v nu">—</span></div>
              </div>
            </div>
          </div>

          <!-- CONNECTORS (decorative SVG; static positions) -->
          <div class="connectors" aria-hidden="true">
            <svg viewBox="0 0 1000 36" preserveAspectRatio="none">
              <path d="M 500 0 L 500 14" />
              <path d="M 167 14 L 833 14" />
              <path
                class:pulse={teammateCells.backend?.statusKind === 'run'}
                d="M 167 14 L 167 36"
              />
              <path
                class:pulse={teammateCells.frontend?.statusKind === 'run'}
                d="M 500 14 L 500 36"
              />
              <path
                class:pulse={teammateCells.qa?.statusKind === 'run'}
                d="M 833 14 L 833 36"
              />
            </svg>
          </div>

          <!-- TEAMMATES (role-grouped) -->
          <div class="team-row">
            {#each ['backend', 'frontend', 'qa'] as RoleTag[] as role}
              {@const cell = teammateCells[role]}
              {#if cell}
                <div class="cell mate {cell.statusKind}" data-role={role}>
                  <div class="head">
                    <span class="role">
                      {role[0].toUpperCase() + role.slice(1)} <span class="br">·</span> Specialist
                    </span>
                    <div class="head-right">
                      <span class="aut assist">{cell.aut}</span>
                      {#if cell.statusKind === 'run'}
                        <span class="status run"><span class="run-tick"></span><span class="lab">{cell.statusLabel}</span></span>
                      {:else if cell.statusKind === 'done'}
                        <span class="status done">{cell.statusLabel}</span>
                      {:else if cell.statusKind === 'stop'}
                        <span class="status stop">{cell.statusLabel}</span>
                      {:else if cell.statusKind === 'abort'}
                        <span class="status planx">{cell.statusLabel}</span>
                      {:else}
                        <span class="status idle">{cell.statusLabel}</span>
                      {/if}
                    </div>
                  </div>
                  <div class="task">
                    {role[0].toUpperCase() + role.slice(1)} specialist on team
                    <b>{teamSlug(latestRunId)}</b> —
                    {#if cell.taskIsNul}
                      <span class="nul">{cell.task}</span>
                    {:else}
                      {cell.task}
                    {/if}
                  </div>
                  <div class="stats s6">
                    <div class="stat"><span class="k">Tok</span><span class="v">{cell.tokens}</span></div>
                    <div class="stat"><span class="k">Turn</span><span class="v">{cell.turns}</span></div>
                    <div class="stat"><span class="k">Files</span><span class="v">{cell.files}</span></div>
                    <div class="stat"><span class="k">Time</span><span class="v">{cell.duration}</span></div>
                    <div class="stat"><span class="k">Aut</span><span class="v sm">{cell.aut}</span></div>
                    <div class="stat"><span class="k">Modl</span><span class="v sm">{cell.model}</span></div>
                  </div>
                  {#if cell.latest}
                    <div class="latest">
                      <span class="arrow">{cell.latest.dir === 'up' ? '←' : cell.latest.dir === 'peer' ? '⇄' : '→'}</span>
                      <b>{cell.latest.dir === 'down' ? cell.latest.to : cell.latest.from}</b> ·
                      <em>{cell.latest.verb}</em> {cell.latest.body}
                    </div>
                  {/if}
                </div>
              {:else}
                <!-- Empty role slot — keeps the 3-column grid stable -->
                <div class="cell mate idle empty" data-role={role}>
                  <div class="head">
                    <span class="role">
                      {role[0].toUpperCase() + role.slice(1)} <span class="br">·</span> Specialist
                    </span>
                    <div class="head-right">
                      <span class="status idle">Idle</span>
                    </div>
                  </div>
                  <div class="task"><span class="nul">No teammate assigned for this role.</span></div>
                </div>
              {/if}
            {/each}
          </div>
        </div>
      </div>

      <!-- Right rail: messages feed -->
      <aside class="side">
        <div class="section-head">
          <span>Messages</span>
          <span class="right">{msgRows.length} / {messages.length}</span>
        </div>
        <div class="body">
          {#if msgRows.length === 0}
            <div class="empty">No coordinator messages yet.</div>
          {:else}
            {#each msgRows as m, i (i)}
              <div class="msg">
                <span class="arr {m.dir === 'up' ? 'up' : m.dir === 'peer' ? 'peer' : ''}">
                  {m.from} → {m.to}
                </span>
                <span class="body"><em>{m.verb}</em>{m.body}</span>
                <span class="t">{m.t}</span>
              </div>
            {/each}
          {/if}
        </div>
      </aside>
    </section>
  {/if}
</div>

<style>
  /* Page container — flush with the sticky strip; edge-to-edge. */
  .fleet-pro {
    display: flex;
    flex-direction: column;
    min-height: calc(100vh - 40px);
    background: var(--paper);
    color: var(--ink);
    font-family: var(--pro-sans);
    background-image: radial-gradient(circle at 1px 1px, var(--dot) 1px, transparent 0);
    background-size: 24px 24px;
  }

  /* ── Page head ──────────────────────────────────────── */
  .fleet-pro :global(.page-head) {
    display: grid; grid-template-columns: auto 1fr;
    align-items: center; gap: 18px;
    padding: 10px 16px;
    border-bottom: 1px solid var(--rule);
    flex-shrink: 0;
  }
  .fleet-pro :global(.page-head h1) {
    margin: 0; font-family: var(--pro-sans);
    font-size: 14px; font-weight: 700;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--ink);
  }
  .fleet-pro :global(.page-head h1 .br) { color: var(--ash); margin: 0 6px; }
  .fleet-pro :global(.page-head h1 .team) { color: var(--graphite); }
  .fleet-pro :global(.page-head .meta) {
    font-family: var(--pro-mono); font-size: 11px;
    color: var(--graphite);
    display: flex; gap: 12px; flex-wrap: wrap; justify-content: flex-end;
  }
  .fleet-pro :global(.page-head .meta b) { color: var(--ink); font-weight: 500; }

  /* ── Plan-review banner ─────────────────────────────── */
  .fleet-pro :global(.plan-banner) {
    display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
    padding: 8px 16px;
    font-family: var(--pro-mono); font-size: 11px;
    color: var(--ink);
    border-bottom: 1px solid var(--rule);
    background: color-mix(in oklab, var(--caution) 8%, var(--paper));
  }
  .fleet-pro :global(.plan-banner.plan_approved) {
    background: color-mix(in oklab, var(--go) 8%, var(--paper));
  }
  .fleet-pro :global(.plan-banner.plan_rejected) {
    background: color-mix(in oklab, var(--abort) 8%, var(--paper));
  }
  .fleet-pro :global(.plan-banner .msg) { flex: 1; min-width: 0; }
  .fleet-pro :global(.plan-banner code) {
    font-family: var(--pro-mono); padding: 0 4px;
    background: var(--paper-2); border: 1px solid var(--rule);
  }
  .fleet-pro :global(.plan-banner .actions) { display: flex; gap: 8px; }
  .fleet-pro :global(.plan-banner .b) {
    font-family: var(--pro-sans); font-size: 9px; font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase;
    padding: 4px 10px; cursor: pointer;
    border: 1px solid var(--ink); background: var(--paper); color: var(--ink);
  }
  .fleet-pro :global(.plan-banner .b.approve) { background: var(--ink); color: var(--paper); }
  .fleet-pro :global(.plan-banner .b.reject)  { border-color: var(--abort); color: var(--abort); }
  .fleet-pro :global(.plan-banner .b:disabled) { opacity: 0.55; cursor: default; }

  /* ── Idle state ─────────────────────────────────────── */
  .fleet-pro :global(.idle-wrap) {
    flex: 1;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    text-align: center; gap: 14px;
    padding: 60px 20px;
  }
  .fleet-pro :global(.idle-mark) {
    width: 56px; height: 56px;
    display: grid; place-items: center;
    background: var(--paper-2);
    border: 1px solid var(--rule-2);
    color: var(--ash);
    font-size: 22px;
    font-family: var(--pro-mono);
  }
  .fleet-pro :global(.idle-wrap h2) {
    margin: 0;
    font-family: var(--pro-sans);
    font-size: 14px; font-weight: 700;
    letter-spacing: 0.2em; text-transform: uppercase;
    color: var(--ink);
  }
  .fleet-pro :global(.idle-wrap p) {
    margin: 0;
    max-width: 460px;
    font-family: var(--pro-mono);
    font-size: 12px; line-height: 1.55;
    color: var(--graphite);
  }
  .fleet-pro :global(.idle-wrap .trigger) {
    margin-top: 8px;
    font-family: var(--pro-sans);
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase;
    background: var(--ink); color: var(--paper);
    border: 1px solid var(--ink);
    padding: 8px 18px;
    cursor: pointer;
  }
  .fleet-pro :global(.idle-wrap .trigger:hover) { background: var(--data); border-color: var(--data); }
  .fleet-pro :global(.idle-wrap .trigger:disabled) { opacity: 0.55; cursor: default; }

  /* ── Fleet body grid ────────────────────────────────── */
  .fleet-pro :global(.fleet) {
    display: grid; grid-template-columns: 1fr 320px;
    flex: 1; min-height: 0;
  }
  .fleet-pro :global(.fleet-main) {
    border-right: 1px solid var(--rule);
    padding: 22px; min-width: 0;
  }
  .fleet-pro :global(.map) {
    display: grid; grid-template-rows: auto 36px auto;
  }

  /* ── Cells ──────────────────────────────────────────── */
  .fleet-pro :global(.cell) {
    background: var(--paper-2);
    border: 1px solid var(--rule);
    font-family: var(--pro-mono);
    display: grid;
  }
  .fleet-pro :global(.cell.lead) { padding: 14px 18px 16px; }
  .fleet-pro :global(.cell .head) {
    display: flex; align-items: center; justify-content: space-between; gap: 14px;
    font-family: var(--pro-sans);
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--ash);
    border-bottom: 1px solid var(--rule);
    padding-bottom: 8px; margin-bottom: 12px;
  }
  .fleet-pro :global(.cell .head .role) { color: var(--ink); }
  .fleet-pro :global(.cell .head .role .br) { color: var(--ash); margin: 0 4px; }
  .fleet-pro :global(.cell .head-right) {
    display: flex; align-items: center; gap: 10px;
  }
  .fleet-pro :global(.cell .head-meta) {
    font-family: var(--pro-mono); font-size: 10px;
    letter-spacing: 0; text-transform: none;
    color: var(--graphite);
  }

  .fleet-pro :global(.cell.run)   { border-left: 3px solid var(--go); }
  .fleet-pro :global(.cell.idle)  { border-left: 3px solid var(--ash); opacity: 0.74; }
  .fleet-pro :global(.cell.stop)  { border-left: 3px solid var(--caution); }
  .fleet-pro :global(.cell.abort) { border-left: 3px solid var(--abort); }
  .fleet-pro :global(.cell.done)  { border-left: 3px solid var(--graphite); }

  .fleet-pro :global(.cell .lead-body) {
    display: grid; grid-template-columns: 1fr auto;
    gap: 22px; align-items: center;
  }
  .fleet-pro :global(.cell .lead-summary) {
    font-family: var(--pro-sans);
    font-size: 14px; color: var(--ink); line-height: 1.4;
  }
  .fleet-pro :global(.cell .lead-summary .sub) {
    font-family: var(--pro-mono);
    color: var(--graphite); font-size: 11px;
    margin-top: 4px;
  }
  .fleet-pro :global(.cell .lead-summary .sub em) {
    font-style: normal; color: var(--data);
  }

  /* ── Stats grid (lead + mate) ───────────────────────── */
  .fleet-pro :global(.stats) {
    display: grid; gap: 1px;
    background: var(--rule); padding: 1px;
    border: 1px solid var(--rule);
  }
  .fleet-pro :global(.stats.s5) {
    grid-template-columns: repeat(5, minmax(64px, 1fr));
  }
  .fleet-pro :global(.stats.s6) {
    grid-template-columns: repeat(3, 1fr);
    grid-auto-rows: minmax(36px, auto);
  }
  .fleet-pro :global(.stat) {
    background: var(--paper-2);
    padding: 4px 8px;
    display: grid; align-content: center;
  }
  .fleet-pro :global(.stat .k) {
    font-family: var(--pro-sans); font-size: 8px; font-weight: 700;
    letter-spacing: 0.2em; text-transform: uppercase;
    color: var(--ash); line-height: 1;
  }
  .fleet-pro :global(.stat .v) {
    font-family: var(--pro-mono); font-weight: 600; font-size: 16px;
    color: var(--ink); line-height: 1;
    font-variant-numeric: tabular-nums; margin-top: 2px;
  }
  .fleet-pro :global(.stat .v.go) { color: var(--go); }
  .fleet-pro :global(.stat .v.nu) { color: var(--ash); }
  .fleet-pro :global(.stat .v.sm) { font-size: 11px; }
  .fleet-pro :global(.stat .v .frac) { color: var(--ash); font-size: 0.7em; }

  /* ── Connectors ─────────────────────────────────────── */
  .fleet-pro :global(.connectors) { position: relative; height: 36px; }
  .fleet-pro :global(.connectors svg) {
    position: absolute; inset: 0; width: 100%; height: 100%;
    pointer-events: none;
  }
  .fleet-pro :global(.connectors path) {
    fill: none; stroke: var(--rule-2); stroke-width: 1;
    transition: stroke 200ms ease, stroke-width 200ms ease;
  }
  .fleet-pro :global(.connectors path.pulse) {
    stroke: var(--data); stroke-width: 2;
  }

  /* ── Teammate row ───────────────────────────────────── */
  .fleet-pro :global(.team-row) {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px;
  }
  .fleet-pro :global(.cell.mate) {
    padding: 12px 14px; min-height: 240px;
    display: flex; flex-direction: column; gap: 10px;
  }
  .fleet-pro :global(.cell.mate .task) {
    font-family: var(--pro-sans); font-size: 12px;
    color: var(--ink); line-height: 1.35;
  }
  .fleet-pro :global(.cell.mate .task b) { font-weight: 600; }
  .fleet-pro :global(.cell.mate .task .nul) {
    color: var(--ash); font-style: italic;
  }
  .fleet-pro :global(.cell.mate .latest) {
    margin-top: auto;
    font-family: var(--pro-mono); font-size: 11px;
    color: var(--graphite);
    border-top: 1px dashed var(--rule);
    padding-top: 8px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .fleet-pro :global(.cell.mate .latest .arrow) {
    color: var(--data); margin-right: 4px;
  }
  .fleet-pro :global(.cell.mate .latest b) {
    color: var(--ink); font-weight: 500;
  }
  .fleet-pro :global(.cell.mate .latest em) {
    font-style: normal; color: var(--data); margin-right: 4px;
  }

  /* ── Right rail: messages ───────────────────────────── */
  .fleet-pro :global(.side) {
    display: flex; flex-direction: column; min-height: 0;
  }
  .fleet-pro :global(.side .body) {
    overflow-y: auto; flex: 1; min-height: 0;
  }
  .fleet-pro :global(.side .empty) {
    padding: 18px 14px;
    font-family: var(--pro-mono); font-size: 11px;
    color: var(--ash); font-style: italic;
  }
  .fleet-pro :global(.msg) {
    display: grid; grid-template-columns: auto 1fr auto;
    gap: 8px; align-items: baseline;
    padding: 8px 14px;
    border-bottom: 1px solid var(--rule);
    font-family: var(--pro-mono); font-size: 11px;
  }
  .fleet-pro :global(.msg .arr) {
    font-family: var(--pro-sans); font-size: 10px; font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase;
    color: var(--data);
    white-space: nowrap;
  }
  .fleet-pro :global(.msg .arr.up)   { color: var(--go); }
  .fleet-pro :global(.msg .arr.peer) { color: var(--caution); }
  .fleet-pro :global(.msg .body) {
    color: var(--ink);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .fleet-pro :global(.msg .body em) {
    font-style: normal; color: var(--data); margin-right: 4px;
  }
  .fleet-pro :global(.msg .t) { color: var(--ash); font-size: 10px; }

  /* ── Responsive ─────────────────────────────────────── */
  @media (max-width: 1180px) {
    .fleet-pro :global(.fleet) { grid-template-columns: 1fr; }
    .fleet-pro :global(.fleet-main) { border-right: none; }
    .fleet-pro :global(.team-row) { grid-template-columns: 1fr; }
  }
  @media (max-width: 760px) {
    .fleet-pro :global(.stats.s5) { grid-template-columns: repeat(2, 1fr); }
  }
</style>
