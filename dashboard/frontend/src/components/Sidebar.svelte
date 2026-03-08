<script lang="ts">
  import { route } from '../lib/router.svelte';

  const links = [
    { page: 'dashboard', label: 'Dashboard', icon: '◉' },
    { page: 'projects', label: 'Projects', icon: '▦' },
    { page: 'runs', label: 'Runs', icon: '▶' },
    { page: 'logs', label: 'Logs', icon: '☰' },
    { page: 'config', label: 'Config', icon: '⚙' },
    { page: 'system', label: 'System', icon: '⊞' },
  ] as const;

  let collapsed = $state(false);
</script>

<aside
  class="flex flex-col bg-surface border-r border-border transition-all duration-200 shrink-0"
  style:width={collapsed ? '56px' : '220px'}
>
  <div class="flex items-center gap-2 px-4 py-4 border-b border-border">
    {#if !collapsed}
      <span class="text-pr font-bold text-lg">Claude Station</span>
    {/if}
    <button
      onclick={() => collapsed = !collapsed}
      class="ml-auto text-text-dim hover:text-text text-sm cursor-pointer"
      title={collapsed ? 'Expand' : 'Collapse'}
    >
      {collapsed ? '▸' : '◂'}
    </button>
  </div>

  <nav class="flex flex-col gap-1 px-2 py-3 flex-1">
    {#each links as link}
      {@const active = route.page === link.page || (link.page === 'runs' && route.page === 'run-detail')}
      <a
        href="#{link.page === 'dashboard' ? '/' : `/${link.page}`}"
        class="flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors no-underline
          {active ? 'bg-surface-2 text-text font-medium' : 'text-text-dim hover:text-text hover:bg-surface-2/50'}"
      >
        <span class="text-base w-5 text-center">{link.icon}</span>
        {#if !collapsed}
          <span>{link.label}</span>
        {/if}
      </a>
    {/each}
  </nav>
</aside>
