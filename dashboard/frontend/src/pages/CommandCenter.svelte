<script lang="ts">
  import { listRuns, getQueueStats, getTokenUsage, getSystemStatus, getAnalytics, getBackpressure, getActiveEmployees, listQueue } from '../lib/api';
  import { navigate } from '../lib/router.svelte';
  import { agentPresence } from '../lib/agent-presence.svelte';
  import { formatTokens, formatDuration, timeAgo, formatPercent } from '../lib/format';
  import type { Run, QueueStats, TokenUsage, SystemStatus, AnalyticsResponse, BackpressureStatus, ActiveEmployee, QueueItem } from '../lib/types';
  import VaporCard from '../components/vapor/VaporCard.svelte';
  import VaporBadge from '../components/vapor/VaporBadge.svelte';
  import SkeletonLoader from '../components/data-display/SkeletonLoader.svelte';

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
  let tokenUsage = $state<TokenUsage | null>(null);
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
    if (systemStatus?.timer?.next) {
      return `Next run ${timeAgo(systemStatus.timer.next)}`;
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
    const [runsRes, empRes, qRes, tRes, sRes, aRes, bRes, qiRes] = await Promise.allSettled([
      listRuns({ limit: 15 }),
      getActiveEmployees(),
      getQueueStats(),
      getTokenUsage(),
      getSystemStatus(),
      getAnalytics({ days: 7 }),
      getBackpressure(),
      listQueue({ limit: 50 }),
    ]);
    if (runsRes.status === 'fulfilled') recentRuns = runsRes.value.runs;
    if (empRes.status === 'fulfilled') activeEmployees = empRes.value;
    if (qRes.status === 'fulfilled') queueStats = qRes.value;
    if (tRes.status === 'fulfilled') tokenUsage = tRes.value;
    if (sRes.status === 'fulfilled') systemStatus = sRes.value;
    if (aRes.status === 'fulfilled') analyticsData = aRes.value;
    if (bRes.status === 'fulfilled') backpressure = bRes.value;
    if (qiRes.status === 'fulfilled') queueItems = qiRes.value.items;
    loading = false;
  }

  $effect(() => {
    loadData();
    const interval = setInterval(loadData, 30_000);
    return () => clearInterval(interval);
  });

  function getGreeting(): string {
    const h = new Date().getHours();
    if (h < 12) return 'Good morning';
    if (h < 18) return 'Good afternoon';
    return 'Good evening';
  }

  function getRunLabel(run: Run): string {
    if (run.issue_number) return `#${run.issue_number}`;
    return run.run_id?.slice(0, 20) ?? `Run #${run.id}`;
  }

  function getRowTint(run: Run): string {
    if (run.verdict === 'APPROVE' || run.verdict === 'PR') return 'background: rgba(46,125,50,0.03);';
    if (run.verdict === 'REJECT') return 'background: rgba(208,96,80,0.03);';
    if (run.status === 'started') return 'background: rgba(46,125,50,0.02);';
    return '';
  }

  function getVerdictBadge(verdict: string | null): string {
    if (!verdict) return '';
    const map: Record<string, string> = { 'APPROVE': 'badge-approve', 'PR': 'badge-pr', 'REJECT': 'badge-reject' };
    return map[verdict] ?? '';
  }

  function getStatusBadge(run: Run): { label: string; cls: string } {
    if (run.verdict) return { label: run.verdict, cls: getVerdictBadge(run.verdict) };
    if (run.status === 'started') return { label: 'RUNNING', cls: 'badge-running' };
    if (run.status === 'finished') return { label: 'DONE', cls: 'badge-completed' };
    return { label: 'PENDING', cls: 'badge-pending' };
  }
</script>

<div style="animation: greeting-in 0.8s cubic-bezier(0.16, 1, 0.3, 1) both;">

  {#if loading}
    <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 28px;">
      {#each Array(4) as _}
        <div class="card" style="padding: 24px;"><SkeletonLoader lines={2} /></div>
      {/each}
    </div>
    <div class="card" style="padding: 24px;"><SkeletonLoader lines={8} /></div>

  {:else}
    <!-- Greeting -->
    <div style="margin-bottom: 24px;">
      <div style="font-size: 24px; font-weight: 800; color: #3D2A1A; letter-spacing: -0.03em;">{getGreeting()}</div>
      <div style="font-size: 14px; color: #8C7A66; margin-top: 4px;">
        {#if stationPhase === 'working'}
          <span style="color: #2E7D32; font-weight: 600;">{stationSummary}</span>
          <span> · </span>
          <button onclick={() => navigate('/agent-teams')} style="color: #B06030; font-weight: 600; cursor: pointer; border: none; background: none; font-family: inherit; font-size: inherit; text-decoration: underline; text-underline-offset: 2px;">Watch Team →</button>
        {:else}
          {stationSummary}
        {/if}
      </div>
    </div>

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
        <div style="font-size: 13px; color: #8C7A66; margin-top: 8px;">{formatPercent(tokenUsage?.max_usage_percent ?? 0)} of budget</div>
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
        <button
          onclick={() => navigate(`/runs/${run.run_id}`)}
          style="display: flex; align-items: center; padding: 16px 24px; border-bottom: 1px solid rgba(0,0,0,0.03); transition: background 0.2s ease; cursor: pointer; width: 100%; text-align: left; border: none; font-family: inherit; {getRowTint(run)}"
        >
          <div style="width: 8px; height: 8px; border-radius: 50%; margin-right: 14px; flex-shrink: 0; {run.verdict === 'APPROVE' || run.verdict === 'PR' ? 'background: #2E7D32; box-shadow: 0 0 6px rgba(46,125,50,0.35);' : run.verdict === 'REJECT' ? 'background: #D06050;' : run.status === 'started' ? 'background: #2E7D32; box-shadow: 0 0 6px rgba(46,125,50,0.35);' : 'background: #C4AA90;'}"></div>
          <span style="font-size: 15px; font-weight: 600; flex: 1; color: {run.verdict === 'APPROVE' || run.verdict === 'PR' || run.status === 'started' ? '#3D2A1A' : '#8C7A66'};">{getRunLabel(run)}</span>
          <span class="badge {status.cls}">{status.label}</span>
          <span style="font-size: 13px; color: #8C7A66; margin-left: 16px;">{run.tokens_total ? formatTokens(run.tokens_total) : '—'}</span>
          <span style="font-size: 13px; color: #8C7A66; margin-left: 16px; min-width: 54px; text-align: right;">{timeAgo(run.started_at)}</span>
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
            <span style="font-size: 13px; color: {backpressure?.level === 'GREEN' ? '#2E7D32' : backpressure?.level === 'YELLOW' ? '#B06030' : '#D06050'}; font-weight: 600;">{backpressure?.level ?? '—'}</span>
          </div>
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <span style="font-size: 14px; color: #3D2A1A;">Memory</span>
            <span style="font-size: 13px; color: #8C7A66;">{systemStatus?.resources?.memory ?? '—'}</span>
          </div>
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <span style="font-size: 14px; color: #3D2A1A;">Uptime</span>
            <span style="font-size: 13px; color: #8C7A66;">{systemStatus?.resources?.uptime ?? '—'}</span>
          </div>
        </div>
      </VaporCard>
    </div>
  {/if}
</div>
