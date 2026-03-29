<script lang="ts">
  import type { TokenUsage, BackpressureStatus, SystemStatus } from '../../lib/types';
  import { formatTokens, formatPercent } from '../../lib/format';

  let {
    activeCount,
    tokenUsage,
    backpressure,
    systemStatus,
    sseConnected,
    successRate,
  }: {
    activeCount: number;
    tokenUsage: TokenUsage | null;
    backpressure: BackpressureStatus | null;
    systemStatus: SystemStatus | null;
    sseConnected: boolean;
    successRate: number;
  } = $props();

  function getBackpressureDotClass(level: string | undefined): string {
    switch (level) {
      case 'GREEN': return 'status-dot online';
      case 'YELLOW': return 'status-dot warning';
      case 'RED': return 'status-dot error';
      case 'BLACK': return 'status-dot error';
      default: return 'status-dot offline';
    }
  }

  let serviceOnline = $derived(systemStatus?.service?.active ?? false);
</script>

<div class="h-10 bg-surface-0 border-t border-border flex items-center px-4 gap-6 text-xs font-mono text-secondary">
  <!-- Agents -->
  <div class="flex items-center gap-2 pr-6 border-r border-border">
    <div class={activeCount > 0 ? 'status-dot running' : 'status-dot offline'}></div>
    <span class={activeCount > 0 ? 'text-violet' : ''}>{activeCount} active</span>
  </div>

  <!-- Tokens -->
  <div class="flex items-center gap-2 pr-6 border-r border-border">
    <span class="text-tertiary">Tokens:</span>
    <span>{formatTokens(tokenUsage?.daily?.tokens_total ?? null)}</span>
    {#if tokenUsage?.max_usage_percent != null}
      <span class="text-tertiary">({formatPercent(tokenUsage.max_usage_percent)})</span>
    {/if}
  </div>

  <!-- Backpressure -->
  <div class="flex items-center gap-2 pr-6 border-r border-border">
    <div class={getBackpressureDotClass(backpressure?.level)}></div>
    <span>{backpressure?.level ?? 'N/A'}</span>
  </div>

  <!-- Success rate -->
  <div class="flex items-center gap-2 pr-6 border-r border-border">
    <span class={successRate >= 80 ? 'text-emerald' : successRate >= 50 ? 'text-amber' : 'text-rose'}>
      {formatPercent(successRate)}
    </span>
    <span class="text-tertiary">7d</span>
  </div>

  <!-- System status -->
  <div class="flex items-center gap-2 pr-6 border-r border-border">
    <div class={serviceOnline ? 'status-dot online' : 'status-dot error'}></div>
    <span>{serviceOnline ? 'Online' : 'Offline'}</span>
  </div>

  <!-- SSE connection -->
  <div class="flex items-center gap-2">
    <div class={sseConnected ? 'status-dot online' : 'status-dot error'}></div>
    <span>SSE</span>
  </div>
</div>
