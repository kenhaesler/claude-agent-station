<script lang="ts">
  import { route, navigate } from '../../lib/router.svelte';
  import { agentPresence } from '../../lib/agent-presence.svelte';
  import { formatTokens } from '../../lib/format';

  let {
    onTrigger,
    triggering = false,
    sseConnected = false,
    activeCount = 0,
  }: {
    onTrigger?: () => void;
    triggering?: boolean;
    sseConnected?: boolean;
    activeCount?: number;
  } = $props();

  // Phase 1 of "The Bridge": an always-visible live token meter. Prefers the
  // current run's accumulated total; falls back to cumulative burn so the
  // counter has something to show between runs.
  let liveTokens = $derived(
    agentPresence.activeRuns[0]?.tokens_total ?? agentPresence.tokensBurned ?? 0
  );

  const navItems = [
    { label: 'Dispatch', page: 'command-center', path: '/' },
    { label: 'Mission', page: 'mission-control', path: '/mission-control' },
    { label: 'Fleet', page: 'agent-teams', path: '/agent-teams' },
    { label: 'Queue', page: 'queue', path: '/queue' },
    { label: 'Projects', page: 'projects', path: '/projects' },
    { label: 'Settings', page: 'settings', path: '/settings' },
  ] as const;

  function isActive(page: string): boolean {
    // Dispatch absorbs the old Runs surface — runs and run-detail
    // both highlight the Dispatch tab.
    if (page === 'command-center') {
      return route.page === 'command-center' || route.page === 'runs' || route.page === 'run-detail';
    }
    if (page === 'queue') return route.page === 'queue' || route.page === 'queue-detail';
    if (page === 'projects') return route.page === 'projects' || route.page === 'project-detail';
    return route.page === page;
  }
</script>

<nav
  class="sticky top-0 z-50 flex items-center justify-between"
  style="padding: 12px 28px; background: rgba(255,245,238,0.65); backdrop-filter: blur(24px) saturate(1.3); -webkit-backdrop-filter: blur(24px) saturate(1.3); border-bottom: 1px solid rgba(255,255,255,0.4); box-shadow: 0 4px 16px rgba(0,0,0,0.03);"
  aria-label="Main navigation"
>
  <!-- Left: Logo + Title -->
  <div class="flex items-center gap-2.5">
    <div
      class="flex items-center justify-center"
      style="width: 30px; height: 30px; border-radius: 9px; background: linear-gradient(135deg, #4A3728, #5C4435); font-weight: 800; font-size: 13px; color: #FFF5EE; box-shadow: 0 2px 8px rgba(74,55,40,0.12); animation: logo-glow 4s ease-in-out infinite;"
    >C</div>
    <span style="font-size: 15px; font-weight: 700; color: #3D2A1A;">Claude Station</span>
  </div>

  <!-- Center: Nav Pills -->
  <div
    class="flex gap-0.5"
    style="background: rgba(255,255,255,0.60); backdrop-filter: blur(12px); border: 1px solid rgba(255,255,255,0.7); border-radius: 14px; padding: 4px;"
  >
    {#each navItems as item}
      <button
        onclick={() => navigate(item.path)}
        class="nav-pill"
        class:active={isActive(item.page)}
        aria-current={isActive(item.page) ? 'page' : undefined}
        style="padding: 8px 20px; border-radius: 10px; font-size: 13px; font-weight: {isActive(item.page) ? '700' : '500'}; color: {isActive(item.page) ? '#4A3728' : '#9E8872'}; background: {isActive(item.page) ? 'white' : 'transparent'}; box-shadow: {isActive(item.page) ? '0 1px 4px rgba(0,0,0,0.06)' : 'none'}; border: none; cursor: pointer; font-family: inherit; transition: all 0.2s ease;"
      >
        {item.label}
      </button>
    {/each}
  </div>

  <!-- Right: Status + Trigger -->
  <div class="flex items-center gap-3.5">
    <div class="flex items-center gap-2.5" style="font-size: 12px; color: #8C7A66;">
      <div class="flex items-center gap-1.5">
        <div
          style="width: 7px; height: 7px; border-radius: 50%; background: {activeCount > 0 ? '#2E7D32' : '#22C55E'}; position: relative;"
        >
          <div style="position: absolute; inset: -3px; border-radius: 50%; background: rgba(34,197,94,0.2); animation: pulse-ring 2.5s ease-out infinite;"></div>
        </div>
        <span>{activeCount > 0 ? `Working (${activeCount})` : 'Idle'}</span>
      </div>
      {#if liveTokens > 0}
        <span style="color: #A08E7A;">·</span>
        <span
          data-testid="topnav-live-tokens"
          title="Tokens burned on the live (or last) run"
          style="font-variant-numeric: tabular-nums; color: #4E3A26; font-weight: 600;"
        >{formatTokens(liveTokens)} tok</span>
      {/if}
      <span style="color: #A08E7A;">·</span>
      <span style="color: {sseConnected ? '#2E7D32' : '#D06050'}; font-weight: 600;">SSE</span>
    </div>

    <button
      onclick={onTrigger}
      disabled={triggering}
      class="trigger-btn"
      style="padding: 10px 22px; border: none; border-radius: 12px; font-size: 14px; font-weight: 700; font-family: inherit; cursor: pointer; background: linear-gradient(135deg, #4A3728 0%, #5C4435 100%); color: #FFF5EE; box-shadow: 0 2px 8px rgba(74,55,40,0.18); transition: transform 0.15s, box-shadow 0.2s; opacity: {triggering ? '0.7' : '1'};"
    >
      {triggering ? '⏳ Triggering...' : '▶ Trigger Run'}
    </button>
  </div>
</nav>

<style>
  .trigger-btn:not(:disabled):hover {
    transform: scale(1.04);
    box-shadow: 0 4px 20px rgba(74, 55, 40, 0.25);
  }
</style>
