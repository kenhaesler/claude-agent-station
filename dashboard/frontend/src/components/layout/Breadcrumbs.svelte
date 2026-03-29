<script lang="ts">
  import { route, getPageTitle, navigate } from '../../lib/router.svelte';

  interface Crumb {
    label: string;
    path?: string;
  }

  let crumbs = $derived.by((): Crumb[] => {
    const result: Crumb[] = [{ label: 'Station', path: '/' }];

    if (route.page === 'command-center') {
      result.push({ label: 'Command Center' });
    } else if (route.page === 'run-detail') {
      result.push({ label: 'Runs', path: '/runs' });
      result.push({ label: route.param ? route.param.slice(0, 12) : 'Detail' });
    } else if (route.page === 'project-detail') {
      result.push({ label: 'Projects', path: '/projects' });
      result.push({ label: route.param ?? 'Detail' });
    } else {
      result.push({ label: getPageTitle(route.page) });
    }

    return result;
  });
</script>

<nav class="flex items-center gap-2 text-sm font-mono" aria-label="Breadcrumb">
  {#each crumbs as crumb, i}
    {#if i > 0}
      <span class="text-ghost text-xs">/</span>
    {/if}
    {#if crumb.path && i < crumbs.length - 1}
      <button
        onclick={() => crumb.path && navigate(crumb.path)}
        class="text-tertiary hover:text-secondary transition-colors duration-150 cursor-pointer"
      >
        {crumb.label}
      </button>
    {:else}
      <span class="text-primary font-semibold">{crumb.label}</span>
    {/if}
  {/each}
</nav>
