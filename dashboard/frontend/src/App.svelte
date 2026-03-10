<script lang="ts">
  import { route } from './lib/router.svelte';
  import { getSystemStatus, getAuthStatus, getUsage, triggerRun } from './lib/api';
  import { toastSuccess, toastError } from './lib/toast.svelte';
  import Sidebar from './components/Sidebar.svelte';
  import HeaderBar from './components/HeaderBar.svelte';
  import Toast from './components/Toast.svelte';
  import DashboardPage from './pages/DashboardPage.svelte';
  import ProjectsPage from './pages/ProjectsPage.svelte';
  import RunsPage from './pages/RunsPage.svelte';
  import RunDetailPage from './pages/RunDetailPage.svelte';
  import LogsPage from './pages/LogsPage.svelte';
  import ConfigPage from './pages/ConfigPage.svelte';
  import PlansPage from './pages/PlansPage.svelte';
  import PlanDetailPage from './pages/PlanDetailPage.svelte';
  import SystemPage from './pages/SystemPage.svelte';

  let serviceActive = $state(false);
  let authOk = $state(false);
  let usagePercent = $state(0);
  let sessionsUsed = $state(0);
  let sessionLimit = $state(50);
  let triggering = $state(false);
  let sidebarOpen = $state(false);

  async function loadStatus() {
    try {
      const [sysRes, authRes, usageRes] = await Promise.allSettled([
        getSystemStatus(),
        getAuthStatus(),
        getUsage(),
      ]);
      if (sysRes.status === 'fulfilled') {
        serviceActive = sysRes.value.service.active;
      }
      if (authRes.status === 'fulfilled') {
        authOk = authRes.value.logged_in && !authRes.value.expired;
      }
      if (usageRes.status === 'fulfilled') {
        usagePercent = usageRes.value.usage_percent;
        sessionsUsed = usageRes.value.sessions_used;
        sessionLimit = usageRes.value.session_limit_24h;
      }
    } catch {
      // silently fail - header just shows defaults
    }
  }

  async function handleTrigger() {
    triggering = true;
    try {
      await triggerRun();
      toastSuccess('Run triggered');
      await loadStatus();
    } catch (e: any) {
      toastError(`Failed to trigger: ${e.message}`);
    } finally {
      triggering = false;
    }
  }

  $effect(() => {
    loadStatus();
    const interval = setInterval(loadStatus, 15000);
    return () => clearInterval(interval);
  });
</script>

<div class="flex min-h-screen bg-bg">
  <Sidebar bind:mobileOpen={sidebarOpen} />
  <div class="flex-1 flex flex-col overflow-hidden min-w-0">
    <HeaderBar
      {serviceActive}
      {authOk}
      {usagePercent}
      {sessionsUsed}
      {sessionLimit}
      onTrigger={handleTrigger}
      {triggering}
      onMenuToggle={() => sidebarOpen = !sidebarOpen}
    />
    <main class="flex-1 p-3 md:p-6 overflow-auto">
      {#if route.page === 'dashboard'}
        <DashboardPage />
      {:else if route.page === 'projects'}
        <ProjectsPage />
      {:else if route.page === 'plans'}
        <PlansPage />
      {:else if route.page === 'plan-detail'}
        <PlanDetailPage planId={route.param ?? ''} />
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
  </div>
  <Toast />
</div>
