<script lang="ts">
  import { route } from './lib/router.svelte';
  import Sidebar from './components/Sidebar.svelte';
  import Toast from './components/Toast.svelte';
  import DashboardPage from './pages/DashboardPage.svelte';
  import ProjectsPage from './pages/ProjectsPage.svelte';
  import RunsPage from './pages/RunsPage.svelte';
  import RunDetailPage from './pages/RunDetailPage.svelte';
  import LogsPage from './pages/LogsPage.svelte';
  import ConfigPage from './pages/ConfigPage.svelte';
  import SystemPage from './pages/SystemPage.svelte';
</script>

<div class="flex min-h-screen bg-bg">
  <Sidebar />
  <main class="flex-1 p-6 overflow-auto">
    {#if route.page === 'dashboard'}
      <DashboardPage />
    {:else if route.page === 'projects'}
      <ProjectsPage />
    {:else if route.page === 'runs'}
      <RunsPage />
    {:else if route.page === 'run-detail'}
      <RunDetailPage runId={route.param ?? ''} />
    {:else if route.page === 'logs'}
      <LogsPage />
    {:else if route.page === 'config'}
      <ConfigPage />
    {:else if route.page === 'system'}
      <SystemPage />
    {/if}
  </main>
  <Toast />
</div>
