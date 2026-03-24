<script lang="ts">
  import { route } from '../lib/router.svelte';
  import { agentPresence } from '../lib/agent-presence.svelte';

  const links = [
    { page: 'command', label: 'Pulse', icon: 'command' },
    { page: 'stream', label: 'Work', icon: 'stream' },
    { page: 'integration', label: 'Integrate', icon: 'integration' },
    { page: 'decide', label: 'Decide', icon: 'decide' },
    { page: 'brainstorm', label: 'Brainstorm', icon: 'brainstorm' },
    { page: 'agents', label: 'Agents', icon: 'agents' },
    { page: 'analytics', label: 'Stats', icon: 'analytics' },
    { page: 'config', label: 'Config', icon: 'config' },
  ] as const;

  function isActive(linkPage: string): boolean {
    if (linkPage === 'command') return route.page === 'command';
    if (linkPage === 'stream') return route.page === 'stream' || route.page === 'stream-detail';
    if (linkPage === 'decide') return route.page === 'decide' || route.page === 'decide-detail';
    if (linkPage === 'brainstorm') return route.page === 'brainstorm' || route.page === 'brainstorm-session';
    if (linkPage === 'agents') return route.page === 'agents' || route.page === 'agent-detail';
    if (linkPage === 'integration') return route.page === 'integration';
    if (linkPage === 'config') return route.page === 'config';
    if (linkPage === 'analytics') return route.page === 'analytics';
    return false;
  }
</script>

<nav class="flex md:flex-col items-center md:w-14 w-full md:h-full h-14 bg-surface border-r-0 md:border-r border-t md:border-t-0 border-border shrink-0 fixed bottom-0 md:relative md:bottom-auto z-40">
  {#each links as link}
    {@const active = isActive(link.page)}
    <a
      href="/{link.page}"
      class="relative flex flex-col items-center justify-center gap-0.5 flex-1 md:flex-none md:w-full md:py-3 transition-colors no-underline
        {active ? 'text-text bg-white/[0.04]' : 'text-text-muted hover:text-text-dim hover:bg-white/[0.02]'}"
      title={link.label}
    >
      {#if active}
        <span class="hidden md:block absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 bg-info rounded-r"></span>
        <span class="md:hidden absolute top-0 left-1/2 -translate-x-1/2 h-0.5 w-5 bg-info rounded-b"></span>
      {/if}

      <svg class="w-5 h-5" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        {#if link.icon === 'command'}
          <circle cx="10" cy="10" r="7" />
          <circle cx="10" cy="10" r="2.5" />
          <line x1="10" y1="3" x2="10" y2="5" />
          <line x1="10" y1="15" x2="10" y2="17" />
          <line x1="3" y1="10" x2="5" y2="10" />
          <line x1="15" y1="10" x2="17" y2="10" />
        {:else if link.icon === 'stream'}
          <path d="M3 5h14M3 10h10M3 15h12" />
          <circle cx="16" cy="10" r="1.5" fill="currentColor" stroke="none" />
        {:else if link.icon === 'integration'}
          <path d="M4 4v12M16 4v12" />
          <path d="M4 10h12" />
          <circle cx="4" cy="7" r="2" fill="currentColor" stroke="none" />
          <circle cx="16" cy="13" r="2" fill="currentColor" stroke="none" />
          <path d="M8 6l4 4-4 4" stroke-width="1.5" />
        {:else if link.icon === 'decide'}
          <path d="M4 4h12M4 8h12M4 12h8" />
          <path d="M13 12l2 2 4-4" stroke-width="2" />
        {:else if link.icon === 'brainstorm'}
          <circle cx="10" cy="7" r="4.5" />
          <path d="M6.5 11c-2 1-3.5 3-3.5 5h14c0-2-1.5-4-3.5-5" />
          <path d="M13 4.5c1.5-1 3.5-0.5 4 1s-0.5 3-2 3.5" />
          <path d="M7 4.5c-1.5-1-3.5-0.5-4 1s0.5 3 2 3.5" />
        {:else if link.icon === 'agents'}
          <circle cx="7" cy="7" r="3" />
          <circle cx="13" cy="7" r="3" />
          <circle cx="10" cy="14" r="3" />
          <line x1="9" y1="8" x2="10" y2="11" />
          <line x1="11" y1="8" x2="10" y2="11" />
        {:else if link.icon === 'analytics'}
          <path d="M3 17V10M7.5 17V6M12 17V3M16.5 17V8" stroke-width="2" stroke-linecap="round" />
        {:else if link.icon === 'config'}
          <circle cx="10" cy="10" r="3" />
          <path d="M10 1.5v2M10 16.5v2M1.5 10h2M16.5 10h2M3.4 3.4l1.4 1.4M15.2 15.2l1.4 1.4M3.4 16.6l1.4-1.4M15.2 4.8l1.4-1.4" />
        {/if}
      </svg>

      <span class="text-[10px] leading-tight">{link.label}</span>

      <!-- Decision badge -->
      {#if link.page === 'decide' && agentPresence.pendingDecisionCount > 0}
        <span class="absolute top-1 md:top-2 right-1 md:right-2 min-w-[16px] h-4 flex items-center justify-center px-1 rounded-full bg-warning text-[10px] font-bold text-bg">
          {agentPresence.pendingDecisionCount}
        </span>
      {/if}
    </a>
  {/each}
</nav>
