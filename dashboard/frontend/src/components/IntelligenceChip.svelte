<script lang="ts">
  import { getSuccessRate, getEscalationRate, getBackpressureStatus, isBackpressureElevated } from '../lib/intelligence-cache.svelte';

  interface Props {
    /** Display mode: 'success-rate' shows mode success %, 'escalation' shows rung info, 'backpressure' shows pressure */
    type: 'success-rate' | 'escalation' | 'backpressure';
    /** Mode name for success-rate type (e.g., 'analyze', 'full') */
    mode?: string;
    /** Escalation rung for escalation type */
    rung?: number;
    class?: string;
  }

  let { type, mode, rung, class: className = '' }: Props = $props();

  let chipText = $derived((() => {
    if (type === 'success-rate' && mode) {
      const rate = getSuccessRate(mode);
      if (!rate) return null;
      return `${mode}: ${Math.round(rate.success_rate * 100)}% success`;
    }
    if (type === 'escalation' && rung != null) {
      const stat = getEscalationRate(rung);
      if (!stat) return null;
      return `Rung ${rung}: ${Math.round(stat.success_rate * 100)}% success (${stat.total} runs)`;
    }
    if (type === 'backpressure') {
      const bp = getBackpressureStatus();
      if (!bp || bp.level === 'GREEN') return null;
      return `Backpressure: ${bp.level} — ${Math.round(bp.usage_percent)}% usage`;
    }
    return null;
  })());

  let chipVariant = $derived((() => {
    if (type === 'backpressure') {
      const bp = getBackpressureStatus();
      if (!bp) return 'info';
      return bp.level === 'RED' ? 'error' : bp.level === 'YELLOW' ? 'warning' : 'info';
    }
    if (type === 'success-rate' && mode) {
      const rate = getSuccessRate(mode);
      if (!rate) return 'info';
      return rate.success_rate >= 0.7 ? 'success' : rate.success_rate >= 0.4 ? 'warning' : 'error';
    }
    if (type === 'escalation') {
      const stat = rung != null ? getEscalationRate(rung) : null;
      if (!stat) return 'info';
      return stat.success_rate >= 0.5 ? 'info' : 'warning';
    }
    return 'info';
  })());

  const variantStyles: Record<string, string> = {
    info: 'bg-info/10 text-info',
    success: 'bg-approve/10 text-approve',
    warning: 'bg-warning/10 text-warning',
    error: 'bg-reject/10 text-reject',
  };
</script>

{#if chipText}
  <span
    class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium font-data {variantStyles[chipVariant]} {className}"
    title={chipText}
  >
    <span class="w-1 h-1 rounded-full {chipVariant === 'info' ? 'bg-info' : chipVariant === 'success' ? 'bg-approve' : chipVariant === 'warning' ? 'bg-warning' : 'bg-reject'}"></span>
    {chipText}
  </span>
{/if}
