<script lang="ts">
  import { route, navigate } from '../../lib/router.svelte';
  import { agentPresence } from '../../lib/agent-presence.svelte';

  interface NavItem {
    id: string;
    icon: string;
    label: string;
    path: string;
    pages: string[];
    badge?: () => number;
    pulse?: () => boolean;
  }

  const items: NavItem[] = [
    {
      id: 'home', icon: '⌂', label: 'Command Center', path: '/',
      pages: ['command-center'],
    },
    {
      id: 'theater', icon: '◉', label: 'Agent Theater', path: '/theater',
      pages: ['theater'],
      pulse: () => agentPresence.activeRuns.length > 0,
    },
    {
      id: 'runs', icon: '▷', label: 'Runs', path: '/runs',
      pages: ['runs', 'run-detail'],
    },
    {
      id: 'queue', icon: '☰', label: 'Queue', path: '/queue',
      pages: ['queue', 'queue-detail'],
    },
    {
      id: 'intelligence', icon: '◈', label: 'Intelligence', path: '/intelligence',
      pages: ['intelligence'],
    },
    {
      id: 'projects', icon: '▤', label: 'Projects', path: '/projects',
      pages: ['projects', 'project-detail'],
    },
    {
      id: 'integration', icon: '⇉', label: 'Integration', path: '/integration',
      pages: ['integration'],
    },
    {
      id: 'brainstorm', icon: '✦', label: 'Brainstorm', path: '/brainstorm',
      pages: ['brainstorm', 'brainstorm-session'],
    },
  ];

  const bottomItems: NavItem[] = [
    {
      id: 'settings', icon: '⚙', label: 'Settings', path: '/settings',
      pages: ['settings'],
    },
  ];

  function isActive(item: NavItem): boolean {
    return item.pages.includes(route.page);
  }
</script>

<nav
  class="flex flex-col items-center w-14 bg-surface-solid border-r border-border-subtle py-3 shrink-0 z-nav"
  aria-label="Main navigation"
>
  <!-- Logo -->
  <a
    href="/"
    class="flex items-center justify-center w-9 h-9 rounded-lg mb-4 text-lg font-bold
           bg-accent-blue/20 text-accent-blue hover:bg-accent-blue/30 transition-colors"
    aria-label="Home"
  >
    C
  </a>

  <!-- Main nav items -->
  <div class="flex flex-col items-center gap-1 flex-1">
    {#each items as item}
      <button
        onclick={() => navigate(item.path)}
        class="relative flex items-center justify-center w-10 h-10 rounded-lg text-base
               transition-all duration-fast
               {isActive(item)
                 ? 'bg-surface-2 text-text'
                 : 'text-text-muted hover:text-text-dim hover:bg-surface/60'}"
        title="{item.label}"
        aria-label="{item.label}"
        aria-current={isActive(item) ? 'page' : undefined}
      >
        <span class="relative">
          {item.icon}
          {#if item.pulse?.()}
            <span class="absolute -top-0.5 -right-1 w-2 h-2 rounded-full bg-status-active animate-pulse"></span>
          {/if}
        </span>
        {#if isActive(item)}
          <span class="absolute left-0 top-2 bottom-2 w-0.5 rounded-r bg-accent-blue"></span>
        {/if}
      </button>
    {/each}
  </div>

  <!-- Bottom items -->
  <div class="flex flex-col items-center gap-1">
    {#each bottomItems as item}
      <button
        onclick={() => navigate(item.path)}
        class="flex items-center justify-center w-10 h-10 rounded-lg text-base
               transition-all duration-fast
               {isActive(item)
                 ? 'bg-surface-2 text-text'
                 : 'text-text-muted hover:text-text-dim hover:bg-surface/60'}"
        title="{item.label}"
        aria-label="{item.label}"
      >
        {item.icon}
      </button>
    {/each}
  </div>
</nav>
