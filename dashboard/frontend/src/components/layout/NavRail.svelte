<script lang="ts">
  import { route, navigate } from '../../lib/router.svelte';
  import { agentPresence } from '../../lib/agent-presence.svelte';

  interface NavItem {
    id: string;
    icon: string;
    label: string;
    path: string;
    pages: string[];
    shortcut: string;
    badge?: () => number;
  }

  const items: NavItem[] = [
    { id: 'home', icon: '⌂', label: 'Dashboard', path: '/', pages: ['command-center'], shortcut: '1' },
    { id: 'agents', icon: '◉', label: 'Agents', path: '/agents', pages: ['theater', 'agents'], shortcut: '2', badge: () => agentPresence.activeRuns.length },
    { id: 'runs', icon: '▷', label: 'Runs', path: '/runs', pages: ['runs', 'run-detail'], shortcut: '3' },
    { id: 'queue', icon: '☰', label: 'Queue', path: '/queue', pages: ['queue'], shortcut: '4' },
    { id: 'projects', icon: '▤', label: 'Projects', path: '/projects', pages: ['projects', 'project-detail'], shortcut: '5' },
  ];

  const bottomItems: NavItem[] = [
    { id: 'settings', icon: '⚙', label: 'Settings', path: '/settings', pages: ['settings'], shortcut: '6' },
  ];

  let expanded = $state(false);

  function isActive(item: NavItem): boolean {
    return item.pages.includes(route.page);
  }
</script>

<nav
  class="group/nav flex flex-col {expanded ? 'w-56' : 'w-16'} shrink-0
         transition-all duration-300 ease-out relative z-50"
  style="background: rgba(10, 10, 18, 0.65); backdrop-filter: blur(24px) saturate(1.4); -webkit-backdrop-filter: blur(24px) saturate(1.4); border-right: 1px solid rgba(255,255,255,0.04);"
  aria-label="Main navigation"
  onmouseenter={() => expanded = true}
  onmouseleave={() => expanded = false}
>
  <!-- Logo -->
  <div class="flex items-center {expanded ? 'px-4' : 'justify-center'} h-14" style="border-bottom: 1px solid rgba(255,255,255,0.04);">
    <a
      href="/"
      class="flex items-center gap-3 no-underline"
      onclick={(e) => { e.preventDefault(); navigate('/'); }}
      aria-label="Claude Agent Station"
    >
      <div class="flex items-center justify-center w-8 h-8 rounded-xl font-heading font-bold text-sm transition-all duration-300"
           style="background: rgba(139,92,246,0.15); color: var(--color-violet); {isActive(items[0]) ? 'box-shadow: 0 0 20px rgba(139,92,246,0.25);' : ''}">
        C
      </div>
      {#if expanded}
        <span class="font-heading text-sm font-semibold text-primary tracking-tight whitespace-nowrap animate-fade-in">
          Agent Station
        </span>
      {/if}
    </a>
  </div>

  <!-- Main nav items -->
  <div class="flex flex-col gap-1 flex-1 py-3 {expanded ? 'px-2' : 'px-1.5'}">
    {#each items as item}
      {@const active = isActive(item)}
      <button
        onclick={() => navigate(item.path)}
        class="relative flex items-center gap-3 {expanded ? 'px-3' : 'justify-center'} h-10 rounded-xl
               text-sm transition-all duration-200 cursor-pointer"
        style="{active
          ? 'background: linear-gradient(135deg, rgba(139,92,246,0.12), rgba(99,102,241,0.08)); color: var(--color-primary); box-shadow: inset 0 1px 0 rgba(255,255,255,0.04);'
          : 'background: transparent; color: var(--color-secondary);'}"
        title={expanded ? undefined : item.label}
        aria-label={item.label}
        aria-current={active ? 'page' : undefined}
      >
        <!-- Active indicator glow bar -->
        {#if active}
          <span class="absolute left-0 top-2.5 bottom-2.5 w-0.5 rounded-r-full" style="background: var(--color-violet); box-shadow: 0 0 12px rgba(139,92,246,0.5);"></span>
        {/if}

        <!-- Icon -->
        <span class="relative flex items-center justify-center w-5 h-5 text-base shrink-0"
              style="{active ? 'color: var(--color-violet);' : ''}">
          {item.icon}
          {#if item.badge && item.badge() > 0 && !expanded}
            <span class="absolute -top-1 -right-1.5 w-3.5 h-3.5 rounded-full text-[8px] font-mono font-bold text-white flex items-center justify-center"
                  style="background: var(--color-violet); box-shadow: 0 0 8px rgba(139,92,246,0.4);">
              {item.badge()}
            </span>
          {/if}
        </span>

        <!-- Label (expanded only) -->
        {#if expanded}
          <span class="flex-1 text-left whitespace-nowrap font-medium animate-fade-in">
            {item.label}
          </span>
          <span class="flex items-center gap-1.5">
            {#if item.badge && item.badge() > 0}
              <span class="w-4 h-4 rounded-full text-[9px] font-mono font-bold flex items-center justify-center"
                    style="background: rgba(139,92,246,0.15); color: var(--color-violet);">
                {item.badge()}
              </span>
            {/if}
            <span class="text-[10px] font-mono text-ghost">{item.shortcut}</span>
          </span>
        {/if}
      </button>
    {/each}
  </div>

  <!-- Bottom items -->
  <div class="flex flex-col gap-1 py-3 {expanded ? 'px-2' : 'px-1.5'}" style="border-top: 1px solid rgba(255,255,255,0.04);">
    {#each bottomItems as item}
      {@const active = isActive(item)}
      <button
        onclick={() => navigate(item.path)}
        class="relative flex items-center gap-3 {expanded ? 'px-3' : 'justify-center'} h-10 rounded-xl
               text-sm transition-all duration-200 cursor-pointer"
        style="{active
          ? 'background: linear-gradient(135deg, rgba(139,92,246,0.12), rgba(99,102,241,0.08)); color: var(--color-primary);'
          : 'background: transparent; color: var(--color-secondary);'}"
        title={expanded ? undefined : item.label}
        aria-label={item.label}
      >
        {#if active}
          <span class="absolute left-0 top-2.5 bottom-2.5 w-0.5 rounded-r-full" style="background: var(--color-violet); box-shadow: 0 0 12px rgba(139,92,246,0.5);"></span>
        {/if}
        <span class="flex items-center justify-center w-5 h-5 text-base shrink-0" style="{active ? 'color: var(--color-violet);' : ''}">
          {item.icon}
        </span>
        {#if expanded}
          <span class="flex-1 text-left whitespace-nowrap font-medium animate-fade-in">{item.label}</span>
          <span class="text-[10px] font-mono text-ghost">{item.shortcut}</span>
        {/if}
      </button>
    {/each}
  </div>
</nav>
