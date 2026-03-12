<script lang="ts">
  interface Props {
    maxUsagePercent: number;
    reservePercent: number;
    onMaxUsageChange: (value: number) => void;
    onReserveChange: (value: number) => void;
  }

  let { maxUsagePercent, reservePercent, onMaxUsageChange, onReserveChange }: Props = $props();

  function handleMaxUsageInput(e: Event) {
    const val = parseInt((e.target as HTMLInputElement).value);
    onMaxUsageChange(val);
    // Auto-adjust reserve to not exceed remaining
    if (val + reservePercent > 100) {
      onReserveChange(100 - val);
    }
  }

  function handleReserveInput(e: Event) {
    const val = parseInt((e.target as HTMLInputElement).value);
    onReserveChange(val);
    // Auto-adjust max usage to not exceed remaining
    if (maxUsagePercent + val > 100) {
      onMaxUsageChange(100 - val);
    }
  }

  let usageColor = $derived(
    maxUsagePercent <= 50 ? '#10b981' :
    maxUsagePercent <= 70 ? '#6366f1' :
    maxUsagePercent <= 85 ? '#f59e0b' : '#ef4444'
  );

  let reserveColor = $derived(
    reservePercent >= 40 ? '#10b981' :
    reservePercent >= 20 ? '#6366f1' :
    reservePercent >= 10 ? '#f59e0b' : '#ef4444'
  );

  let unallocated = $derived(Math.max(0, 100 - maxUsagePercent - reservePercent));
</script>

<div class="space-y-6">
  <!-- Max Usage Slider -->
  <div>
    <div class="flex items-center justify-between mb-2">
      <label for="max-usage" class="text-sm font-medium text-text">Max Agent Usage</label>
      <span class="font-data text-lg font-bold" style="color: {usageColor}">{maxUsagePercent}%</span>
    </div>
    <input
      id="max-usage"
      type="range"
      min="10"
      max="90"
      step="5"
      value={maxUsagePercent}
      oninput={handleMaxUsageInput}
      class="usage-slider w-full"
      style="--slider-color: {usageColor}"
    />
    <p class="text-xs text-text-dim mt-1.5">
      Agent will stop spawning employees when Claude plan usage exceeds this threshold.
      {#if maxUsagePercent === 60}
        <span class="text-accent-emerald">(Recommended)</span>
      {/if}
    </p>
  </div>

  <!-- Reserve Slider -->
  <div>
    <div class="flex items-center justify-between mb-2">
      <label for="reserve" class="text-sm font-medium text-text">Reserve for Manual Use</label>
      <span class="font-data text-lg font-bold" style="color: {reserveColor}">{reservePercent}%</span>
    </div>
    <input
      id="reserve"
      type="range"
      min="0"
      max="80"
      step="5"
      value={reservePercent}
      oninput={handleReserveInput}
      class="usage-slider w-full"
      style="--slider-color: {reserveColor}"
    />
    <p class="text-xs text-text-dim mt-1.5">
      Keeps this portion of your plan available for your own Claude usage.
      {#if reservePercent === 40}
        <span class="text-accent-emerald">(Recommended)</span>
      {/if}
    </p>
  </div>

  <!-- Visual Budget Bar -->
  <div class="mt-4">
    <div class="flex items-center gap-2 mb-2">
      <span class="text-xs text-text-dim uppercase tracking-wider">Budget Allocation</span>
    </div>
    <div class="h-3 rounded-full overflow-hidden flex bg-white/[0.04] border border-border/30">
      <div
        class="h-full transition-all duration-300"
        style="width: {maxUsagePercent}%; background: {usageColor}"
        title="Agent: {maxUsagePercent}%"
      ></div>
      <div
        class="h-full transition-all duration-300"
        style="width: {reservePercent}%; background: {reserveColor}; opacity: 0.6"
        title="Reserved: {reservePercent}%"
      ></div>
      {#if unallocated > 0}
        <div
          class="h-full transition-all duration-300 bg-white/[0.06]"
          style="width: {unallocated}%"
          title="Unallocated: {unallocated}%"
        ></div>
      {/if}
    </div>
    <div class="flex justify-between text-[10px] text-text-dim mt-1.5 font-data">
      <span style="color: {usageColor}">Agent {maxUsagePercent}%</span>
      <span style="color: {reserveColor}">Reserved {reservePercent}%</span>
      {#if unallocated > 0}
        <span>Buffer {unallocated}%</span>
      {/if}
    </div>
  </div>
</div>

<style>
  .usage-slider {
    -webkit-appearance: none;
    appearance: none;
    height: 6px;
    border-radius: 3px;
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(71, 85, 105, 0.3);
    outline: none;
    cursor: pointer;
  }

  .usage-slider::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: var(--slider-color, #6366f1);
    border: 2px solid rgba(255, 255, 255, 0.15);
    box-shadow: 0 0 10px color-mix(in srgb, var(--slider-color, #6366f1) 40%, transparent);
    cursor: pointer;
    transition: box-shadow 0.2s ease;
  }

  .usage-slider::-webkit-slider-thumb:hover {
    box-shadow: 0 0 16px color-mix(in srgb, var(--slider-color, #6366f1) 60%, transparent);
  }

  .usage-slider::-moz-range-thumb {
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: var(--slider-color, #6366f1);
    border: 2px solid rgba(255, 255, 255, 0.15);
    box-shadow: 0 0 10px color-mix(in srgb, var(--slider-color, #6366f1) 40%, transparent);
    cursor: pointer;
  }

  .usage-slider::-moz-range-track {
    height: 6px;
    border-radius: 3px;
    background: rgba(255, 255, 255, 0.04);
  }
</style>
