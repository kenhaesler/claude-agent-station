<script lang="ts">
  import StatusOrb from './StatusOrb.svelte';
  import ArcGauge from './ArcGauge.svelte';
  import AiStatusLine from './AiStatusLine.svelte';

  interface Props {
    serviceActive: boolean;
    authOk: boolean;
    usagePercent: number;
    sessionsUsed: number;
    sessionLimit: number;
    onTrigger: () => void;
    triggering: boolean;
    onMenuToggle?: () => void;
    projectCount?: number;
  }

  let {
    serviceActive,
    authOk,
    usagePercent,
    sessionsUsed,
    sessionLimit,
    onTrigger,
    triggering,
    onMenuToggle,
    projectCount = 0,
  }: Props = $props();

  let usageColor = $derived(
    usagePercent > 80 ? '#ef4444' :
    usagePercent > 60 ? '#f59e0b' : '#06b6d4'
  );

  let statusMessages = $derived([
    serviceActive ? 'Systems nominal' : 'Systems offline',
    `Session capacity ${Math.round(100 - usagePercent)}%`,
    projectCount > 0 ? `Monitoring ${projectCount} projects` : 'Awaiting directives',
  ]);
</script>

<header class="header-bar h-12 w-full flex items-center justify-between px-3 md:px-4 glass-hud shrink-0">
  <!-- Left: hamburger (mobile) + station name -->
  <div class="flex items-center gap-2">
    <button
      onclick={onMenuToggle}
      class="md:hidden p-1.5 text-text-dim hover:text-text cursor-pointer"
      title="Menu"
    >
      <svg class="w-5 h-5" viewBox="0 0 20 20" fill="currentColor">
        <path d="M3 5h14M3 10h14M3 15h14" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" />
      </svg>
    </button>
    <span class="md:hidden text-accent-cyan font-bold text-sm text-glow-cyan">Claude Station</span>
    <!-- AI Status text (desktop only) -->
    <div class="hidden md:block">
      <AiStatusLine messages={statusMessages} speed={45} />
    </div>
  </div>

  <!-- Status indicators + trigger button -->
  <div class="flex items-center gap-2 md:gap-4">
    <!-- Service status -->
    <div class="flex items-center gap-1.5">
      <StatusOrb active={serviceActive} />
      <span class="text-xs text-text-dim hidden sm:inline">
        {serviceActive ? 'Active' : 'Down'}
      </span>
    </div>

    <!-- Auth status -->
    <div class="flex items-center gap-1.5">
      <StatusOrb active={authOk} />
      <span class="text-xs text-text-dim hidden sm:inline">
        {authOk ? 'Auth OK' : 'Auth Error'}
      </span>
    </div>

    <!-- Usage arc gauge (desktop) / text (mobile) -->
    <div class="flex items-center gap-1.5 hidden sm:flex">
      <span class="text-xs text-text-dim font-data hidden md:hidden lg:inline">{sessionsUsed}/{sessionLimit}</span>
      <ArcGauge value={usagePercent} size={32} color={usageColor} />
    </div>

    <!-- Trigger Run button -->
    <button
      onclick={onTrigger}
      disabled={triggering}
      class="run-btn px-2.5 md:px-3 py-1 text-xs font-medium rounded-md text-white transition-all cursor-pointer
        bg-gradient-to-r from-accent-cyan to-accent-emerald hover:shadow-[0_0_16px_rgba(6,182,212,0.3)]
        {triggering ? 'opacity-50 cursor-not-allowed animate-glow-pulse' : 'hover:opacity-90 active:scale-95'}"
    >
      {triggering ? '...' : 'Run'}
    </button>
  </div>
</header>

<style>
  .header-bar {
    border-bottom: 1px solid transparent;
    border-image: linear-gradient(90deg, transparent, rgba(6, 182, 212, 0.25), transparent) 1;
  }
</style>
