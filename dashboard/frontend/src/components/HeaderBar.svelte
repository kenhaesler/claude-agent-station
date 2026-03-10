<script lang="ts">
  import StatusOrb from './StatusOrb.svelte';

  interface Props {
    serviceActive: boolean;
    authOk: boolean;
    usagePercent: number;
    sessionsUsed: number;
    sessionLimit: number;
    onTrigger: () => void;
    triggering: boolean;
    onMenuToggle?: () => void;
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
  }: Props = $props();

  let usageBarColor = $derived(
    usagePercent > 80 ? 'bg-reject' :
    usagePercent > 60 ? 'bg-warning' : 'bg-accent-emerald'
  );

  let usageGlow = $derived(
    usagePercent > 80 ? 'shadow-[0_0_8px_rgba(239,68,68,0.3)]' :
    usagePercent > 60 ? '' : 'shadow-[0_0_8px_rgba(16,185,129,0.2)]'
  );
</script>

<header class="h-12 w-full flex items-center justify-between px-3 md:px-4 glass-heavy border-0 border-b border-border/50 shrink-0">
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
    <span class="md:hidden text-accent-blue font-bold text-sm text-glow-blue">Claude Station</span>
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

    <!-- Usage meter -->
    <div class="flex items-center gap-1.5 hidden sm:flex">
      <span class="text-xs text-text-dim font-data">{sessionsUsed}/{sessionLimit}</span>
      <div class="w-16 md:w-20 h-1.5 bg-white/[0.05] rounded-full overflow-hidden">
        <div
          class="h-full rounded-full transition-all duration-500 {usageBarColor} {usageGlow}"
          style:width="{Math.min(usagePercent, 100)}%"
        ></div>
      </div>
    </div>

    <!-- Trigger Run button -->
    <button
      onclick={onTrigger}
      disabled={triggering}
      class="px-2.5 md:px-3 py-1 text-xs font-medium rounded-md text-white transition-all cursor-pointer
        bg-gradient-to-r from-accent-blue to-accent-emerald hover:shadow-[0_0_16px_rgba(59,130,246,0.3)]
        {triggering ? 'opacity-50 cursor-not-allowed' : 'hover:opacity-90 active:scale-95'}"
    >
      {triggering ? '...' : 'Run'}
    </button>
  </div>
</header>
