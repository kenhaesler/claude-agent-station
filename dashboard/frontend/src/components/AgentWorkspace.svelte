<script lang="ts">
  import { agentPresence, togglePanel } from '../lib/agent-presence.svelte';
  import { navigate } from '../lib/router.svelte';
  import type { SystemStatus, UsageData } from '../lib/types';

  interface Props {
    systemStatus?: SystemStatus | null;
    usage?: UsageData | null;
    renderQuality?: 'full' | 'ambient';
    interactive?: boolean;
    opacity?: number;
  }

  let { systemStatus = null, usage = null, renderQuality = 'full', interactive = true, opacity = 1 }: Props = $props();

  // Derive teammate data from active runs
  let teammates = $derived(
    agentPresence.activeRuns
      .filter(r => r.mode !== 'manager' && r.mode !== 'analyst')
      .map((r, i) => ({
        index: i,
        runId: r.run_id,
        issueNumber: r.issue_number,
        status: r.status,
        turns: r.turns ?? 0,
        mode: r.mode,
        branch: r.branch,
        model: r.model,
      }))
  );

  let completedCount = $derived(teammates.filter(t => t.status === 'completed' || t.status === 'success').length);
  let totalCount = $derived(teammates.length);
  let progressPct = $derived(totalCount > 0 ? Math.round((completedCount / totalCount) * 100) : 0);

  let totalTurns = $derived(teammates.reduce((sum, t) => sum + t.turns, 0));
  let totalTokens = $derived(agentPresence.tokensBurned ?? 0);

  // Estimate cost: ~$15/M input, ~$75/M output (rough average ~$25/M blended)
  let estimatedCost = $derived((totalTokens / 1_000_000) * 25);

  let phaseLabel = $derived(
    agentPresence.phase === 'coordinating' ? 'Coordinating'
    : agentPresence.phase === 'employee' ? 'Implementing'
    : agentPresence.phase === 'manager_review' ? 'Reviewing'
    : agentPresence.phase === 'executing_verdict' ? 'Verdict'
    : agentPresence.phase === 'idle' ? 'Standby'
    : agentPresence.phase.replace(/_/g, ' ')
  );

  let phaseColor = $derived(
    agentPresence.phase === 'coordinating' ? 'text-accent-purple'
    : agentPresence.phase === 'employee' ? 'text-info'
    : agentPresence.phase === 'manager_review' ? 'text-warning'
    : agentPresence.phase === 'executing_verdict' ? 'text-approve'
    : 'text-text-dim'
  );

  function phaseBadge(status: string, mode?: string): { label: string; color: string } {
    // Lead agent in agent-teams mode is coordinating, not implementing
    if (status === 'running' && mode === 'agent-teams') {
      return { label: 'Coordinating', color: 'bg-accent-purple/20 text-accent-purple' };
    }
    switch (status) {
      case 'running': return { label: 'Implementing', color: 'bg-info/20 text-info' };
      case 'plan_reviewing': return { label: 'Planning', color: 'bg-accent-blue/20 text-accent-blue' };
      case 'reviewing': return { label: 'In Review', color: 'bg-warning/20 text-warning' };
      case 'completed': case 'success': return { label: 'Done', color: 'bg-approve/20 text-approve' };
      case 'failed': return { label: 'Failed', color: 'bg-reject/20 text-reject' };
      default: return { label: 'Queued', color: 'bg-white/5 text-text-dim' };
    }
  }

  function handleRowClick(runId: string) {
    if (interactive) navigate(`/stream/${runId}`);
  }
</script>

<div class="w-full h-full flex flex-col" style="opacity: {opacity}">
  {#if totalCount === 0}
    <!-- Idle state -->
    <div class="flex-1 flex items-center justify-center">
      <div class="text-center">
        <div class="text-2xl text-text-dim/40 mb-2">No active team</div>
        <div class="text-xs text-text-dim/30 font-data">
          {phaseLabel} · {agentPresence.wsConnected ? 'Connected' : 'Offline'}
        </div>
      </div>
    </div>
  {:else}
    <!-- Team Header -->
    <div class="px-4 py-3 border-b border-border/30">
      <div class="flex items-center justify-between mb-2">
        <div class="flex items-center gap-3">
          <span class="text-sm font-medium text-text">{completedCount}/{totalCount} tasks done</span>
          <span class="text-xs {phaseColor} font-data">{phaseLabel}</span>
        </div>
        <div class="flex items-center gap-2">
          {#if usage}
            <span class="text-xs font-data text-text-dim">Plan {Math.round(usage.usage_percent)}%</span>
          {/if}
          <div class="flex items-center gap-1">
            <div class="w-1.5 h-1.5 rounded-full {agentPresence.wsConnected ? 'bg-approve' : 'bg-reject'}"></div>
            <span class="text-[10px] font-data {agentPresence.wsConnected ? 'text-approve' : 'text-reject'}">
              {agentPresence.wsConnected ? 'Live' : 'Offline'}
            </span>
          </div>
        </div>
      </div>
      <!-- Progress bar -->
      <div class="h-1.5 rounded-full bg-white/[0.04] overflow-hidden">
        <div
          class="h-full rounded-full bg-approve transition-all duration-500"
          style="width: {progressPct}%"
        ></div>
      </div>
    </div>

    <!-- Task Table -->
    <div class="flex-1 overflow-auto">
      <table class="w-full text-xs">
        <thead>
          <tr class="text-text-dim/60 font-data uppercase tracking-wider border-b border-border/20">
            <th class="text-left px-4 py-2">Issue</th>
            <th class="text-left px-3 py-2">Teammate</th>
            <th class="text-left px-3 py-2">Phase</th>
            <th class="text-left px-3 py-2 w-24">Progress</th>
            <th class="text-right px-4 py-2">Turns</th>
          </tr>
        </thead>
        <tbody>
          {#each teammates as tm (tm.runId)}
            {@const badge = phaseBadge(tm.status, tm.mode)}
            <tr
              class="border-b border-border/10 hover:bg-white/[0.02] transition-colors {interactive ? 'cursor-pointer' : ''}"
              onclick={() => handleRowClick(tm.runId)}
            >
              <td class="px-4 py-2.5">
                <div class="text-text font-medium">
                  {#if tm.issueNumber}#{tm.issueNumber}{:else}—{/if}
                </div>
                {#if tm.branch}
                  <div class="text-text-dim/50 text-[10px] font-data mt-0.5 truncate max-w-[140px]">{tm.branch}</div>
                {/if}
              </td>
              <td class="px-3 py-2.5">
                <span class="text-text-dim font-data">
                  {#if tm.mode === 'agent-teams'}Lead{:else}Teammate {tm.index + 1}{/if}
                </span>
              </td>
              <td class="px-3 py-2.5">
                <span class="inline-block px-2 py-0.5 rounded text-[10px] font-data {badge.color}">
                  {badge.label}
                </span>
              </td>
              <td class="px-3 py-2.5">
                <div class="flex items-center gap-2">
                  <div class="flex-1 h-1 rounded-full bg-white/[0.04] overflow-hidden">
                    <div
                      class="h-full rounded-full transition-all duration-300 {tm.status === 'completed' || tm.status === 'success' ? 'bg-approve' : tm.status === 'failed' ? 'bg-reject' : 'bg-info'}"
                      style="width: {tm.status === 'completed' || tm.status === 'success' ? 100 : tm.status === 'failed' ? 100 : Math.min(95, (tm.turns / 50) * 100)}%"
                    ></div>
                  </div>
                </div>
              </td>
              <td class="px-4 py-2.5 text-right">
                <span class="font-data text-text-dim">{tm.turns}t</span>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>

    <!-- Footer: totals -->
    <div class="px-4 py-2 border-t border-border/30 text-[10px] font-data text-text-dim/60 flex items-center justify-between">
      <span>{totalTurns} turns · {totalTokens > 0 ? `${(totalTokens / 1000).toFixed(1)}k tokens` : 'tokens n/a'}{estimatedCost > 0.01 ? ` · ~$${estimatedCost.toFixed(2)}` : ''}</span>
      <span>{totalCount} teammate{totalCount !== 1 ? 's' : ''}</span>
    </div>
  {/if}
</div>
