<script lang="ts">
  import { getIntelligenceInsights, getIntelligenceDecisions, getBackpressure } from '../lib/api';
  import type { IntelligenceInsights, IntelligenceDecision, BackpressureStatus } from '../lib/types';
  import GlassCard from './GlassCard.svelte';
  import EscalationTimeline from './EscalationTimeline.svelte';
  import ConfidenceCalibration from './ConfidenceCalibration.svelte';

  let insights = $state<IntelligenceInsights | null>(null);
  let decisions = $state<IntelligenceDecision[]>([]);
  let pressure = $state<BackpressureStatus | null>(null);
  let expanded = $state(false);
  let loading = $state(false);

  async function loadData() {
    loading = true;
    try {
      const [insightsRes, decisionsRes, pressureRes] = await Promise.allSettled([
        getIntelligenceInsights(),
        getIntelligenceDecisions({ limit: 10 }),
        getBackpressure(),
      ]);
      if (insightsRes.status === 'fulfilled') insights = insightsRes.value;
      if (decisionsRes.status === 'fulfilled') decisions = decisionsRes.value;
      if (pressureRes.status === 'fulfilled') pressure = pressureRes.value;
    } catch { /* silent */ }
    finally { loading = false; }
  }

  function toggle() {
    expanded = !expanded;
    if (expanded && !insights) loadData();
  }

  function pressureColor(level: string): string {
    switch (level) {
      case 'GREEN': return 'text-approve';
      case 'YELLOW': return 'text-warning';
      case 'RED': return 'text-reject';
      case 'BLACK': return 'text-reject animate-pulse';
      default: return 'text-text-dim';
    }
  }

  function eventTypeLabel(type: string): string {
    const labels: Record<string, string> = {
      'intelligence.mode_selected': 'Mode Selected',
      'intelligence.confidence_gate_passed': 'Auto-PR Approved',
      'intelligence.confidence_gate_failed': 'Gate Failed',
      'intelligence.escalation_triggered': 'Escalation',
      'intelligence.adaptive_override': 'Adaptive Override',
      'intelligence.outcome_recorded': 'Outcome',
    };
    return labels[type] || type.replace('intelligence.', '');
  }

  function parseEventData(data: string): Record<string, unknown> {
    try { return JSON.parse(data); }
    catch { return {}; }
  }

  $effect(() => {
    if (expanded) {
      loadData();
      const interval = setInterval(loadData, 30000);
      return () => clearInterval(interval);
    }
  });
</script>

<div>
  <button
    onclick={toggle}
    class="w-full text-left text-xs text-text-dim hover:text-text cursor-pointer flex items-center gap-1.5 py-1"
  >
    <span class="text-[10px]">{expanded ? '▾' : '▸'}</span>
    Intelligence
    {#if insights}
      <span class="text-text-muted">({insights.total_samples} samples)</span>
    {/if}
    {#if pressure}
      <span class="ml-auto {pressureColor(pressure.level)} text-[10px] font-data">{pressure.level}</span>
    {/if}
  </button>

  {#if expanded}
    <div class="space-y-3 mt-2 animate-fade-in-up">
      <!-- Backpressure Gauge -->
      {#if pressure}
        <GlassCard class="p-3">
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs font-medium text-text">Backpressure</span>
            <span class="text-xs font-data {pressureColor(pressure.level)}">{pressure.level}</span>
          </div>
          <div class="w-full h-1.5 bg-white/[0.05] rounded-full overflow-hidden">
            <div
              class="h-full rounded-full transition-all duration-500 {
                pressure.level === 'GREEN' ? 'bg-approve' :
                pressure.level === 'YELLOW' ? 'bg-warning' : 'bg-reject'
              }"
              style="width: {Math.min(pressure.usage_percent, 100)}%"
            ></div>
          </div>
          <div class="flex justify-between text-[10px] text-text-muted mt-1 font-data">
            <span>{Math.round(pressure.usage_percent)}% usage</span>
            <span>{pressure.effective_concurrent}/{pressure.max_concurrent} concurrent</span>
          </div>
        </GlassCard>
      {/if}

      <!-- Success Rates by Mode -->
      {#if insights && insights.success_rates.length > 0}
        <GlassCard class="p-3">
          <h4 class="text-xs font-medium text-text mb-2">Success Rate by Mode</h4>
          <div class="space-y-1.5">
            {#each insights.success_rates as rate}
              <div class="flex items-center justify-between text-[11px]">
                <div class="flex items-center gap-1.5">
                  <span class="px-1.5 py-0.5 rounded text-[10px] bg-info/15 text-info">{rate.mode}</span>
                  <span class="text-text-muted truncate max-w-[120px]">{rate.model.split('-').slice(-2).join('-')}</span>
                </div>
                <div class="flex items-center gap-2 font-data">
                  <span class="{rate.success_rate >= 0.7 ? 'text-approve' : rate.success_rate >= 0.4 ? 'text-warning' : 'text-reject'}">
                    {(rate.success_rate * 100).toFixed(0)}%
                  </span>
                  <span class="text-text-muted">{rate.total} runs</span>
                </div>
              </div>
            {/each}
          </div>
        </GlassCard>
      {/if}

      <!-- Confidence Calibration -->
      {#if insights && insights.calibration.length > 0}
        <ConfidenceCalibration data={insights.calibration} />
      {/if}

      <!-- Escalation Stats -->
      {#if insights && insights.escalation_stats.length > 1}
        <EscalationTimeline data={insights.escalation_stats} />
      {/if}

      <!-- Recent Decisions -->
      {#if decisions.length > 0}
        <GlassCard class="p-3">
          <h4 class="text-xs font-medium text-text mb-2">Recent Decisions</h4>
          <div class="space-y-1.5 max-h-[200px] overflow-auto">
            {#each decisions as decision}
              {@const eventData = parseEventData(decision.event_data)}
              <div class="flex items-start gap-2 text-[11px] py-1 border-b border-border/20 last:border-0">
                <span class="px-1.5 py-0.5 rounded text-[10px] whitespace-nowrap {
                  decision.event_type.includes('passed') ? 'bg-approve/15 text-approve' :
                  decision.event_type.includes('escalation') ? 'bg-warning/15 text-warning' :
                  decision.event_type.includes('failed') ? 'bg-reject/15 text-reject' :
                  'bg-info/15 text-info'
                }">
                  {eventTypeLabel(decision.event_type)}
                </span>
                <div class="flex-1 min-w-0">
                  {#if eventData.mode}
                    <span class="text-text-dim">{eventData.mode}</span>
                  {/if}
                  {#if eventData.reasoning}
                    <span class="text-text-muted truncate block">{eventData.reasoning}</span>
                  {/if}
                  {#if eventData.confidence}
                    <span class="text-text-muted font-data">conf: {eventData.confidence}</span>
                  {/if}
                </div>
                {#if decision.created_at}
                  <span class="text-[10px] text-text-muted font-data whitespace-nowrap">
                    {new Date(decision.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </span>
                {/if}
              </div>
            {/each}
          </div>
        </GlassCard>
      {/if}

      <!-- Learning Status -->
      {#if insights}
        <div class="text-[10px] text-text-muted text-center font-data">
          Learning loop: {insights.total_samples} samples | {insights.intelligence_event_count} events
        </div>
      {/if}
    </div>
  {/if}
</div>
