<script lang="ts">
  import {
    getAutonomyAudit,
    getAutonomySummary,
    type AutonomyAuditRow,
    type AutonomySummary,
  } from '../lib/api';
  import { timeAgo } from '../lib/format';
  import AutonomyBadge from '../components/badges/AutonomyBadge.svelte';
  import SkeletonLoader from '../components/data-display/SkeletonLoader.svelte';
  import EmptyState from '../components/data-display/EmptyState.svelte';

  let summary = $state<AutonomySummary | null>(null);
  let rows = $state<AutonomyAuditRow[]>([]);
  let total = $state(0);
  let loading = $state(true);
  let runFilter = $state('');
  let toolFilter = $state('');
  let decisionFilter = $state<'' | 'allow' | 'deny'>('');
  let typeFilter = $state<'' | 'auto_mode_decision' | 'auto_mode_referral'>('');

  async function load() {
    loading = true;
    try {
      const params: Record<string, unknown> = { limit: 200 };
      if (runFilter) params.run_id = runFilter;
      if (toolFilter) params.tool_name = toolFilter;
      if (decisionFilter) params.decision = decisionFilter;
      if (typeFilter) params.event_type = typeFilter;
      const [sum, audit] = await Promise.all([
        getAutonomySummary(30),
        getAutonomyAudit(params),
      ]);
      summary = sum;
      rows = audit.items;
      total = audit.total;
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    runFilter; toolFilter; decisionFilter; typeFilter;
    load();
  });

  // Donut: compute arc offsets for a pure-SVG ring.
  const DONUT_SIZE = 120;
  const DONUT_RADIUS = 48;
  const DONUT_CIRCUMFERENCE = 2 * Math.PI * DONUT_RADIUS;

  const LEVEL_COLORS: Record<string, string> = {
    manual: '#6E5D4A',
    assisted: '#5D5F94',
    auto: '#2E7D32',
    unknown: '#A08E7A',
  };

  interface DonutSlice { label: string; value: number; offset: number; length: number; color: string; }

  let donutSlices = $derived.by<DonutSlice[]>(() => {
    if (!summary || summary.total_decisions === 0) return [];
    let cumulative = 0;
    const slices: DonutSlice[] = [];
    const entries = Object.entries(summary.by_level).sort((a, b) => b[1] - a[1]);
    for (const [level, count] of entries) {
      const fraction = count / summary.total_decisions;
      const length = fraction * DONUT_CIRCUMFERENCE;
      slices.push({
        label: level,
        value: count,
        offset: cumulative,
        length,
        color: LEVEL_COLORS[level] ?? LEVEL_COLORS.unknown,
      });
      cumulative += length;
    }
    return slices;
  });

  function inputPreview(input: Record<string, unknown>): string {
    if (typeof input.command === 'string') return input.command;
    if (typeof input.file_path === 'string') return input.file_path;
    for (const v of Object.values(input)) {
      if (typeof v === 'string') return v;
    }
    return JSON.stringify(input).slice(0, 80);
  }

  function decisionColor(d: string): string {
    return d === 'allow' ? '#2E7D32' : '#B06030';
  }
</script>

<div class="space-y-4 animate-fade-in" data-testid="autonomy-audit">
  <div class="flex items-center justify-between">
    <h1 class="font-heading text-xl">Autonomy Audit</h1>
    <span class="text-secondary text-sm">Last 30 days · {summary?.total_decisions ?? 0} decisions</span>
  </div>

  <!-- Summary panel -->
  <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
    <div class="card p-4 flex items-center gap-4">
      {#if summary && summary.total_decisions > 0}
        <svg width={DONUT_SIZE} height={DONUT_SIZE} viewBox="0 0 {DONUT_SIZE} {DONUT_SIZE}" aria-label="Decisions by autonomy level">
          <circle cx={DONUT_SIZE / 2} cy={DONUT_SIZE / 2} r={DONUT_RADIUS}
                  fill="none" stroke="rgba(160,142,122,0.15)" stroke-width="14" />
          {#each donutSlices as slice}
            <circle cx={DONUT_SIZE / 2} cy={DONUT_SIZE / 2} r={DONUT_RADIUS}
                    fill="none" stroke={slice.color} stroke-width="14"
                    stroke-dasharray="{slice.length} {DONUT_CIRCUMFERENCE - slice.length}"
                    stroke-dashoffset={-slice.offset}
                    transform="rotate(-90 {DONUT_SIZE / 2} {DONUT_SIZE / 2})" />
          {/each}
          <text x={DONUT_SIZE / 2} y={DONUT_SIZE / 2 + 5} text-anchor="middle"
                style="font-weight: 700; fill: #4A3728;">{summary.total_decisions}</text>
        </svg>
      {:else}
        <div style="width: {DONUT_SIZE}px; height: {DONUT_SIZE}px;" class="flex items-center justify-center text-secondary text-sm">No data</div>
      {/if}
      <div class="space-y-1.5">
        {#each donutSlices as slice}
          <div class="flex items-center gap-2 text-sm">
            <span style="display: inline-block; width: 10px; height: 10px; border-radius: 2px; background: {slice.color};"></span>
            <span class="font-medium capitalize">{slice.label}</span>
            <span class="text-secondary">{slice.value}</span>
          </div>
        {/each}
      </div>
    </div>

    <div class="card p-4">
      <h3 class="text-sm font-semibold mb-2">Decisions</h3>
      {#if summary}
        <div class="space-y-1 text-sm">
          <div class="flex justify-between"><span>Allow</span><span style="color: #2E7D32; font-weight: 600;">{summary.by_decision.allow ?? 0}</span></div>
          <div class="flex justify-between"><span>Deny</span><span style="color: #B06030; font-weight: 600;">{summary.by_decision.deny ?? 0}</span></div>
          <div class="flex justify-between text-secondary mt-2 pt-2" style="border-top: 1px solid rgba(160,142,122,0.2);">
            <span>Referrals</span><span>{summary.by_event_type.auto_mode_referral ?? 0}</span>
          </div>
          <div class="flex justify-between text-secondary">
            <span>Direct decisions</span><span>{summary.by_event_type.auto_mode_decision ?? 0}</span>
          </div>
        </div>
      {/if}
    </div>

    <div class="card p-4">
      <h3 class="text-sm font-semibold mb-2">Top tools</h3>
      {#if summary}
        <div class="space-y-1 text-sm">
          {#each Object.entries(summary.by_tool) as [tool, count]}
            <div class="flex justify-between">
              <span>{tool}</span><span class="text-secondary">{count}</span>
            </div>
          {/each}
          {#if Object.keys(summary.by_tool).length === 0}
            <div class="text-secondary">No tool data yet.</div>
          {/if}
        </div>
      {/if}
    </div>
  </div>

  <!-- Filters -->
  <div class="flex flex-wrap gap-2 items-center">
    <input
      type="text"
      placeholder="Filter by run id…"
      bind:value={runFilter}
      class="input text-sm"
      style="min-width: 200px;"
    />
    <input
      type="text"
      placeholder="Tool name…"
      bind:value={toolFilter}
      class="input text-sm"
      style="min-width: 140px;"
    />
    <select bind:value={decisionFilter} class="input text-sm">
      <option value="">All decisions</option>
      <option value="allow">Allow</option>
      <option value="deny">Deny</option>
    </select>
    <select bind:value={typeFilter} class="input text-sm">
      <option value="">All types</option>
      <option value="auto_mode_decision">Direct decision</option>
      <option value="auto_mode_referral">Tray referral</option>
    </select>
    <span class="text-secondary text-sm">{total} row{total === 1 ? '' : 's'}</span>
  </div>

  <!-- Audit table -->
  <div class="card overflow-hidden">
    {#if loading}
      <div class="p-4"><SkeletonLoader rows={6} /></div>
    {:else if rows.length === 0}
      <EmptyState
        title="No audit rows yet"
        description="When the policy engine evaluates a tool call, the decision shows up here."
      />
    {:else}
      <div style="overflow-x: auto;">
        <table class="w-full text-sm">
          <thead style="background: rgba(240,220,200,0.15); border-bottom: 1px solid rgba(160,142,122,0.3);">
            <tr class="text-left">
              <th class="p-2 font-medium">Time</th>
              <th class="p-2 font-medium">Run</th>
              <th class="p-2 font-medium">Agent</th>
              <th class="p-2 font-medium">Level</th>
              <th class="p-2 font-medium">Tool</th>
              <th class="p-2 font-medium">Decision</th>
              <th class="p-2 font-medium">Type</th>
              <th class="p-2 font-medium">Input</th>
            </tr>
          </thead>
          <tbody>
            {#each rows as row (row.event_id)}
              <tr style="border-bottom: 1px solid rgba(160,142,122,0.12);">
                <td class="p-2 text-secondary whitespace-nowrap">{row.created_at ? timeAgo(row.created_at) : '—'}</td>
                <td class="p-2 font-mono text-xs">
                  {#if row.run_id}
                    <a href="/runs/{row.run_id}" class="link">{row.run_id}</a>
                  {:else}—{/if}
                </td>
                <td class="p-2 text-xs">{row.agent_id}</td>
                <td class="p-2"><AutonomyBadge level={row.level} size="xs" /></td>
                <td class="p-2 font-mono text-xs">{row.tool_name}</td>
                <td class="p-2" style="color: {decisionColor(row.decision)}; font-weight: 600; text-transform: capitalize;">{row.decision}</td>
                <td class="p-2 text-xs text-secondary">
                  {row.event_type === 'auto_mode_referral' ? 'referral' : 'direct'}
                </td>
                <td class="p-2 font-mono text-xs" style="max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title={inputPreview(row.tool_input)}>
                  {inputPreview(row.tool_input)}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </div>
</div>
