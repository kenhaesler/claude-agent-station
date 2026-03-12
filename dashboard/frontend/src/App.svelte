<script lang="ts">
  import { route } from './lib/router.svelte';
  import { getSystemStatus, getAuthStatus, getUsage, triggerRun } from './lib/api';
  import { toastSuccess, toastError } from './lib/toast.svelte';
  import { agentPresence, connect as connectPresence, disconnect as disconnectPresence, togglePanel } from './lib/agent-presence.svelte';
  import NavRail from './components/NavRail.svelte';
  import HeaderBar from './components/HeaderBar.svelte';
  import AgentPanel from './components/AgentPanel.svelte';
  import Toast from './components/Toast.svelte';
  import CommandCenterPage from './pages/CommandCenterPage.svelte';
  import WorkStreamPage from './pages/WorkStreamPage.svelte';
  import DecisionsPage from './pages/DecisionsPage.svelte';
  import ConfigPage from './pages/ConfigPage.svelte';

  let serviceActive = $state(false);
  let authOk = $state(false);
  let usagePercent = $state(0);
  let sessionsUsed = $state(0);
  let sessionLimit = $state(50);
  let triggering = $state(false);

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
        sessionLimit = usageRes.value.plan_limit || usageRes.value.max_usage_percent;
      }
    } catch {
      // silently fail
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

  // Status polling
  $effect(() => {
    loadStatus();
    const interval = setInterval(loadStatus, 15000);
    return () => clearInterval(interval);
  });

  // Agent presence lifecycle
  $effect(() => {
    connectPresence();
    return () => disconnectPresence();
  });

  // Keyboard shortcuts
  function handleKeydown(e: KeyboardEvent) {
    // Ignore when typing in inputs
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;

    if (e.key === 'Escape' && agentPresence.panelOpen) {
      agentPresence.panelOpen = false;
      return;
    }
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      togglePanel();
      return;
    }
    if (e.key === '1') { window.location.hash = '/command'; return; }
    if (e.key === '2') { window.location.hash = '/stream'; return; }
    if (e.key === '3') { window.location.hash = '/decide'; return; }
    if (e.key === '4') { window.location.hash = '/config'; return; }
  }
</script>

<svelte:window onkeydown={handleKeydown} />

<div class="flex flex-col md:flex-row min-h-screen bg-bg">
  <!-- NavRail (bottom on mobile, left on desktop) -->
  <div class="hidden md:block">
    <NavRail />
  </div>

  <!-- Main area -->
  <div class="flex-1 flex flex-col overflow-hidden min-w-0">
    <HeaderBar
      {serviceActive}
      {authOk}
      {usagePercent}
      {sessionsUsed}
      {sessionLimit}
      onTrigger={handleTrigger}
      {triggering}
      onPanelToggle={() => togglePanel()}
    />
    <div class="flex flex-1 overflow-hidden">
      <!-- Page content -->
      <main class="flex-1 p-3 md:p-6 overflow-auto pb-20 md:pb-6">
        {#if route.page === 'command'}
          <CommandCenterPage />
        {:else if route.page === 'stream' || route.page === 'stream-detail'}
          <WorkStreamPage runId={route.page === 'stream-detail' ? route.param : null} />
        {:else if route.page === 'decide' || route.page === 'decide-detail'}
          <DecisionsPage planId={route.page === 'decide-detail' ? route.param : null} />
        {:else if route.page === 'config'}
          <ConfigPage tab={route.param} />
        {/if}
      </main>

      <!-- Agent Panel (slide-out right) -->
      {#if agentPresence.panelOpen}
        <AgentPanel onClose={() => agentPresence.panelOpen = false} />
      {/if}
    </div>
  </div>

  <!-- Mobile NavRail (bottom) -->
  <div class="md:hidden">
    <NavRail />
  </div>

  <Toast />
</div>
