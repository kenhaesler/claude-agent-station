<script lang="ts">
  import { listRuns, getQueueStats, getTokenUsage, getPlanUsage, getSystemStatus, getAnalytics, getBackpressure, getActiveEmployees, listQueue, listProjects } from '../lib/api';
  import { navigate } from '../lib/router.svelte';
  import { agentPresence } from '../lib/agent-presence.svelte';
  import { formatTokens, formatDuration, timeAgo, formatPercent } from '../lib/format';

  function formatUptime(seconds: number | null | undefined): string {
    if (seconds == null) return '—';
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (d > 0) return `${d}d ${h}h`;
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
  }

  function formatMemoryMB(usedMb: number | null | undefined, totalMb: number | null | undefined): string {
    if (usedMb == null || totalMb == null) return '—';
    return `${(usedMb / 1024).toFixed(1)} / ${(totalMb / 1024).toFixed(1)} GB`;
  }
  import type { Run, QueueStats, TokenUsage, PlanUsage, SystemStatus, AnalyticsResponse, BackpressureStatus, ActiveEmployee, QueueItem, Project } from '../lib/types';
  import VaporCard from '../components/vapor/VaporCard.svelte';
  import VaporBadge from '../components/vapor/VaporBadge.svelte';
  import SkeletonLoader from '../components/data-display/SkeletonLoader.svelte';
  import AgentActivityFeed from '../components/agents/AgentActivityFeed.svelte';
  import { stopRun } from '../lib/api';
  import { addToast } from '../lib/toast.svelte';

  let stopping = $state(false);

  async function handleStopActiveRun() {
    const active = agentPresence.activeRuns[0];
    if (!active?.run_id || stopping) return;
    const ok = confirm(`Stop the agent NOW?\n\nThis will kill the claude-agent service and mark run ${active.run_id} as interrupted. All active work halts immediately.`);
    if (!ok) return;
    stopping = true;
    try {
      await stopRun(active.run_id);
      addToast('success', 'Hard stop issued — service stopping, run interrupted');
    } catch (e) {
      const raw = e instanceof Error ? e.message : 'Stop failed';
      addToast('error', raw.startsWith('409:') ? raw.slice(4).trim() : raw);
    } finally {
      stopping = false;
    }
  }

  let {
    triggering = false,
    onTrigger,
  }: {
    triggering?: boolean;
    onTrigger?: () => void;
  } = $props();

  // Data state
  let recentRuns = $state<Run[]>([]);
  let activeEmployees = $state<ActiveEmployee[]>([]);
  let queueStats = $state<QueueStats | null>(null);
  let queueItems = $state<QueueItem[]>([]);
  let projects = $state<Project[]>([]);
  let tokenUsage = $state<TokenUsage | null>(null);
  let planUsage = $state<PlanUsage | null>(null);
  let systemStatus = $state<SystemStatus | null>(null);
  let analyticsData = $state<AnalyticsResponse | null>(null);
  let backpressure = $state<BackpressureStatus | null>(null);
  let loading = $state(true);

  // Derived
  let stationPhase = $derived<'idle' | 'working' | 'attention'>(
    activeEmployees.length > 0 ? 'working' :
    (queueStats?.by_state?.review ?? 0) > 0 ? 'attention' : 'idle'
  );

  let stationSummary = $derived.by(() => {
    if (activeEmployees.length > 0) {
      const projects = new Set(activeEmployees.map(e => e.project_id)).size;
      return `${activeEmployees.length} agent${activeEmployees.length > 1 ? 's' : ''} working across ${projects} project${projects > 1 ? 's' : ''}`;
    }
    if ((queueStats?.by_state?.review ?? 0) > 0) {
      return `${queueStats!.by_state.review} item${queueStats!.by_state.review > 1 ? 's' : ''} need review`;
    }
    if (systemStatus?.timer?.next_trigger) {
      return `Next run ${timeAgo(systemStatus.timer.next_trigger)}`;
    }
    return 'All systems nominal';
  });

  let verdictCounts = $derived.by(() => {
    if (!analyticsData?.verdict_distribution) return { approve: 0, pr: 0, reject: 0, skip: 0, total: 0 };
    const dist = analyticsData.verdict_distribution;
    return {
      approve: dist.find(v => v.verdict === 'APPROVE')?.count ?? 0,
      pr: dist.find(v => v.verdict === 'PR')?.count ?? 0,
      reject: dist.find(v => v.verdict === 'REJECT')?.count ?? 0,
      skip: dist.find(v => v.verdict === 'SKIP')?.count ?? 0,
      total: dist.reduce((s, v) => s + v.count, 0) || 1,
    };
  });

  let successRate = $derived(
    verdictCounts.total > 0
      ? Math.round(((verdictCounts.approve + verdictCounts.pr) / verdictCounts.total) * 100)
      : 0
  );

  let queuePending = $derived(
    (queueStats?.by_state?.pending ?? 0) +
    (queueStats?.by_state?.assigned ?? 0) +
    (queueStats?.by_state?.claimed ?? 0) +
    (queueStats?.by_state?.planning ?? 0)
  );

  // Fetch data
  async function loadData() {
    const [runsRes, empRes, qRes, tRes, pRes, sRes, aRes, bRes, qiRes, projRes] = await Promise.allSettled([
      listRuns({ limit: 15 }),
      getActiveEmployees(),
      getQueueStats(),
      getTokenUsage(),
      getPlanUsage(),
      getSystemStatus(),
      getAnalytics({ days: 7 }),
      getBackpressure(),
      listQueue({ limit: 50 }),
      listProjects(),
    ]);
    if (runsRes.status === 'fulfilled') recentRuns = runsRes.value.runs;
    if (empRes.status === 'fulfilled') activeEmployees = empRes.value;
    if (qRes.status === 'fulfilled') queueStats = qRes.value;
    if (tRes.status === 'fulfilled') tokenUsage = tRes.value;
    if (pRes.status === 'fulfilled') planUsage = pRes.value;
    if (sRes.status === 'fulfilled') systemStatus = sRes.value;
    if (aRes.status === 'fulfilled') analyticsData = aRes.value;
    if (bRes.status === 'fulfilled') backpressure = bRes.value;
    if (qiRes.status === 'fulfilled') queueItems = qiRes.value.items;
    if (projRes.status === 'fulfilled') projects = projRes.value;
    loading = false;
  }

  $effect(() => {
    loadData();
    const interval = setInterval(loadData, 30_000);
    return () => clearInterval(interval);
  });

  function getProjectRepo(projectId: number | null | undefined): string | null {
    if (projectId == null) return null;
    const proj = projects.find((p) => p.id === projectId);
    if (!proj) return null;
    const repo = proj.repo ?? '';
    return repo.includes('/') ? repo.split('/').pop()! : repo;
  }

  function getIssueTitle(run: Run): string | null {
    if (!run.employee_report) return null;
    try {
      const report = typeof run.employee_report === 'string'
        ? JSON.parse(run.employee_report)
        : run.employee_report;
      return report?.issue_title ?? null;
    } catch {
      return null;
    }
  }

  function getRunLabel(run: Run): string {
    const repo = getProjectRepo(run.project_id);
    const issue = run.issue_number ? `#${run.issue_number}` : null;
    if (repo && issue) return `${repo} ${issue}`;
    if (repo) return repo;
    if (issue) return issue;
    return run.run_id?.slice(0, 20) ?? `Run #${run.id}`;
  }

  function getRowTint(run: Run): string {
    if (run.verdict === 'APPROVE' || run.verdict === 'PR') return 'background: rgba(46,125,50,0.04);';
    if (run.verdict === 'REJECT') return 'background: rgba(208,96,80,0.04);';
    if (run.status === 'running' || run.status === 'started' || run.status === 'reviewing' || run.status === 'plan_reviewing') return 'background: rgba(46,125,50,0.03);';
    if (run.status === 'interrupted') return 'background: rgba(176,96,48,0.04);';
    if (run.status === 'failed') return 'background: rgba(208,96,80,0.03);';
    return '';
  }

  function getVerdictBadge(verdict: string | null): string {
    if (!verdict) return '';
    const map: Record<string, string> = { 'APPROVE': 'badge-approve', 'PR': 'badge-pr', 'REJECT': 'badge-reject', 'SKIP': 'badge-pending' };
    return map[verdict] ?? '';
  }

  function getStatusBadge(run: Run): { label: string; cls: string } {
    // Priority: live > terminal
    const s = (run.status ?? '').toLowerCase();
    if (s === 'running' || s === 'started') return { label: 'RUNNING', cls: 'badge-running' };
    if (s === 'reviewing') return { label: 'REVIEWING', cls: 'badge-running' };
    if (s === 'plan_reviewing') return { label: 'PLAN REVIEW', cls: 'badge-running' };
    if (run.verdict) return { label: run.verdict, cls: getVerdictBadge(run.verdict) };
    if (s === 'completed' || s === 'finished' || s === 'success') return { label: 'DONE', cls: 'badge-completed' };
    if (s === 'failed' || s === 'error') return { label: 'FAILED', cls: 'badge-reject' };
    if (s === 'interrupted') return { label: 'STOPPED', cls: 'badge-pending' };
    if (!run.status && !run.finished_at) return { label: 'QUEUED', cls: 'badge-pending' };
    return { label: (run.status ?? 'unknown').toUpperCase(), cls: 'badge-pending' };
  }

  function getStatusDot(run: Run): string {
    if (run.verdict === 'APPROVE' || run.verdict === 'PR') return 'background: #2E7D32; box-shadow: 0 0 6px rgba(46,125,50,0.35);';
    if (run.verdict === 'REJECT') return 'background: #D06050;';
    const s = (run.status ?? '').toLowerCase();
    if (s === 'running' || s === 'started' || s === 'reviewing' || s === 'plan_reviewing') return 'background: #2E7D32; box-shadow: 0 0 6px rgba(46,125,50,0.35);';
    if (s === 'failed' || s === 'error') return 'background: #D06050;';
    if (s === 'interrupted') return 'background: #B06030;';
    return 'background: #C4AA90;';
  }
</script>

<div data-testid="command-center" class="dispatch-pro animate-fade-in">

  {#if loading}
    <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 28px;">
      {#each Array(4) as _}
        <div class="card" style="padding: 24px;"><SkeletonLoader lines={2} /></div>
      {/each}
    </div>
    <div class="card" style="padding: 24px;"><SkeletonLoader lines={8} /></div>

  {:else}
    <!-- Page header (Pro) -->
    <div class="dp-page-head">
      <h1 class="dp-title">Dispatch</h1>
      <div class="dp-meta">
        {#if stationPhase === 'working'}
          <span class="dp-status go">{stationSummary}</span>
          <span class="sep">·</span>
          <button onclick={() => navigate('/mission-control')} class="dp-mc-btn">Open Mission Control →</button>
        {:else}
          <span>{stationSummary}</span>
        {/if}
      </div>
    </div>

    <!--
      Live Activity — Phase 1 of "The Bridge".
      Replaces the silent landing page with a stream of agent narration, tool
      calls, and phase transitions. Pulled straight from agentPresence so the
      same data any page could already access now surfaces on home. Renders
      whenever we have activity — otherwise yields an idle hint instead of
      dead whitespace.
    -->
    {#if agentPresence.activeRuns.length > 0 || agentPresence.conversationLog.length > 0}
      <div
        data-testid="bridge-activity"
        style="background: rgba(255,251,247,0.65); backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px); border: 1px solid rgba(240,220,200,0.6); border-radius: 18px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.04), 0 12px 32px rgba(0,0,0,0.07); margin-bottom: 28px; animation: card-in 0.6s cubic-bezier(0.16, 1, 0.3, 1) 0.10s both;"
      >
        <div style="padding: 14px 24px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(0,0,0,0.04);">
          <div style="display: flex; align-items: center; gap: 10px;">
            <div
              style="width: 8px; height: 8px; border-radius: 50%; {agentPresence.activeRuns.length > 0 ? 'background: #2E7D32; box-shadow: 0 0 8px rgba(46,125,50,0.45);' : 'background: #C4AA90;'}"
            ></div>
            <span style="font-size: 15px; font-weight: 700; color: #3D2A1A;">
              {agentPresence.activeRuns.length > 0 ? 'Live' : 'Last activity'}
            </span>
            {#if agentPresence.phase && agentPresence.phase !== 'idle'}
              <span style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #7A6652; font-weight: 600;">· {agentPresence.phase}</span>
            {/if}
            {#if agentPresence.tokensBurned > 0}
              <span style="font-size: 12px; color: #4E3A26; font-variant-numeric: tabular-nums;">· {formatTokens(agentPresence.tokensBurned)} tokens</span>
            {/if}
          </div>
          <div style="display: flex; align-items: center; gap: 8px;">
            {#if agentPresence.activeRuns.length > 0}
              <button
                onclick={handleStopActiveRun}
                disabled={stopping}
                data-testid="bridge-stop-btn"
                title="Hard stop: kill the agent service and mark active runs interrupted"
                style="font-size: 12px; font-weight: 700; color: #8B1A1A; background: rgba(208,80,80,0.10); border: 1px solid rgba(208,80,80,0.30); padding: 6px 12px; border-radius: 8px; cursor: pointer; font-family: inherit; transition: background 0.15s ease;"
              >{stopping ? '…' : '⏹ Stop agent'}</button>
            {/if}
            <button
              onclick={() => navigate('/mission-control')}
              style="font-size: 13px; color: #B06030; font-weight: 600; cursor: pointer; border: none; background: none; font-family: inherit;"
            >Open Mission Control →</button>
          </div>
        </div>
        <div style="position: relative; height: 260px; padding: 6px 14px;">
          <AgentActivityFeed maxEntries={120} />
        </div>
      </div>
    {:else}
      <div
        data-testid="bridge-idle"
        style="background: rgba(255,251,247,0.45); border: 1px dashed rgba(240,220,200,0.8); border-radius: 14px; padding: 16px 20px; margin-bottom: 28px; font-size: 13px; color: #8C7A66; display: flex; align-items: center; justify-content: space-between;"
      >
        <span>No live activity. Trigger a run to watch the agent work here, in real time.</span>
        <button
          onclick={onTrigger}
          disabled={triggering}
          style="font-size: 13px; font-weight: 600; color: #B06030; cursor: pointer; border: none; background: none; font-family: inherit;"
        >{triggering ? 'Triggering…' : 'Trigger run →'}</button>
      </div>
    {/if}

    <!-- 4 Metric Cards -->
    <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 28px;">
      <VaporCard stagger={0.05}>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <div style="font-size: 14px; color: #7A6652; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em;">Runs (7d)</div>
          <div style="width: 32px; height: 32px; background: rgba(59,130,246,0.08); border-radius: 9px; display: flex; align-items: center; justify-content: center;">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#3B82F6" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
          </div>
        </div>
        <div style="font-size: 48px; font-weight: 800; letter-spacing: -0.05em; margin-top: 10px; line-height: 1; color: #3D2A1A;">{analyticsData?.total_runs ?? 0}</div>
        <div style="font-size: 13px; color: #2E7D32; font-weight: 600; margin-top: 8px;">{stationSummary}</div>
      </VaporCard>

      <VaporCard stagger={0.12}>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <div style="font-size: 14px; color: #7A6652; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em;">Success</div>
          <div style="width: 32px; height: 32px; background: rgba(234,88,12,0.08); border-radius: 9px; display: flex; align-items: center; justify-content: center;">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#EA580C" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
          </div>
        </div>
        <div style="font-size: 48px; font-weight: 800; letter-spacing: -0.05em; margin-top: 10px; line-height: 1; color: #D84315;">{successRate}%</div>
        <div style="width: 100%; height: 4px; background: rgba(0,0,0,0.05); border-radius: 999px; margin-top: 12px; overflow: hidden;">
          <div class="progress-fill" style="width: {successRate}%; height: 100%; background: #D84315; border-radius: 999px;"></div>
        </div>
      </VaporCard>

      <VaporCard stagger={0.19}>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <div style="font-size: 14px; color: #7A6652; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em;">Tokens</div>
          <div style="width: 32px; height: 32px; background: rgba(99,102,241,0.08); border-radius: 9px; display: flex; align-items: center; justify-content: center;">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#6366F1" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
          </div>
        </div>
        <div style="font-size: 48px; font-weight: 800; letter-spacing: -0.05em; margin-top: 10px; line-height: 1; color: #3D2A1A;">{formatTokens(tokenUsage?.daily?.tokens_total ?? null)}</div>
        <div style="font-size: 13px; color: #8C7A66; margin-top: 8px;">
          {#if planUsage?.weekly_tokens_percent != null}
            {formatPercent(planUsage.weekly_tokens_percent)} of weekly plan
          {:else}
            this week
          {/if}
        </div>
      </VaporCard>

      <VaporCard stagger={0.26}>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <div style="font-size: 14px; color: #7A6652; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em;">Queue</div>
          <div style="width: 32px; height: 32px; background: rgba(176,96,48,0.08); border-radius: 9px; display: flex; align-items: center; justify-content: center;">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#B06030" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
          </div>
        </div>
        <div style="font-size: 48px; font-weight: 800; letter-spacing: -0.05em; margin-top: 10px; line-height: 1; color: #B06030;">{queuePending}</div>
        <div style="font-size: 13px; color: #8C7A66; margin-top: 8px;">{queueStats?.by_state?.review ?? 0} in review</div>
      </VaporCard>
    </div>

    <!-- Recent Runs Table -->
    <div style="background: rgba(255,251,247,0.65); backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px); border: 1px solid rgba(240,220,200,0.6); border-radius: 18px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.04), 0 12px 32px rgba(0,0,0,0.07); animation: card-in 0.6s cubic-bezier(0.16, 1, 0.3, 1) 0.35s both; margin-bottom: 28px;">
      <div style="padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(0,0,0,0.04);">
        <span style="font-size: 15px; font-weight: 700; color: #3D2A1A;">Recent Runs</span>
        <button onclick={() => navigate('/runs')} style="font-size: 14px; color: #7A6652; cursor: pointer; border: none; background: none; font-family: inherit;">View all →</button>
      </div>
      {#each recentRuns.slice(0, 10) as run (run.id)}
        {@const status = getStatusBadge(run)}
        {@const title = getIssueTitle(run)}
        <button
          onclick={() => navigate(`/runs/${run.run_id}`)}
          title="Open {run.run_id}"
          style="display: flex; align-items: center; gap: 14px; padding: 14px 24px; border-bottom: 1px solid rgba(0,0,0,0.04); transition: background 0.2s ease; cursor: pointer; width: 100%; text-align: left; border: none; font-family: inherit; {getRowTint(run)}"
        >
          <div style="width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; {getStatusDot(run)}"></div>

          <!-- Left: project/#issue + title -->
          <div style="flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px;">
            <div style="display: flex; align-items: baseline; gap: 8px; min-width: 0;">
              <span style="font-size: 14px; font-weight: 700; color: #2A1C0E; white-space: nowrap;">{getRunLabel(run)}</span>
              {#if title}
                <span style="font-size: 13px; color: #4E3A26; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0;">{title}</span>
              {/if}
            </div>
            <div style="display: flex; align-items: center; gap: 10px; font-size: 12px; color: #6B5743;">
              {#if run.turns != null}<span>{run.turns} turns</span>{/if}
              {#if run.duration_ms}<span>·</span><span>{formatDuration(run.duration_ms)}</span>{/if}
              {#if run.branch}<span>·</span><span style="font-family: var(--font-mono); font-size: 11px;">{run.branch}</span>{/if}
              {#if run.autonomy_level}<span>·</span><span style="text-transform: uppercase; letter-spacing: 0.05em; font-size: 10px; font-weight: 600;">{run.autonomy_level}</span>{/if}
            </div>
          </div>

          <!-- Right: status + verdict + tokens + time -->
          <div style="display: flex; align-items: center; gap: 10px; flex-shrink: 0;">
            <span class="badge {status.cls}">{status.label}</span>
            {#if run.verdict && status.label !== run.verdict}
              <span class="badge {getVerdictBadge(run.verdict)}">{run.verdict}</span>
            {/if}
            <span style="font-size: 13px; color: #4E3A26; min-width: 56px; text-align: right; font-variant-numeric: tabular-nums;">{run.tokens_total ? formatTokens(run.tokens_total) : '—'}</span>
            <span style="font-size: 12px; color: #6B5743; min-width: 60px; text-align: right;">{timeAgo(run.started_at)}</span>
          </div>
        </button>
      {/each}
      {#if recentRuns.length === 0}
        <div style="padding: 48px 24px; text-align: center;">
          <div style="font-size: 32px; opacity: 0.2; margin-bottom: 12px;">▶</div>
          <p style="font-size: 14px; color: #7A6652; margin-bottom: 4px;">No runs yet</p>
          <p style="font-size: 13px; color: #8C7A66;">Trigger your first agent run to see results here</p>
        </div>
      {/if}
    </div>

    <!-- 3 Secondary Cards -->
    <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; animation: card-in 0.6s cubic-bezier(0.16, 1, 0.3, 1) 0.85s both;">
      <VaporCard>
        <div style="font-size: 14px; color: #7A6652; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 14px;">Active Projects</div>
        <div style="display: flex; flex-direction: column; gap: 12px;">
          {#each (queueItems.reduce((acc, item) => { if (!acc.find(a => a.repo === item.project_repo)) acc.push({ repo: item.project_repo, active: ['in_progress', 'assigned', 'claimed', 'planning'].includes(item.state) }); return acc; }, [] as { repo: string; active: boolean }[]).slice(0, 4)) as project}
            <div style="display: flex; align-items: center; justify-content: space-between;">
              <span style="font-size: 14px; {project.active ? 'font-weight: 600; color: #3D2A1A;' : 'color: #8C7A66;'}">{project.repo.split('/').pop()}</span>
              <VaporBadge variant={project.active ? 'active' : 'disabled'}>{project.active ? 'Active' : 'Idle'}</VaporBadge>
            </div>
          {/each}
          {#if queueItems.length === 0}
            <div style="font-size: 13px; color: #8C7A66;">No projects in queue</div>
          {/if}
        </div>
      </VaporCard>

      <VaporCard>
        <div style="font-size: 14px; color: #7A6652; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 14px;">Queue Preview</div>
        <div style="display: flex; flex-direction: column; gap: 10px;">
          {#each queueItems.filter(i => i.state === 'pending' || i.state === 'assigned').slice(0, 3) as item}
            <div style="font-size: 13px; color: #3D2A1A; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{item.issue_title || `#${item.issue_number}`}</div>
          {/each}
          {#if queuePending > 3}
            <div style="font-size: 12px; color: #8C7A66; margin-top: 2px;">+{queuePending - 3} more in queue</div>
          {/if}
          {#if queuePending === 0}
            <div style="font-size: 13px; color: #8C7A66;">Queue empty</div>
          {/if}
        </div>
      </VaporCard>

      <VaporCard>
        <div style="font-size: 14px; color: #7A6652; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 14px;">System Health</div>
        <div style="display: flex; flex-direction: column; gap: 12px;">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <span style="font-size: 14px; color: #3D2A1A;">Service</span>
            <span style="font-size: 13px; color: {systemStatus?.service?.active ? '#2E7D32' : '#D06050'}; font-weight: 600;">{systemStatus?.service?.active ? 'Online' : 'Offline'}</span>
          </div>
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <span style="font-size: 14px; color: #3D2A1A;">Backpressure</span>
            <span style="font-size: 13px; color: {
              backpressure?.level === 'GREEN' ? '#2E7D32' :
              backpressure?.level === 'YELLOW' ? '#B06030' :
              backpressure?.level === 'RED' ? '#D06050' :
              backpressure?.level === 'BLACK' ? '#111111' :
              '#8C7A66'
            }; font-weight: 600;">{backpressure?.level ?? '—'}</span>
          </div>
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <span style="font-size: 14px; color: #3D2A1A;">Memory</span>
            <span style="font-size: 13px; color: #8C7A66;">{formatMemoryMB(systemStatus?.resources?.memory_used_mb, systemStatus?.resources?.memory_total_mb)}</span>
          </div>
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <span style="font-size: 14px; color: #3D2A1A;">Uptime</span>
            <span style="font-size: 13px; color: #8C7A66;">{formatUptime(systemStatus?.resources?.uptime_seconds)}</span>
          </div>
        </div>
      </VaporCard>
    </div>
  {/if}
</div>

<style>
  .dispatch-pro :global(.dp-page-head) {
    display: flex; align-items: center; justify-content: space-between;
    gap: 14px; padding: 6px 0 12px; margin-bottom: 18px;
    border-bottom: 1px solid var(--rule);
  }
  .dispatch-pro :global(.dp-title) {
    margin: 0;
    font-family: var(--pro-sans);
    font-size: 14px; font-weight: 700;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--ink);
  }
  .dispatch-pro :global(.dp-meta) {
    font-family: var(--pro-mono); font-size: 12px;
    color: var(--graphite);
    display: flex; gap: 8px; align-items: center;
  }
  .dispatch-pro :global(.dp-meta .sep) { color: var(--ash); }
  .dispatch-pro :global(.dp-status.go) { color: var(--go); font-weight: 500; }
  .dispatch-pro :global(.dp-mc-btn) {
    background: transparent; border: none; cursor: pointer;
    color: var(--data); font-family: inherit; font-size: inherit;
    padding: 0; text-decoration: underline; text-underline-offset: 2px;
  }
  .dispatch-pro :global(.dp-mc-btn:hover) { filter: brightness(0.9); }

  /* Flatten neumorphic surfaces on the page */
  .dispatch-pro :global(.card) {
    background: var(--paper-2) !important;
    border: 1px solid var(--rule) !important;
    border-radius: 0 !important;
    backdrop-filter: none !important;
    -webkit-backdrop-filter: none !important;
    box-shadow: none !important;
    transition: border-color 200ms ease !important;
    animation: none !important;
  }
  .dispatch-pro :global(.card:hover) {
    border-color: var(--rule-2) !important;
    transform: none !important;
    box-shadow: none !important;
  }
  /* Kill the breathe animations on KPI cards */
  .dispatch-pro :global(.card-breathe),
  .dispatch-pro :global(.card-breathe-amber),
  .dispatch-pro :global([class*="card-in"]) {
    animation: none !important;
  }
</style>
