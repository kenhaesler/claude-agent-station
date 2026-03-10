<script lang="ts">
  import { route } from '../lib/router.svelte';

  interface Props {
    mobileOpen?: boolean;
    onclose?: () => void;
  }

  let { mobileOpen = $bindable(false), onclose }: Props = $props();

  let collapsed = $state(false);

  const links = [
    { page: 'dashboard', label: 'Dashboard' },
    { page: 'projects', label: 'Projects' },
    { page: 'plans', label: 'Plans' },
    { page: 'runs', label: 'Runs' },
    { page: 'logs', label: 'Logs' },
    { page: 'config', label: 'Config' },
    { page: 'system', label: 'System' },
  ] as const;

  function closeMobile() {
    mobileOpen = false;
    onclose?.();
  }
</script>

<!-- Mobile backdrop -->
{#if mobileOpen}
  <div
    class="fixed inset-0 bg-black/60 backdrop-blur-sm z-30 md:hidden"
    onclick={closeMobile}
    role="presentation"
  ></div>
{/if}

<!-- Sidebar -->
<aside
  class="flex flex-col glass-heavy transition-all duration-200 shrink-0
    fixed inset-y-0 left-0 z-40 md:relative md:translate-x-0
    {mobileOpen ? 'translate-x-0' : '-translate-x-full'}"
  style:width={collapsed ? '56px' : '220px'}
>
  <div class="flex items-center gap-2 px-4 h-12 border-b border-border/50">
    {#if !collapsed}
      <span class="text-accent-blue font-bold text-lg text-glow-blue">Claude Station</span>
    {/if}
    <!-- Close button on mobile -->
    <button
      onclick={closeMobile}
      class="ml-auto text-text-dim hover:text-text text-sm cursor-pointer md:hidden"
      title="Close"
    >
      &times;
    </button>
    <!-- Collapse button on desktop -->
    <button
      onclick={() => collapsed = !collapsed}
      class="ml-auto text-text-dim hover:text-text text-sm cursor-pointer hidden md:block"
      title={collapsed ? 'Expand' : 'Collapse'}
    >
      {collapsed ? '▸' : '◂'}
    </button>
  </div>

  <nav class="flex flex-col gap-1 px-2 py-3 flex-1">
    {#each links as link}
      {@const active = route.page === link.page || (link.page === 'runs' && route.page === 'run-detail') || (link.page === 'plans' && route.page === 'plan-detail')}
      <a
        href="#{link.page === 'dashboard' ? '/' : `/${link.page}`}"
        onclick={closeMobile}
        class="relative flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-all duration-200 no-underline
          {active
            ? 'bg-accent-blue/10 text-accent-blue font-medium'
            : 'text-text-dim hover:text-text hover:bg-white/[0.03] hover:translate-x-0.5'}"
      >
        {#if active}
          <span class="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 bg-accent-blue rounded-r shadow-[0_0_8px_rgba(59,130,246,0.5)]"></span>
        {/if}
        <svg class="w-5 h-5 shrink-0" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          {#if link.page === 'dashboard'}
            <rect x="2" y="2" width="7" height="7" rx="1" /><rect x="11" y="2" width="7" height="7" rx="1" /><rect x="2" y="11" width="7" height="7" rx="1" /><rect x="11" y="11" width="7" height="7" rx="1" />
          {:else if link.page === 'projects'}
            <path d="M2 6V16a1 1 0 001 1h14a1 1 0 001-1V8a1 1 0 00-1-1h-7l-2-2H3a1 1 0 00-1 1z" />
          {:else if link.page === 'plans'}
            <path d="M4 4h12M4 8h12M4 12h8" /><path d="M13 12l2 2 4-4" stroke-width="2" />
          {:else if link.page === 'runs'}
            <circle cx="10" cy="10" r="8" /><polygon points="8,6 15,10 8,14" fill="currentColor" stroke="none" />
          {:else if link.page === 'logs'}
            <rect x="3" y="2" width="14" height="16" rx="1" /><line x1="6" y1="6" x2="14" y2="6" /><line x1="6" y1="9.5" x2="14" y2="9.5" /><line x1="6" y1="13" x2="11" y2="13" />
          {:else if link.page === 'config'}
            <circle cx="10" cy="10" r="3" /><path d="M10 1.5v2M10 16.5v2M1.5 10h2M16.5 10h2M3.4 3.4l1.4 1.4M15.2 15.2l1.4 1.4M3.4 16.6l1.4-1.4M15.2 4.8l1.4-1.4" />
          {:else if link.page === 'system'}
            <rect x="3" y="2" width="14" height="5" rx="1" /><rect x="3" y="9" width="14" height="5" rx="1" /><circle cx="6" cy="4.5" r="0.75" fill="currentColor" /><circle cx="6" cy="11.5" r="0.75" fill="currentColor" /><line x1="8" y1="17" x2="12" y2="17" /><line x1="10" y1="14" x2="10" y2="17" />
          {/if}
        </svg>
        {#if !collapsed}
          <span>{link.label}</span>
        {/if}
      </a>
    {/each}
  </nav>
</aside>
