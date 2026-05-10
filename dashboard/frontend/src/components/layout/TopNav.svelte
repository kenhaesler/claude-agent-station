<script lang="ts">
  import { route, navigate } from '../../lib/router.svelte';
  import { agentPresence } from '../../lib/agent-presence.svelte';
  import { appearance, setTheme } from '../../lib/appearance.svelte';
  import { pauseAll } from '../../lib/api';
  import { addToast } from '../../lib/toast.svelte';

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

  function fmtTok(n: number): string {
    if (!n) return '0';
    if (n < 1000) return String(n);
    return (n / 1000).toFixed(n < 10000 ? 1 : 0) + 'K';
  }

  const navItems = [
    { label: 'Dispatch', page: 'command-center', path: '/' },
    { label: 'Mission', page: 'mission-control', path: '/mission-control' },
    { label: 'Fleet', page: 'agent-teams', path: '/agent-teams' },
    { label: 'Queue', page: 'queue', path: '/queue' },
    { label: 'Projects', page: 'projects', path: '/projects' },
    { label: 'Settings', page: 'settings', path: '/settings' },
  ] as const;

  function isActive(page: string): boolean {
    if (page === 'command-center') {
      return route.page === 'command-center'
        || route.page === 'runs'
        || route.page === 'run-detail';
    }
    if (page === 'queue') return route.page === 'queue' || route.page === 'queue-detail';
    if (page === 'projects') return route.page === 'projects' || route.page === 'project-detail';
    return route.page === page;
  }

  function toggleTheme() {
    setTheme(appearance.theme === 'dark' ? 'light' : 'dark');
  }

  let stopping = $state(false);
  async function handleStop() {
    if (stopping) return;
    if (activeCount === 0) {
      // Idle — the Stop button doubles as the legacy trigger so the slot
      // isn't dead between runs.
      onTrigger?.();
      return;
    }
    stopping = true;
    try {
      await pauseAll();
      addToast('success', 'Global pause engaged — all agents will defer to the permission tray.');
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Pause failed';
      addToast('error', msg);
    } finally {
      stopping = false;
    }
  }
</script>

<header class="strip" aria-label="Main navigation">
  <div class="wordmark">
    <span class="name">Station</span>
    <span class="tag">OPERATOR · v1</span>
  </div>

  <nav class="strip-nav">
    {#each navItems as item}
      <button
        type="button"
        onclick={() => navigate(item.path)}
        class:active={isActive(item.page)}
        aria-current={isActive(item.page) ? 'page' : undefined}
      >{item.label}</button>
    {/each}
  </nav>

  <div class="strip-status">
    <span>
      <span class="dot {activeCount > 0 ? 'go live' : ''}"></span>
      <span class="pill">{activeCount > 0 ? `WORKING · ${activeCount}` : 'IDLE'}</span>
    </span>

    <span
      data-testid="topnav-live-tokens"
      title="Tokens burned on the live (or last) run"
      style="font-variant-numeric: tabular-nums;"
    >{fmtTok(liveTokens)} TOK</span>

    <span>
      SSE&nbsp;<b style="color: {sseConnected ? 'var(--go)' : 'var(--abort)'};">●</b>
    </span>

    <button
      type="button"
      class="btn-theme"
      onclick={toggleTheme}
      aria-label="Toggle theme"
      title="Toggle theme (t)"
    >
      {#if appearance.theme === 'dark'}
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="4"/>
          <line x1="12" y1="2" x2="12" y2="5"/>
          <line x1="12" y1="19" x2="12" y2="22"/>
          <line x1="4.2" y1="4.2" x2="6.3" y2="6.3"/>
          <line x1="17.7" y1="17.7" x2="19.8" y2="19.8"/>
          <line x1="2" y1="12" x2="5" y2="12"/>
          <line x1="19" y1="12" x2="22" y2="12"/>
          <line x1="4.2" y1="19.8" x2="6.3" y2="17.7"/>
          <line x1="17.7" y1="6.3" x2="19.8" y2="4.2"/>
        </svg>
      {:else}
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>
        </svg>
      {/if}
    </button>

    <button
      type="button"
      class="btn-stop"
      onclick={handleStop}
      disabled={stopping || triggering}
      title={activeCount > 0 ? 'Engage global pause (Cmd+. or Ctrl+.)' : 'Trigger run'}
    >
      {#if activeCount > 0}
        Stop <kbd>⌘.</kbd>
      {:else}
        {triggering ? '…' : 'Run'}
      {/if}
    </button>
  </div>
</header>
