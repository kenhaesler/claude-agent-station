<script lang="ts">
  import { route, handleLinkClick, navigate } from './lib/router.svelte';
  import { triggerRun, getActiveEmployees } from './lib/api';
  import { addToast } from './lib/toast.svelte';
  import { connect as connectPresence, disconnect as disconnectPresence, agentPresence } from './lib/agent-presence.svelte';
  import type { ActiveEmployee } from './lib/types';

  // Initialize real-time connections (SSE + WebSocket + polling)
  $effect(() => {
    connectPresence();
    return () => disconnectPresence();
  });

  // Layout
  import Shell from './components/layout/Shell.svelte';

  // Overlays
  import Toast from './components/overlays/Toast.svelte';

  // Pages
  import CommandCenter from './pages/CommandCenter.svelte';
  import AgentTheater from './pages/AgentTheater.svelte';
  import RunsPage from './pages/RunsPage.svelte';
  import RunDetail from './pages/RunDetail.svelte';
  import QueueBoard from './pages/QueueBoard.svelte';
  import ProjectsPage from './pages/ProjectsPage.svelte';
  import ProjectDetail from './pages/ProjectDetail.svelte';
  import SettingsPage from './pages/SettingsPage.svelte';

  // --- App State ---
  let triggering = $state(false);
  let activeEmployees = $state<ActiveEmployee[]>([]);

  // SSE connected state from agentPresence
  let sseConnected = $derived(agentPresence.sseConnected);

  // --- Actions ---
  async function handleTrigger() {
    triggering = true;
    try {
      await triggerRun();
      addToast('success', 'Run triggered successfully');
    } catch (e: any) {
      addToast('error', `Failed to trigger: ${e.message}`);
    } finally {
      triggering = false;
    }
  }

  // --- Polling ---
  $effect(() => {
    async function poll() {
      try {
        const emps = await getActiveEmployees();
        activeEmployees = emps;
      } catch { /* ignore */ }
    }
    poll();
    const interval = setInterval(poll, 15_000);
    return () => clearInterval(interval);
  });

  // --- Dynamic tab title ---
  $effect(() => {
    const count = activeEmployees.length;
    if (count > 0) {
      document.title = `Working (${count}) — Claude Station`;
    } else {
      document.title = 'Claude Station';
    }
  });

  // --- Keyboard shortcuts ---
  function handleKeydown(e: KeyboardEvent) {
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;

    // Number keys for navigation (matches NavRail order)
    if (e.key === '1') { navigate('/'); return; }
    if (e.key === '2') { navigate('/agents'); return; }
    if (e.key === '3') { navigate('/runs'); return; }
    if (e.key === '4') { navigate('/queue'); return; }
    if (e.key === '5') { navigate('/projects'); return; }
    if (e.key === '6') { navigate('/settings'); return; }
  }
</script>

<svelte:window onkeydown={handleKeydown} />

<!-- Skip to content -->
<a href="#main-content" class="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50
   px-4 py-2 bg-cyan text-void rounded-md text-sm font-medium">
  Skip to content
</a>

<!-- Main app -->
<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="relative min-h-screen" onclick={handleLinkClick}>
  <Shell
    onTrigger={handleTrigger}
    {triggering}
    {sseConnected}
    activeCount={activeEmployees.length}
  >
    {#if route.page === 'command-center'}
      <CommandCenter {triggering} onTrigger={handleTrigger} />
    {:else if route.page === 'theater' || route.page === 'agents'}
      <AgentTheater />
    {:else if route.page === 'runs'}
      <RunsPage />
    {:else if route.page === 'run-detail'}
      <RunDetail runId={route.param ?? ''} />
    {:else if route.page === 'queue'}
      <QueueBoard />
    {:else if route.page === 'projects'}
      <ProjectsPage />
    {:else if route.page === 'project-detail'}
      <ProjectDetail projectId={route.param ?? ''} />
    {:else if route.page === 'settings'}
      <SettingsPage tab={route.param} />
    {:else}
      <div class="text-secondary text-center py-20">Page not found</div>
    {/if}
  </Shell>

  <Toast />
</div>
