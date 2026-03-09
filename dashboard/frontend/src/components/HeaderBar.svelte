<script lang="ts">
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

  let authColor = $derived(
    authOk ? 'bg-approve' : 'bg-reject'
  );

  let authLabel = $derived(
    authOk ? 'Auth OK' : 'Auth Error'
  );

  let usageBarColor = $derived(
    usagePercent > 80 ? 'bg-reject' :
    usagePercent > 60 ? 'bg-warning' : 'bg-approve'
  );
</script>

<header class="h-12 w-full flex items-center justify-between px-3 md:px-4 bg-surface border-b border-border shrink-0">
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
    <span class="md:hidden text-pr font-bold text-sm">Claude Station</span>
  </div>

  <!-- Spacer on desktop -->
  <div class="hidden md:block"></div>

  <!-- Status indicators + trigger button -->
  <div class="flex items-center gap-2 md:gap-4">
    <!-- Service status -->
    <div class="flex items-center gap-1.5">
      <span
        class="inline-block w-2 h-2 rounded-full {serviceActive ? 'bg-approve' : 'bg-reject'}"
      ></span>
      <span class="text-xs text-text-dim hidden sm:inline">
        {serviceActive ? 'Active' : 'Down'}
      </span>
    </div>

    <!-- Auth status -->
    <div class="flex items-center gap-1.5">
      <span class="inline-block w-2 h-2 rounded-full {authColor}"></span>
      <span class="text-xs text-text-dim hidden sm:inline">{authLabel}</span>
    </div>

    <!-- Usage meter -->
    <div class="flex items-center gap-1.5 hidden sm:flex">
      <span class="text-xs text-text-dim">{sessionsUsed}/{sessionLimit}</span>
      <div class="w-16 md:w-20 h-2 bg-surface-2 rounded-full overflow-hidden">
        <div
          class="h-full rounded-full transition-all duration-500 {usageBarColor}"
          style:width="{Math.min(usagePercent, 100)}%"
        ></div>
      </div>
    </div>

    <!-- Trigger Run button -->
    <button
      onclick={onTrigger}
      disabled={triggering}
      class="px-2.5 md:px-3 py-1 text-xs font-medium rounded-md bg-pr text-white transition-opacity cursor-pointer
        {triggering ? 'opacity-50 cursor-not-allowed' : 'hover:opacity-90'}"
    >
      {triggering ? '...' : 'Run'}
    </button>
  </div>
</header>
