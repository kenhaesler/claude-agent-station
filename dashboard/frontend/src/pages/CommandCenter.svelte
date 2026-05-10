<script lang="ts">
  import {
    listRuns,
    getActiveEmployees,
    getTelemetrySummary,
    getSystemStatus,
    getAgentEvents,
    getCoordinatorTasks,
    listProjects,
    pauseAll,
  } from '../lib/api';
  import { navigate } from '../lib/router.svelte';
  import { agentPresence } from '../lib/agent-presence.svelte';
  import { appearance, setTheme } from '../lib/appearance.svelte';
  import { addToast } from '../lib/toast.svelte';
  import { flap } from '../lib/design/flap';
  import type {
    Run,
    ActiveEmployee,
    AgentEvent,
    CoordinatorTask,
    Project,
    TelemetrySummary,
    SystemStatus,
  } from '../lib/types';

  let { triggering = false, onTrigger }: { triggering?: boolean; onTrigger?: () => void } = $props();

  // ── Data state ─────────────────────────────────────────
  let recentRuns = $state<Run[]>([]);
  let activeEmployees = $state<ActiveEmployee[]>([]);
  let telemetry = $state<TelemetrySummary | null>(null);
  let systemStatus = $state<SystemStatus | null>(null);
  let agentEvents = $state<AgentEvent[]>([]);
  let projects = $state<Project[]>([]);
  let coordTasks = $state<CoordinatorTask[]>([]);
  let loading = $state(true);

  // ── UI state ───────────────────────────────────────────
  type FilterId = 'all' | 'active' | '24h' | 'plan-review' | 'interrupted' | 'by-mode';
  let activeFilter = $state<FilterId>('all');
  let searchQuery = $state('');
  let hovered = $state<Run | null>(null);
  let selectedIdx = $state<number>(-1);
  let clockNow = $state<string>('00:00:00');
  let acked = $state<Set<string>>(new Set());

  // ── Helpers ────────────────────────────────────────────
  function pad2(n: number) { return String(n).padStart(2, '0'); }
  function fmtTok(n: number | null | undefined): string {
    if (n == null || n === 0) return '—';
    if (n < 1000) return String(n);
    if (n < 10_000) return (n / 1000).toFixed(1) + 'K';
    if (n < 1_000_000) return Math.round(n / 1000) + 'K';
    return (n / 1_000_000).toFixed(1) + 'M';
  }
  function fmtDur(ms: number | null | undefined, status: string | null | undefined): string {
    if (ms == null) return status === 'running' || status === 'started' ? 'live' : '—';
    if (ms < 60_000) return (ms / 1000).toFixed(1) + 's';
    const m = Math.floor(ms / 60_000);
    const s = Math.round((ms % 60_000) / 1000);
    if (m < 60) return `${m}m${s}s`;
    return `${Math.floor(m / 60)}h${m % 60}m`;
  }
  function fmtAge(startedAt: string | null): string {
    if (!startedAt) return '—';
    const hasTz = startedAt.endsWith('Z') || /[+-]\d{2}:\d{2}$/.test(startedAt);
    const t = new Date(hasTz ? startedAt : startedAt + 'Z').getTime();
    const diff = Date.now() - t;
    if (diff < 0) return '0s';
    const s = Math.floor(diff / 1000);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h`;
    return `${Math.floor(h / 24)}d`;
  }
  function shortId(id: string | null | undefined): string {
    if (!id) return '—';
    return id.replace(/^run-(vb-)?/, '…');
  }
  function fmtUptime(secs: number | null | undefined): string {
    if (secs == null) return '—';
    const d = Math.floor(secs / 86400);
    const h = Math.floor((secs % 86400) / 3600);
    if (d > 0) return `${d}d ${pad2(h)}h`;
    const m = Math.floor((secs % 3600) / 60);
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
  }

  function projectRepo(projectId: number | null | undefined): string {
    if (projectId == null) return '—';
    const p = projects.find((x) => x.id === projectId);
    if (!p) return '—';
    return p.repo.includes('/') ? p.repo.split('/').pop()! : p.repo;
  }

  // Map run → status / mode / aut bucket
  type StatusBucket = { label: string; cls: string; tick: boolean };
  function statusFor(r: Run): StatusBucket {
    const s = (r.status ?? '').toLowerCase();
    if (s === 'running' || s === 'started') return { label: 'RUN', cls: 'run', tick: true };
    if (s === 'reviewing') return { label: 'REVIEW', cls: 'run', tick: true };
    if (s === 'plan_reviewing' || s === 'awaiting_plan_review') return { label: 'PLAN ?', cls: 'planok', tick: false };
    if (s === 'plan_approved' || r.verdict === 'APPROVE' || r.verdict === 'PR') return { label: 'PLAN OK', cls: 'planok', tick: false };
    if (s === 'plan_rejected' || r.verdict === 'REJECT') return { label: 'PLAN ✗', cls: 'planx', tick: false };
    if (s === 'interrupted') return { label: 'STOP', cls: 'stop', tick: false };
    if (s === 'failed' || s === 'error') return { label: 'FAIL', cls: 'planx', tick: false };
    if (s === 'completed' || s === 'finished' || s === 'success') return { label: 'DONE', cls: 'done', tick: false };
    if (!r.status && !r.finished_at) return { label: 'IDLE', cls: 'idle', tick: false };
    return { label: (r.status ?? 'IDLE').toUpperCase().slice(0, 6), cls: 'idle', tick: false };
  }

  function modeFor(r: Run): { label: string; cls: string } {
    const m = (r.mode ?? '').toLowerCase();
    if (m === 'plan_only') return { label: 'PLAN', cls: 'plan' };
    if (m === 'vision-bootstrap') return { label: 'VIS', cls: 'vision' };
    if (m === 'employee') return { label: 'EMP', cls: 'full' };
    if (m === 'full') return { label: 'FULL', cls: 'full' };
    return { label: (m || '—').toUpperCase().slice(0, 4), cls: 'full' };
  }

  function autFor(r: Run): { label: string; cls: string } {
    const a = (r.autonomy_level ?? '').toLowerCase();
    if (a === 'assisted') return { label: 'ASSIST', cls: 'assist' };
    if (a === 'auto') return { label: 'AUTO', cls: 'auto' };
    if (a === 'manual') return { label: 'MAN', cls: 'manual' };
    return { label: '—', cls: 'manual' };
  }

  function headlineFor(r: Run): { repo: string; title: string; nul: boolean } {
    const repo = projectRepo(r.project_id);
    let title: string | null = null;
    if (r.employee_report) {
      try {
        const rep = typeof r.employee_report === 'string' ? JSON.parse(r.employee_report) : r.employee_report;
        title = (rep && (rep as Record<string, unknown>).issue_title as string) ?? null;
      } catch { /* ignore */ }
    }
    if (!title && r.issue_number) title = `issue #${r.issue_number}`;
    if (!title && (r.mode as string) === 'vision-bootstrap') return { repo, title: 'vision bootstrap', nul: false };
    if (!title && r.mode === 'plan_only') return { repo, title: 'plan-only run', nul: true };
    if (!title) return { repo, title: 'untitled · no issue', nul: true };
    return { repo, title, nul: false };
  }

  // ── Filtering ─────────────────────────────────────────
  let runs24h = $derived(
    recentRuns.filter((r) => {
      if (!r.started_at) return false;
      const hasTz = r.started_at.endsWith('Z') || /[+-]\d{2}:\d{2}$/.test(r.started_at);
      const t = new Date(hasTz ? r.started_at : r.started_at + 'Z').getTime();
      return Date.now() - t < 24 * 3600 * 1000;
    }),
  );
  let runsActive = $derived(
    recentRuns.filter((r) => {
      const s = (r.status ?? '').toLowerCase();
      return s === 'running' || s === 'started' || s === 'reviewing' || s === 'plan_reviewing';
    }),
  );
  let runsPlanReview = $derived(
    recentRuns.filter((r) => {
      const s = (r.status ?? '').toLowerCase();
      return s === 'plan_reviewing' || s === 'awaiting_plan_review' || s === 'plan_approved' || s === 'plan_rejected';
    }),
  );
  let runsInterrupted = $derived(
    recentRuns.filter((r) => (r.status ?? '').toLowerCase() === 'interrupted'),
  );

  let filteredRuns = $derived.by(() => {
    let base: Run[] = recentRuns;
    if (activeFilter === 'active') base = runsActive;
    else if (activeFilter === '24h') base = runs24h;
    else if (activeFilter === 'plan-review') base = runsPlanReview;
    else if (activeFilter === 'interrupted') base = runsInterrupted;
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      base = base.filter((r) =>
        (r.run_id ?? '').toLowerCase().includes(q)
        || projectRepo(r.project_id).toLowerCase().includes(q)
        || (r.branch ?? '').toLowerCase().includes(q)
        || String(r.issue_number ?? '').includes(q),
      );
    }
    return base;
  });

  let counts = $derived({
    all: recentRuns.length,
    active: runsActive.length,
    '24h': runs24h.length,
    'plan-review': runsPlanReview.length,
    interrupted: runsInterrupted.length,
  });

  // ── Alerts derived from system status ─────────────────
  type Alert = { id: string; level: 'caution' | 'abort'; lev: string; body: string; time: string };
  let alerts = $derived.by<Alert[]>(() => {
    const out: Alert[] = [];
    const r = systemStatus?.resources;
    if (!r) return out;
    const now = `${pad2(new Date().getHours())}:${pad2(new Date().getMinutes())}`;
    if (r.disk_free_gb != null) {
      if (r.disk_free_gb < 1) {
        out.push({ id: 'disk-abort', level: 'abort', lev: 'Abort · Disk',
          body: `Only ${r.disk_free_gb.toFixed(1)} G free — purge worktrees now.`, time: now });
      } else if (r.disk_free_gb < 5) {
        out.push({ id: 'disk-caution', level: 'caution', lev: 'Caution · Disk',
          body: `${r.disk_free_gb.toFixed(1)} G free of ${(r.disk_total_gb ?? 0).toFixed(0)} G — keep an eye on it.`, time: now });
      }
    }
    if (r.memory_used_mb != null && r.memory_total_mb) {
      const pct = r.memory_used_mb / r.memory_total_mb;
      if (pct > 0.9) {
        out.push({ id: 'mem-abort', level: 'abort', lev: 'Abort · Memory',
          body: `${(r.memory_used_mb / 1024).toFixed(1)} / ${(r.memory_total_mb / 1024).toFixed(1)} G used (${Math.round(pct * 100)}%) — back off concurrency.`, time: now });
      } else if (pct > 0.7) {
        out.push({ id: 'mem-caution', level: 'caution', lev: 'Caution · Memory',
          body: `${(r.memory_used_mb / 1024).toFixed(1)} / ${(r.memory_total_mb / 1024).toFixed(1)} G used (${Math.round(pct * 100)}%).`, time: now });
      }
    }
    return out.filter((a) => !acked.has(a.id));
  });

  // ── Ticker entries ────────────────────────────────────
  let tickerSegments = $derived.by(() => {
    const segs = agentEvents.slice(0, 20).map((e) => {
      const actor = (e.agent_id ?? 'station').split(':').pop() ?? 'station';
      const tool =
        (e.event_data && (e.event_data as Record<string, unknown>).tool as string)
        ?? e.event_type
        ?? 'event';
      const target =
        (e.event_data && (e.event_data as Record<string, unknown>).summary as string)
        ?? (e.event_data && (e.event_data as Record<string, unknown>).file_path as string)
        ?? '';
      return { actor, tool, target };
    });
    if (segs.length === 0) {
      // Idle fallback so the rail isn't blank between bursts
      return [
        { actor: 'station', tool: 'idle', target: 'no recent activity' },
      ];
    }
    return segs;
  });

  // ── Footer event ──────────────────────────────────────
  let footerEvent = $derived.by(() => {
    const e = agentEvents[0];
    if (!e) return { actor: '—', tool: '', target: 'awaiting events' };
    const actor = (e.agent_id ?? 'station').split(':').pop() ?? 'station';
    const tool = ((e.event_data && (e.event_data as Record<string, unknown>).tool as string) ?? e.event_type ?? '').toString();
    const target = ((e.event_data && (e.event_data as Record<string, unknown>).summary as string)
      ?? (e.event_data && (e.event_data as Record<string, unknown>).file_path as string)
      ?? '').toString();
    return { actor, tool, target };
  });

  let latestActiveRun = $derived(
    recentRuns.find((r) => {
      const s = (r.status ?? '').toLowerCase();
      return s === 'running' || s === 'started' || s === 'reviewing' || s === 'plan_reviewing';
    }) ?? recentRuns[0] ?? null,
  );

  // ── Loaders ──────────────────────────────────────────
  // Fast path: live ticker + working count drivers (cheap, change quickly).
  async function loadFast() {
    const [empR, evR] = await Promise.allSettled([
      getActiveEmployees(),
      getAgentEvents({ limit: 20 }),
    ]);
    if (empR.status === 'fulfilled') activeEmployees = empR.value;
    if (evR.status === 'fulfilled') agentEvents = evR.value;
  }

  // Slow path: heavier endpoints (telemetry runs ~5 SQL queries; system
  // status reads /proc and shells out). Refresh less often.
  async function loadSlow() {
    const [runsR, telR, sysR, projR] = await Promise.allSettled([
      listRuns({ limit: 50 }),
      getTelemetrySummary(),
      getSystemStatus(),
      listProjects(),
    ]);
    if (runsR.status === 'fulfilled') recentRuns = runsR.value.runs;
    if (telR.status === 'fulfilled') telemetry = telR.value;
    if (sysR.status === 'fulfilled') systemStatus = sysR.value;
    if (projR.status === 'fulfilled') projects = projR.value;
  }

  async function loadAll() {
    await Promise.all([loadFast(), loadSlow()]);
    loading = false;
  }

  async function loadCoordTasks(runId: string | null | undefined) {
    if (!runId) {
      coordTasks = [];
      return;
    }
    try {
      coordTasks = await getCoordinatorTasks(runId);
    } catch { coordTasks = []; }
  }

  $effect(() => {
    loadAll();
    // Skip polling while the tab is hidden — saves ~0.6 req/s on background tabs.
    const isHidden = () => typeof document !== 'undefined' && document.visibilityState === 'hidden';
    const fast = setInterval(() => { if (!isHidden()) loadFast(); }, 10_000);
    const slow = setInterval(() => { if (!isHidden()) loadSlow(); }, 30_000);
    const c = setInterval(() => {
      const d = new Date();
      clockNow = `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
    }, 1000);
    // On tab refocus, pull fresh data immediately so users don't see stale-then-update.
    const onVis = () => { if (!isHidden()) loadAll(); };
    if (typeof document !== 'undefined') document.addEventListener('visibilitychange', onVis);
    return () => {
      clearInterval(fast);
      clearInterval(slow);
      clearInterval(c);
      if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVis);
    };
  });

  // Reload coordinator tasks whenever the latest active run changes
  $effect(() => {
    loadCoordTasks(latestActiveRun?.run_id);
  });

  // ── Keyboard shortcuts ───────────────────────────────
  function onKeydown(e: KeyboardEvent) {
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
    if (e.key === '/') {
      e.preventDefault();
      (document.querySelector('.dispatch-pro .search') as HTMLInputElement | null)?.focus();
      return;
    }
    if (e.key === 'j' || e.key === 'k') {
      e.preventDefault();
      const list = filteredRuns;
      if (list.length === 0) return;
      let next = selectedIdx;
      if (e.key === 'j') next = Math.min(list.length - 1, selectedIdx + 1);
      else next = Math.max(0, selectedIdx - 1);
      selectedIdx = next;
      hovered = list[next];
      const rows = document.querySelectorAll<HTMLElement>('.dispatch-pro .board-row');
      rows[next]?.scrollIntoView({ block: 'nearest' });
      return;
    }
    if (e.key === 't') {
      e.preventDefault();
      setTheme(appearance.theme === 'dark' ? 'light' : 'dark');
      return;
    }
    if (e.key === '.' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleGlobalPause();
      return;
    }
  }

  let pausing = $state(false);
  async function handleGlobalPause() {
    if (pausing) return;
    pausing = true;
    try {
      await pauseAll();
      addToast('success', 'Global pause engaged.');
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Pause failed';
      addToast('error', msg);
    } finally {
      pausing = false;
    }
  }

  function ackAlert(id: string) {
    const next = new Set(acked);
    next.add(id);
    acked = next;
  }

  // ── Sparkline polyline points ─────────────────────────
  function sparkPoints(spark: number[]): string {
    if (!spark || spark.length === 0) return '';
    const w = 80, h = 32;
    const max = Math.max(...spark, 1);
    const min = Math.min(...spark);
    const range = max - min || 1;
    const step = spark.length > 1 ? w / (spark.length - 1) : 0;
    return spark.map((v, i) => {
      const x = i * step;
      const y = h - ((v - min) / range) * (h - 4) - 2;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
  }

  function navigateRow(r: Run) {
    navigate(`/runs/${r.run_id}`);
  }

  let currentRunTokens = $derived(
    activeEmployees[0]?.tokens_total
      ?? agentPresence.activeRuns[0]?.tokens_total
      ?? agentPresence.tokensBurned
      ?? 0,
  );
</script>

<svelte:window onkeydown={onKeydown} />

<div data-testid="command-center" class="dispatch-pro animate-fade-in">

  <!-- Live ticker -->
  <div class="ticker">
    <span class="ticker-tag">// Live</span>
    <div class="ticker-track">
      {#each [...tickerSegments, ...tickerSegments] as seg}
        <span><b>{seg.actor}</b> · <em>{seg.tool}</em> {seg.target}</span>
      {/each}
    </div>
  </div>

  <!-- Filters -->
  <div class="filters">
    <div class="views" role="tablist">
      <button class="view" class:active={activeFilter === 'all'} onclick={() => activeFilter = 'all'}>
        All <span class="count">{counts.all}</span>
      </button>
      <button class="view" class:active={activeFilter === 'active'} onclick={() => activeFilter = 'active'}>
        Active <span class="count">{counts.active}</span>
      </button>
      <button class="view" class:active={activeFilter === '24h'} onclick={() => activeFilter = '24h'}>
        24H <span class="count">{counts['24h']}</span>
      </button>
      <button class="view" class:active={activeFilter === 'plan-review'} onclick={() => activeFilter = 'plan-review'}>
        Plan-Review <span class="count">{counts['plan-review']}</span>
      </button>
      <button class="view" class:active={activeFilter === 'interrupted'} onclick={() => activeFilter = 'interrupted'}>
        Interrupted <span class="count">{counts.interrupted}</span>
      </button>
      <button class="view" class:active={activeFilter === 'by-mode'} onclick={() => activeFilter = 'by-mode'} title="Coming soon">
        By Mode
      </button>
    </div>
    <div class="filter-right">
      {#if latestActiveRun}
        <span>{projectRepo(latestActiveRun.project_id)}</span>
        <span>·</span>
      {/if}
      <input
        class="search"
        placeholder="filter…  ·  / focuses"
        bind:value={searchQuery}
      />
    </div>
  </div>

  {#if activeFilter === 'by-mode'}
    <div class="mode-stub">
      <span class="mode plan">PLAN</span>
      <span>Mode-grouped view is coming soon — switch back to <button class="link" onclick={() => activeFilter = 'all'}>All</button>.</span>
    </div>
  {/if}

  <!-- Alerts (rendered only if any) -->
  {#if alerts.length > 0}
    <div class="alerts">
      {#each alerts as a (a.id)}
        <div class="alert {a.level === 'caution' ? 'caution' : ''}">
          <span class="dot {a.level === 'caution' ? 'caution' : 'abort'}"></span>
          <span class="lev">{a.lev}</span>
          <span class="time">{a.time}</span>
          <span class="body">{a.body}</span>
          <button class="ack" onclick={() => ackAlert(a.id)}>Ack</button>
        </div>
      {/each}
    </div>
  {/if}

  <!-- Telemetry strip -->
  <section class="telemetry">
    <div class="tcell">
      <div class="label">Active</div>
      <div class="value {(telemetry?.active.count ?? 0) > 0 ? 'go' : ''}">{telemetry?.active.count ?? 0}</div>
      <div class="sub">
        {#if telemetry && telemetry.active.teammates > 0}
          {telemetry.active.teammates} teammates
          {#if telemetry.active.roles.length > 0}· {telemetry.active.roles.join(', ')}{/if}
        {:else}
          no live work
        {/if}
      </div>
    </div>

    <div class="tcell">
      <div class="label">Queue</div>
      <div class="value">{telemetry?.queue.total ?? 0}</div>
      <div class="sub">
        {telemetry?.queue.claimed ?? 0} claimed · {telemetry?.queue.done ?? 0} done · {telemetry?.queue.pending ?? 0} pending
      </div>
    </div>

    <div class="tcell">
      <div class="label">Tokens · 7D</div>
      <div class="value">{fmtTok(telemetry?.tokens_7d.total ?? 0)}</div>
      {#if telemetry && telemetry.tokens_7d.spark.length > 0}
        <svg class="spark" viewBox="0 0 80 32" preserveAspectRatio="none" aria-hidden="true">
          <polyline
            points={sparkPoints(telemetry.tokens_7d.spark)}
            fill="none" stroke="currentColor" stroke-width="1.4"
          />
        </svg>
      {/if}
      <div class="sub">
        {telemetry?.tokens_7d.runs ?? 0} runs · {fmtTok(telemetry?.tokens_7d.output ?? 0)} out / {fmtTok(telemetry?.tokens_7d.input ?? 0)} in
      </div>
    </div>

    <div class="tcell">
      <div class="label">System</div>
      <div class="value {(telemetry?.system.status ?? 'NOMINAL') === 'CRIT' ? 'abort' : (telemetry?.system.status ?? 'NOMINAL') === 'DEGR' ? 'caution' : 'go'}">{telemetry?.system.status ?? 'NOMINAL'}</div>
      <div class="sub">
        {#if telemetry?.system.disk_free_gb != null}
          disk {telemetry.system.disk_free_gb.toFixed(1)} G ·
        {/if}
        {#if telemetry?.system.memory_used_pct != null}
          mem {telemetry.system.memory_used_pct}% ·
        {/if}
        uptime {fmtUptime(telemetry?.system.uptime_secs)}
      </div>
    </div>
  </section>

  <!-- Board -->
  <section class="board">
    <div class="board-main">
      <div class="board-head">
        <span class="col-ix">#</span>
        <span class="col-id">Run ID</span>
        <span>Mode</span>
        <span>Aut</span>
        <span>Headline</span>
        <span>Status</span>
        <span class="num">Turns</span>
        <span class="num">Tok</span>
        <span class="num">Dur</span>
        <span class="num">Age</span>
      </div>

      {#if loading}
        <div class="empty">Loading…</div>
      {:else if filteredRuns.length === 0}
        <div class="empty">No runs match this filter.</div>
      {:else}
        {#each filteredRuns as r, i (r.run_id ?? r.id)}
          {@const stat = statusFor(r)}
          {@const m = modeFor(r)}
          {@const a = autFor(r)}
          {@const head = headlineFor(r)}
          {@const baseDelay = i * 60}
          <button
            type="button"
            class="board-row"
            class:selected={selectedIdx === i || hovered?.run_id === r.run_id}
            data-run-id={r.run_id}
            onmouseenter={() => { hovered = r; selectedIdx = i; }}
            onfocus={() => { hovered = r; selectedIdx = i; }}
            onclick={() => navigateRow(r)}
          >
            <span class="ix"><span use:flap={{ text: String(i + 1).padStart(2, '0'), baseDelay }}></span></span>
            <span class="id"><span use:flap={{ text: shortId(r.run_id), baseDelay: baseDelay + 24 }}></span></span>
            <span><span class="mode {m.cls}"><span use:flap={{ text: m.label, baseDelay: baseDelay + 48 }}></span></span></span>
            <span><span class="aut {a.cls}"><span use:flap={{ text: a.label, baseDelay: baseDelay + 60 }}></span></span></span>
            <span class="title">
              <span class="repo"><span use:flap={{ text: head.repo + '  ·  ', baseDelay: baseDelay + 80 }}></span></span>
              <span class="t" class:nul={head.nul}><span use:flap={{ text: head.title, baseDelay: baseDelay + 110 }}></span></span>
            </span>
            <span><span class="status {stat.cls}">
              {#if stat.tick}<span class="run-tick"></span>{/if}
              <span use:flap={{ text: stat.label, baseDelay: baseDelay + 160 }}></span>
            </span></span>
            <span class="num" class:nu={r.turns == null}>
              <span use:flap={{ text: r.turns == null ? '—' : String(r.turns), baseDelay: baseDelay + 200 }}></span>
            </span>
            <span class="num" class:nu={r.tokens_total == null}>
              <span use:flap={{ text: fmtTok(r.tokens_total), baseDelay: baseDelay + 230 }}></span>
            </span>
            <span class="num" class:nu={r.duration_ms == null && (r.status ?? '') !== 'running'}>
              <span use:flap={{ text: fmtDur(r.duration_ms, r.status), baseDelay: baseDelay + 260 }}></span>
            </span>
            <span class="num">
              <span use:flap={{ text: fmtAge(r.started_at), baseDelay: baseDelay + 290 }}></span>
            </span>
          </button>
        {/each}
      {/if}
    </div>

    <!-- Right rail -->
    <aside class="side">
      <section>
        <div class="section-head">
          <span>Coordinator · Live</span>
          <span class="right">{latestActiveRun ? shortId(latestActiveRun.run_id) : '—'}</span>
        </div>
        <div class="body">
          {#if !latestActiveRun || coordTasks.length === 0}
            <div class="task empty">No coordinator tasks.</div>
          {:else}
            {#each coordTasks.slice(0, 8) as task (task.id)}
              <div class="task">
                <div class="lane">{task.claimed_by ?? task.teammate_agent_id ?? 'unassigned'}</div>
                <div class="t">{task.title}</div>
                <div class="meta">
                  <span>{(task.status ?? '—').toUpperCase()}</span>
                  {#if task.employee_index != null}<span>EMP {task.employee_index}</span>{/if}
                </div>
              </div>
            {/each}
          {/if}
        </div>
      </section>

      <section>
        <div class="section-head">
          <span>Context</span>
          <span class="right">{hovered ? shortId(hovered.run_id) : 'hover a row'}</span>
        </div>
        <div class="body">
          {#if !hovered}
            <div class="ctx"><div class="empty">Hover or use j/k to inspect a run.</div></div>
          {:else}
            {@const stat = statusFor(hovered)}
            <div class="ctx">
              <div class="row"><span class="lbl">Run ID</span><span class="val">{hovered.run_id ?? '—'}</span></div>
              <div class="row"><span class="lbl">Project</span><span class="val">{projectRepo(hovered.project_id)}</span></div>
              <div class="row"><span class="lbl">Mode</span><span class="val">{hovered.mode ?? '—'}</span></div>
              <div class="row"><span class="lbl">Autonomy</span><span class="val">{hovered.autonomy_level ?? '—'}</span></div>
              <div class="row"><span class="lbl">Status</span><span class="val" style="color: var(--{stat.cls === 'run' || stat.cls === 'planok' ? 'go' : stat.cls === 'planx' ? 'abort' : stat.cls === 'stop' ? 'caution' : 'graphite'});">{hovered.status ?? '—'}</span></div>
              <div class="row"><span class="lbl">Turns</span><span class="val">{hovered.turns ?? '—'}</span></div>
              <div class="row"><span class="lbl">Tokens</span><span class="val">{fmtTok(hovered.tokens_total)}</span></div>
              <div class="row"><span class="lbl">Duration</span><span class="val">{fmtDur(hovered.duration_ms, hovered.status)}</span></div>
              <div class="row"><span class="lbl">Branch</span><span class="val" style={hovered.branch ? '' : 'color: var(--ash);'}>{hovered.branch ?? '—'}</span></div>
              <div class="row"><span class="lbl">Issue</span><span class="val" style={hovered.issue_number ? '' : 'color: var(--ash);'}>{hovered.issue_number ? `#${hovered.issue_number}` : '—'}</span></div>
              <div class="row"><span class="lbl">Verdict</span><span class="val" style={hovered.verdict ? '' : 'color: var(--ash);'}>{hovered.verdict ?? '—'}</span></div>
              <div class="row"><span class="lbl">Cost</span><span class="val" style={hovered.cost_usd ? '' : 'color: var(--ash);'}>{hovered.cost_usd != null ? `$${hovered.cost_usd.toFixed(3)}` : '—'}</span></div>
            </div>
          {/if}
        </div>
      </section>
    </aside>
  </section>

  <!-- Footer -->
  <footer class="tele">
    <span class="now">
      <span class="run-tick"></span>
      <span>{clockNow}</span>
    </span>
    <span><b>{latestActiveRun ? shortId(latestActiveRun.run_id) : '—'}</b></span>
    <span class="ev">
      {#if footerEvent.actor !== '—'}
        {footerEvent.actor} · <em>{footerEvent.tool}</em> {footerEvent.target}
      {:else}
        awaiting events
      {/if}
    </span>
    <span data-testid="footer-tokens">{currentRunTokens > 0 ? `+${fmtTok(currentRunTokens)}` : '—'}</span>
  </footer>

  <div class="legend" aria-hidden="true">
    <span><kbd>/</kbd>filter</span>
    <span><kbd>j/k</kbd>row</span>
    <span><kbd>t</kbd>theme</span>
    <span><kbd>⌘.</kbd>stop</span>
  </div>
</div>

<style>
  /* Layout — keep flush with the strip; the page owns its own padding. */
  .dispatch-pro {
    display: flex;
    flex-direction: column;
    min-height: calc(100vh - 40px);
    background: var(--paper);
    color: var(--ink);
    font-family: var(--pro-sans);
    background-image: radial-gradient(circle at 1px 1px, var(--dot) 1px, transparent 0);
    background-size: 24px 24px;
  }

  /* Filter strip */
  .dispatch-pro :global(.filters) {
    display: grid;
    grid-template-columns: 1fr auto;
    align-items: center;
    height: 34px;
    padding: 0 16px;
    border-bottom: 1px solid var(--rule);
    background: var(--paper);
  }
  .dispatch-pro :global(.views) { display: flex; align-items: center; }
  .dispatch-pro :global(.view) {
    font-family: var(--pro-sans);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--graphite);
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 8px 12px;
    cursor: pointer;
    height: 34px;
  }
  .dispatch-pro :global(.view.active) { color: var(--ink); border-bottom-color: var(--ink); }
  .dispatch-pro :global(.view .count) {
    font-family: var(--pro-mono); font-size: 9px; color: var(--ash);
    margin-left: 4px; font-weight: 500;
  }
  .dispatch-pro :global(.view.active .count) { color: var(--graphite); }
  .dispatch-pro :global(.view:hover) { color: var(--ink); }
  .dispatch-pro :global(.filter-right) {
    display: flex; align-items: center; gap: 12px;
    font-family: var(--pro-mono); font-size: 10px; color: var(--ash);
  }
  .dispatch-pro :global(.search) {
    font-family: var(--pro-mono);
    font-size: 11px;
    background: var(--paper-2);
    border: 1px solid var(--rule);
    padding: 3px 8px;
    color: var(--ink);
    width: 180px;
    border-radius: 0;
  }
  .dispatch-pro :global(.search::placeholder) { color: var(--ash); }
  .dispatch-pro :global(.search:focus) { outline: none; border-color: var(--ink); }

  /* By-Mode stub banner */
  .dispatch-pro :global(.mode-stub) {
    display: flex; align-items: center; gap: 10px;
    padding: 10px 16px;
    border-bottom: 1px solid var(--rule);
    background: color-mix(in oklab, var(--data) 6%, var(--paper));
    font-family: var(--pro-mono); font-size: 11px; color: var(--ink);
  }
  .dispatch-pro :global(.mode-stub .link) {
    background: none; border: none; color: var(--data);
    text-decoration: underline; cursor: pointer; font: inherit;
  }

  /* Alerts */
  .dispatch-pro :global(.alerts) { border-bottom: 1px solid var(--rule); }
  .dispatch-pro :global(.alert) {
    display: grid;
    grid-template-columns: 8px auto auto 1fr auto;
    gap: 10px;
    align-items: center;
    padding: 6px 16px;
    border-left: 3px solid var(--abort);
    background: color-mix(in oklab, var(--abort) 6%, var(--paper));
    font-family: var(--pro-mono);
    font-size: 11px;
    color: var(--ink);
  }
  .dispatch-pro :global(.alert.caution) {
    border-left-color: var(--caution);
    background: color-mix(in oklab, var(--caution) 6%, var(--paper));
  }
  .dispatch-pro :global(.alert .lev) {
    font-family: var(--pro-sans); font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase;
    font-size: 10px; color: var(--abort);
  }
  .dispatch-pro :global(.alert.caution .lev) { color: var(--caution); }
  .dispatch-pro :global(.alert .time) { color: var(--ash); font-size: 10px; }
  .dispatch-pro :global(.alert .body b) { font-weight: 500; }
  .dispatch-pro :global(.alert .ack) {
    font-family: var(--pro-sans); font-size: 9px;
    letter-spacing: 0.16em; text-transform: uppercase;
    color: var(--ink); background: transparent;
    border: 1px solid var(--rule-2); padding: 2px 8px; cursor: pointer;
  }
  .dispatch-pro :global(.alert .ack:hover) { background: var(--paper-2); }

  /* Telemetry */
  .dispatch-pro :global(.telemetry) {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    border-bottom: 1px solid var(--rule);
  }
  .dispatch-pro :global(.tcell) {
    padding: 14px 18px;
    border-right: 1px solid var(--rule);
    display: grid;
    grid-template-columns: 1fr auto;
    grid-template-rows: auto auto auto;
    column-gap: 14px; row-gap: 4px;
  }
  .dispatch-pro :global(.tcell:last-child) { border-right: none; }
  .dispatch-pro :global(.tcell .label) {
    font-family: var(--pro-sans); font-size: 9px; font-weight: 700;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--graphite);
    grid-column: 1; grid-row: 1;
  }
  .dispatch-pro :global(.tcell .value) {
    font-family: var(--pro-mono); font-weight: 600; font-size: 26px;
    letter-spacing: -0.02em; color: var(--ink); line-height: 1;
    grid-column: 1; grid-row: 2;
  }
  .dispatch-pro :global(.tcell .value.go)      { color: var(--go); }
  .dispatch-pro :global(.tcell .value.caution) { color: var(--caution); }
  .dispatch-pro :global(.tcell .value.abort)   { color: var(--abort); }
  .dispatch-pro :global(.tcell .sub) {
    font-family: var(--pro-mono); font-size: 10px; color: var(--ash);
    grid-column: 1 / -1; grid-row: 3; margin-top: 4px;
  }
  .dispatch-pro :global(.tcell .spark) {
    grid-column: 2; grid-row: 1 / 3; align-self: center;
    width: 80px; height: 32px; color: var(--graphite);
  }

  /* Board */
  .dispatch-pro :global(.board) {
    display: grid;
    grid-template-columns: 1fr 300px;
    min-height: 440px;
    flex: 1;
  }
  .dispatch-pro :global(.board-main) {
    border-right: 1px solid var(--rule);
    display: flex; flex-direction: column;
  }
  .dispatch-pro :global(.board-head),
  .dispatch-pro :global(.board-row) {
    display: grid;
    grid-template-columns: 28px 110px 56px 60px 1.2fr 78px 50px 62px 62px 52px;
    align-items: center; gap: 10px; padding: 0 16px;
    border-bottom: 1px solid var(--rule);
  }
  .dispatch-pro :global(.board-head) {
    height: 28px; background: var(--paper-2);
    font-family: var(--pro-sans); font-size: 9px; font-weight: 700;
    letter-spacing: 0.2em; text-transform: uppercase; color: var(--ash);
    position: sticky; top: 40px;
  }
  .dispatch-pro :global(.board-head .num) { text-align: right; }
  .dispatch-pro :global(.board-row) {
    height: 38px;
    font-family: var(--pro-mono); font-size: 11px; color: var(--ink);
    cursor: pointer; text-decoration: none;
    background: transparent;
    border-left: none; border-right: none; border-top: none;
    text-align: left;
  }
  .dispatch-pro :global(.board-row:hover) { background: var(--paper-2); }
  .dispatch-pro :global(.board-row.selected) {
    background: color-mix(in oklab, var(--data) 9%, var(--paper));
  }
  .dispatch-pro :global(.board-row .ix) { color: var(--ash); font-size: 10px; }
  .dispatch-pro :global(.board-row .id) { color: var(--graphite); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .dispatch-pro :global(.board-row .title) {
    font-family: var(--pro-sans); font-size: 12px; font-weight: 500; color: var(--ink);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    min-width: 0;
  }
  .dispatch-pro :global(.board-row .title .repo) { color: var(--graphite); margin-right: 8px; }
  .dispatch-pro :global(.board-row .title .nul) { color: var(--ash); font-style: italic; }
  .dispatch-pro :global(.board-row .num) { text-align: right; font-variant-numeric: tabular-nums; }
  .dispatch-pro :global(.board-row .nu) { color: var(--ash); }
  .dispatch-pro :global(.empty) {
    padding: 24px 16px; text-align: center;
    font-family: var(--pro-mono); font-size: 11px; color: var(--ash);
  }

  /* Right rail (split: tasking + context) */
  .dispatch-pro :global(.side) {
    display: grid; grid-template-rows: 1fr 1fr; min-height: 100%;
  }
  .dispatch-pro :global(.side > section) {
    display: flex; flex-direction: column; min-height: 0;
  }
  .dispatch-pro :global(.side > section + section) { border-top: 1px solid var(--rule); }
  .dispatch-pro :global(.side .body) { overflow-y: auto; padding: 0; }
  .dispatch-pro :global(.task) {
    padding: 10px 14px; border-bottom: 1px solid var(--rule);
    display: grid; gap: 3px;
  }
  .dispatch-pro :global(.task .lane) {
    font-family: var(--pro-sans); font-size: 8px; font-weight: 700;
    letter-spacing: 0.2em; text-transform: uppercase; color: var(--data);
  }
  .dispatch-pro :global(.task .t) {
    font-family: var(--pro-sans); font-size: 12px; color: var(--ink); line-height: 1.3;
  }
  .dispatch-pro :global(.task .meta) {
    font-family: var(--pro-mono); font-size: 9px; color: var(--ash); letter-spacing: 0.04em;
    display: flex; gap: 8px; flex-wrap: wrap;
  }
  .dispatch-pro :global(.task.empty) {
    color: var(--ash); font-family: var(--pro-mono); font-size: 11px; padding: 12px 14px;
  }

  .dispatch-pro :global(.ctx) {
    padding: 12px 14px; font-family: var(--pro-mono); font-size: 11px; color: var(--ink);
  }
  .dispatch-pro :global(.ctx .row) {
    display: grid; grid-template-columns: 70px 1fr; gap: 8px;
    padding: 3px 0; border-bottom: 1px dashed var(--rule);
  }
  .dispatch-pro :global(.ctx .row:last-child) { border-bottom: none; }
  .dispatch-pro :global(.ctx .row .lbl) {
    color: var(--ash); font-family: var(--pro-sans); font-size: 9px; font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase;
  }
  .dispatch-pro :global(.ctx .row .val) { color: var(--ink); }
  .dispatch-pro :global(.ctx .empty) {
    color: var(--ash); font-style: italic; padding: 12px 0;
  }

  @media (max-width: 1180px) {
    .dispatch-pro :global(.board) { grid-template-columns: 1fr; }
    .dispatch-pro :global(.board-main) { border-right: none; }
    .dispatch-pro :global(.telemetry) { grid-template-columns: repeat(2, 1fr); }
    .dispatch-pro :global(.tcell:nth-child(2)) { border-right: none; }
    .dispatch-pro :global(.board-head),
    .dispatch-pro :global(.board-row) {
      grid-template-columns: 28px 56px 60px 1fr 78px 50px 62px 52px;
    }
    .dispatch-pro :global(.board-row .id),
    .dispatch-pro :global(.board-head .col-id) { display: none; }
  }
</style>
