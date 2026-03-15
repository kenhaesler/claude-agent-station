<script lang="ts">
  import { untrack } from 'svelte';
  import { route, navigate, handleLinkClick } from './lib/router.svelte';
  import { getSystemStatus, getAuthStatus, getUsage, triggerRun, getGitHubOAuthStatus } from './lib/api';
  import { toastSuccess, toastError } from './lib/toast.svelte';
  import { agentPresence, connect as connectPresence, disconnect as disconnectPresence, togglePanel } from './lib/agent-presence.svelte';
  // Import theme store to trigger initialization (applies saved theme on load)
  import './lib/theme.svelte';
  import { startIntelligenceRefresh, stopIntelligenceRefresh } from './lib/intelligence-cache.svelte';
  import { audioEngine } from './lib/audio-engine';
  import type { SystemStatus, UsageData } from './lib/types';
  import NavRail from './components/NavRail.svelte';
  import HeaderBar from './components/HeaderBar.svelte';
  import AgentPanel from './components/AgentPanel.svelte';
  import SpaceBackground from './components/SpaceBackground.svelte';
  import AmbientParticles from './components/AmbientParticles.svelte';
  import ApiKeyModal from './components/ApiKeyModal.svelte';
  import CommandPalette from './components/CommandPalette.svelte';
  import Toast from './components/Toast.svelte';
  import PulsePage from './pages/PulsePage.svelte';
  import WorkStreamPage from './pages/WorkStreamPage.svelte';
  import DecisionsPage from './pages/DecisionsPage.svelte';
  import ConfigPage from './pages/ConfigPage.svelte';
  import BrainstormPage from './pages/BrainstormPage.svelte';
  import AgentObservatoryPage from './pages/AgentObservatoryPage.svelte';
  import RunDetailPage from './pages/RunDetailPage.svelte';
  import AnalyticsPage from './pages/AnalyticsPage.svelte';

  let serviceActive = $state(false);
  let authOk = $state(false);
  let githubConnected = $state(false);
  let githubUsername = $state<string | null>(null);
  let usagePercent = $state(0);
  let sessionsUsed = $state(0);
  let sessionLimit = $state(50);
  let triggering = $state(false);
  let showApiKeyModal = $state(false);
  let paletteOpen = $state(false);

  // Background mode (persisted to localStorage)
  type BackgroundMode = '3d' | '2d' | 'off';
  let backgroundMode = $state<BackgroundMode>(
    (localStorage.getItem('station-bg-mode') as BackgroundMode) ?? '3d'
  );

  // Reduced motion detection
  let reduceMotion = $state(false);
  $effect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    reduceMotion = mq.matches;
    const handler = (e: MediaQueryListEvent) => { reduceMotion = e.matches; };
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  });

  // System status and usage for Cortex
  let systemStatus = $state<SystemStatus | null>(null);
  let usageData = $state<UsageData | null>(null);

  async function loadStatus() {
    try {
      const [sysRes, authRes, usageRes, ghRes] = await Promise.allSettled([
        getSystemStatus(),
        getAuthStatus(),
        getUsage(),
        getGitHubOAuthStatus(),
      ]);
      if (sysRes.status === 'fulfilled') {
        serviceActive = sysRes.value.service.active;
        systemStatus = sysRes.value;
      }
      if (authRes.status === 'fulfilled') {
        authOk = authRes.value.logged_in && !authRes.value.expired;
      }
      if (usageRes.status === 'fulfilled') {
        usagePercent = usageRes.value.usage_percent;
        sessionsUsed = usageRes.value.sessions_used;
        sessionLimit = usageRes.value.plan_limit || usageRes.value.max_usage_percent;
        usageData = usageRes.value;
      }
      if (ghRes.status === 'fulfilled') {
        githubConnected = ghRes.value.connected;
        githubUsername = ghRes.value.username ?? null;
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

  // Listen for auth-required events from API layer
  $effect(() => {
    const handler = () => { showApiKeyModal = true; };
    window.addEventListener('station-auth-required', handler);
    return () => window.removeEventListener('station-auth-required', handler);
  });

  // Agent presence lifecycle
  $effect(() => {
    connectPresence();
    return () => disconnectPresence();
  });

  // Intelligence cache lifecycle
  $effect(() => {
    untrack(() => startIntelligenceRefresh());
    return () => stopIntelligenceRefresh();
  });

  // Audio engine — listen for workspace-sound events and play through AudioEngine
  $effect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      audioEngine.play(detail);
    };
    document.body.addEventListener('workspace-sound', handler);
    return () => document.body.removeEventListener('workspace-sound', handler);
  });

  // Keyboard shortcuts
  function handleKeydown(e: KeyboardEvent) {
    // Ignore when typing in inputs
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;

    if (e.key === 'Escape') {
      if (paletteOpen) { paletteOpen = false; return; }
      if (agentPresence.panelOpen) { agentPresence.panelOpen = false; return; }
      return;
    }
    // Cmd+K: Command Palette
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      paletteOpen = !paletteOpen;
      return;
    }
    // Cmd+Shift+A: Agent Panel
    if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key === 'a') {
      e.preventDefault();
      togglePanel();
      return;
    }
    if (e.key === '1') { navigate('/command'); return; }
    if (e.key === '2') { navigate('/stream'); return; }
    if (e.key === '3') { navigate('/decide'); return; }
    if (e.key === '4') { navigate('/brainstorm'); return; }
    if (e.key === '5') { navigate('/config'); return; }
    if (e.key === '6') { navigate('/agents'); return; }
    if (e.key === '7') { navigate('/analytics'); return; }
  }
</script>

<svelte:window onkeydown={handleKeydown} />

<!-- Skip to content (accessibility) -->
<a href="#main-content" class="skip-to-content">Skip to content</a>

<!-- Ambient background — configurable 3D space / 2D particles / off -->
<div class="fixed inset-0 z-cortex" aria-hidden="true">
  {#if backgroundMode === '3d' && !reduceMotion}
    <SpaceBackground phase={agentPresence.phase} />
  {:else if backgroundMode === '2d' && !reduceMotion}
    <AmbientParticles phase={agentPresence.phase} />
  {/if}
</div>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="relative z-content flex flex-col md:flex-row h-screen" onclick={handleLinkClick}>
  <!-- NavRail (bottom on mobile, left on desktop) -->
  <nav class="hidden md:block" aria-label="Main navigation">
    <NavRail />
  </nav>

  <!-- Main area -->
  <div class="flex-1 flex flex-col overflow-hidden min-w-0">
    <HeaderBar
      {serviceActive}
      {authOk}
      {githubConnected}
      {githubUsername}
      {usagePercent}
      {sessionsUsed}
      {sessionLimit}
      onTrigger={handleTrigger}
      {triggering}
      onPanelToggle={() => togglePanel()}
      onAuthClick={() => showApiKeyModal = true}
      {backgroundMode}
      onBackgroundModeChange={(mode) => {
        backgroundMode = mode;
        localStorage.setItem('station-bg-mode', mode);
      }}
    />
    <div class="flex flex-1 overflow-hidden">
      <!-- Page content with semi-transparent overlay -->
      <main
        id="main-content"
        class="flex-1 p-3 md:p-6 overflow-auto pb-20 md:pb-6 transition-all duration-400 cortex-overlay"
        aria-label="Page content"
      >
        {#if route.page === 'command'}
          <PulsePage {systemStatus} usage={usageData} />
        {:else if route.page === 'stream-detail' && route.param}
          <RunDetailPage runId={route.param} />
        {:else if route.page === 'stream'}
          <WorkStreamPage />
        {:else if route.page === 'decide' || route.page === 'decide-detail'}
          <DecisionsPage planId={route.page === 'decide-detail' ? route.param : null} />
        {:else if route.page === 'brainstorm' || route.page === 'brainstorm-session'}
          <BrainstormPage sessionId={route.page === 'brainstorm-session' ? route.param : null} />
        {:else if route.page === 'config'}
          <ConfigPage tab={route.param} />
        {:else if route.page === 'agents'}
          <AgentObservatoryPage />
        {:else if route.page === 'analytics'}
          <AnalyticsPage />
        {/if}
      </main>

      <!-- Agent Panel (slide-out right) -->
      {#if agentPresence.panelOpen}
        <aside aria-label="Agent panel">
          <AgentPanel onClose={() => agentPresence.panelOpen = false} />
        </aside>
      {/if}
    </div>
  </div>

  <!-- Mobile NavRail (bottom) -->
  <nav class="md:hidden" aria-label="Main navigation">
    <NavRail />
  </nav>

  <ApiKeyModal show={showApiKeyModal} onClose={() => showApiKeyModal = false} />
  <CommandPalette open={paletteOpen} onclose={() => paletteOpen = false} />
  <Toast />
</div>
