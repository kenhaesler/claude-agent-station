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
      result.push({ label: route.param ?? 'Detail' });
    } else if (route.page === 'queue-detail') {
      result.push({ label: 'Queue', path: '/queue' });
      result.push({ label: `#${route.param}` });
    } else if (route.page === 'project-detail') {
      result.push({ label: 'Projects', path: '/projects' });
      result.push({ label: route.param ?? 'Detail' });
    } else if (route.page === 'brainstorm-session') {
      result.push({ label: 'Brainstorm', path: '/brainstorm' });
      result.push({ label: 'Session' });
    } else {
      result.push({ label: getPageTitle(route.page) });
    }

    return result;
  });
</script>

<nav class="flex items-center gap-1 text-xs" aria-label="Breadcrumb">
  {#each crumbs as crumb, i}
    {#if i > 0}
      <span class="text-text-muted">/</span>
    {/if}
    {#if crumb.path && i < crumbs.length - 1}
      <a
        href={crumb.path}
        class="text-text-muted hover:text-text-dim transition-colors"
      >
        {crumb.label}
      </a>
    {:else}
      <span class="text-text-dim font-medium">{crumb.label}</span>
    {/if}
  {/each}
</nav>
