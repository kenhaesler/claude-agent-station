<script lang="ts">
  import { getUsage, getTokenUsage } from '../lib/api';
  import type { UsageData, TokenUsageData } from '../lib/types';
  import ArcGauge from './ArcGauge.svelte';

  let loading = $state(true);
  let error = $state('');
  let usage = $state<UsageData | null>(null);
  let tokenUsage = $state<TokenUsageData | null>(null);

  async function loadUsage() {
    try {
      const [usageRes, tokenRes] = await Promise.allSettled([
        getUsage(),
        getTokenUsage(),
      ]);
      if (usageRes.status === 'fulfilled') usage = usageRes.value;
      if (tokenRes.status === 'fulfilled') tokenUsage = tokenRes.value;
      error = '';
    } catch (e: any) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    loadUsage();
    const interval = setInterval(loadUsage, 30000);
    return () => clearInterval(interval);
  });

  function formatHours(h: number): string {
    const hrs = Math.floor(h);
    const mins = Math.round((h - hrs) * 60);
    if (hrs === 0) return `${mins}m`;
    if (mins === 0) return `${hrs}h`;
    return `${hrs}h ${mins}m`;
  }

  function formatTokens(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
    return String(n);
  }

  let sessionColor = $derived(
    !usage ? '#94a3b8' :
    usage.usage_percent >= 80 ? '#ef4444' :
    usage.usage_percent >= 60 ? '#f59e0b' :
    usage.usage_percent >= 30 ? '#06b6d4' : '#10b981'
  );

  let agentCapColor = $derived(
    !tokenUsage ? '#94a3b8' :
    tokenUsage.max_usage_percent >= 80 ? '#f59e0b' : '#3b82f6'
  );
</script>

<div class="space-y-4">
  {#if loading}
    <div class="flex items-center gap-2 text-text-dim text-sm">
      <div class="w-4 h-4 border-2 border-accent-cyan/30 border-t-accent-cyan rounded-full animate-spin"></div>
      Loading plan usage...
    </div>
  {:else if error}
    <div class="text-xs text-reject/80 bg-reject/10 rounded-lg px-3 py-2">
      Failed to load usage: {error}
    </div>
  {:else}
    <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
      <!-- Session Usage -->
      <div class="flex items-center gap-3 bg-white/[0.02] rounded-lg px-3 py-3 border border-border/20">
        <ArcGauge value={usage?.usage_percent ?? 0} size={56} color={sessionColor} label="SESSION" />
        <div class="min-w-0">
          <div class="text-sm font-medium text-text">Session</div>
          <div class="text-xs text-text-dim font-data">
            {usage?.sessions_used ?? 0} used
          </div>
          {#if usage && usage.window_remaining_hours > 0}
            <div class="text-[10px] text-text-dim font-data mt-0.5">
              Resets in {formatHours(usage.window_remaining_hours)}
            </div>
          {/if}
        </div>
      </div>

      <!-- Token Usage (Daily) -->
      <div class="flex items-center gap-3 bg-white/[0.02] rounded-lg px-3 py-3 border border-border/20">
        <ArcGauge
          value={0}
          size={56}
          color="#06b6d4"
          label="TOKENS"
        />
        <div class="min-w-0">
          <div class="text-sm font-medium text-text">Tokens (24h)</div>
          <div class="text-xs text-text-dim font-data">
            {formatTokens(tokenUsage?.daily.tokens_total ?? 0)} consumed
          </div>
          <div class="text-[10px] text-text-dim font-data mt-0.5">
            In: {formatTokens(tokenUsage?.daily.tokens_input ?? 0)} / Out: {formatTokens(tokenUsage?.daily.tokens_output ?? 0)}
          </div>
        </div>
      </div>

      <!-- Agent Cap -->
      <div class="flex items-center gap-3 bg-white/[0.02] rounded-lg px-3 py-3 border border-border/20">
        <ArcGauge
          value={tokenUsage?.max_usage_percent ?? 60}
          size={56}
          color={agentCapColor}
          label="CAP"
        />
        <div class="min-w-0">
          <div class="text-sm font-medium text-text">Agent Cap</div>
          <div class="text-xs text-text-dim font-data">
            {tokenUsage?.max_usage_percent ?? 60}% of plan
          </div>
          <div class="text-[10px] text-text-dim font-data mt-0.5">
            {tokenUsage?.reserve_percent ?? 40}% reserved
          </div>
        </div>
      </div>
    </div>

    <!-- Monthly summary -->
    {#if tokenUsage}
      <div class="text-xs text-text-dim bg-white/[0.02] rounded-lg px-3 py-2 border border-border/20 font-data">
        Monthly total: {formatTokens(tokenUsage.monthly.tokens_total)} tokens consumed (30-day window)
      </div>
    {/if}
  {/if}
</div>
