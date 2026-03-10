<script lang="ts">
  import { formatTokens } from '../lib/format';

  interface Props {
    tokens: number | null;
    maxTokens?: number;
    label?: string;
  }

  let { tokens, maxTokens = 1, label = '' }: Props = $props();

  let pct = $derived(tokens != null ? Math.min((tokens / maxTokens) * 100, 100) : 0);
  let barColor = $derived(
    tokens != null && tokens > maxTokens * 0.8 ? 'bg-reject' :
    tokens != null && tokens > maxTokens * 0.5 ? 'bg-warning' : 'bg-pr'
  );
  let glowColor = $derived(
    tokens != null && tokens > maxTokens * 0.8 ? 'shadow-[0_0_6px_rgba(239,68,68,0.2)]' :
    tokens != null && tokens > maxTokens * 0.5 ? 'shadow-[0_0_6px_rgba(245,158,11,0.2)]' : 'shadow-[0_0_6px_rgba(168,85,247,0.2)]'
  );
</script>

<div class="flex items-center gap-3">
  {#if label}
    <span class="text-xs text-text-dim w-24 truncate font-data">{label}</span>
  {/if}
  <div class="flex-1 h-2.5 bg-white/[0.04] rounded-full overflow-hidden">
    <div class="h-full rounded-full transition-all duration-700 ease-out {barColor} {glowColor}" style:width="{pct}%"></div>
  </div>
  <span class="text-xs text-text-dim w-16 text-right font-data">{formatTokens(tokens)}</span>
</div>
