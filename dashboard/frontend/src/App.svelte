<script lang="ts">
  import { untrack } from 'svelte';
  import { route, handleLinkClick, navigate } from './lib/router.svelte';
  import { triggerRun, getSystemStatus, getAuthStatus, getUsage, getGitHubOAuthStatus } from './lib/api';
  import { toastSuccess, toastError } from './lib/toast.svelte';
  import { agentPresence, connect as connectPresence, disconnect as disconnectPresence } from './lib/agent-presence.svelte';
  import './lib/theme.svelte';
  import { startIntelligenceRefresh, stopIntelligenceRefresh } from './lib/intelligence-cache.svelte';
  import { audioEngine } from './lib/audio-engine';
  import { handleShortcutKeydown } from './lib/shortcuts';

  // Layout
  import Shell from './components/layout/Shell.svelte';

  // Overlays
  import ApiKeyModal from './components/overlays/ApiKeyModal.svelte';
  import CommandPalette from './components/overlays/CommandPalette.svelte';
  import ShortcutReference from './components/overlays/ShortcutReference.svelte';
  import Toast from './components/overlays/Toast.svelte';

  // Ambient
  import NeuralAurora from './components/ambient/NeuralAurora.svelte';
  import AmbientGlow from './components/ambient/AmbientGlow.svelte';

  // Pages (lazy-ish — all imported, route switches which renders)
  import CommandCenter from './pages/CommandCenter.svelte';
  import AgentTheater from './pages/AgentTheater.svelte';
  import RunsPage from './pages/RunsPage.svelte';
  import RunDetail from './pages/RunDetail.svelte';
  import QueueBoard from './pages/QueueBoard.svelte';
  import IntelligenceHub from './pages/IntelligenceHub.svelte';
  import ProjectsPage from './pages/ProjectsPage.svelte';
  import ProjectDetail from './pages/ProjectDetail.svelte';
  import IntegrationPage from './pages/IntegrationPage.svelte';
  import BrainstormPage from './pages/BrainstormPage.svelte';
  import BrainstormSession from './pages/BrainstormSession.svelte';
  import SettingsPage from './pages/SettingsPage.svelte';

  // --- App State ---
  let triggering = $state(false);
  let showApiKeyModal = $state(false);
  let paletteOpen = $state(false);
  let shortcutRefOpen = $state(false);

  // Background mode
  type BackgroundMode = 'rich' | 'lite' | 'off';
  const rawBg = localStorage.getItem('station-bg-mode');
  const migratedBg: BackgroundMode =
    rawBg === '3d' ? 'rich' : rawBg === '2d' ? 'lite' :
    (rawBg as BackgroundMode) ?? 'rich';
  if (rawBg === '3d' || rawBg === '2d') localStorage.setItem('station-bg-mode', migratedBg);
  let backgroundMode = $state<BackgroundMode>(migratedBg);

  // --- Actions ---
  async function handleTrigger() {
    triggering = true;
    try {
      await triggerRun();
      toastSuccess('Run triggered');
    } catch (e: any) {
      toastError(`Failed to trigger: ${e.message}`);
    } finally {
      triggering = false;
    }
  }

  // --- Auth listener ---
  $effect(() => {
    const handler = () => { showApiKeyModal = true; };
    window.addEventListener('station-auth-required', handler);
    return () => window.removeEventListener('station-auth-required', handler);
  });

  // --- Agent presence lifecycle ---
  $effect(() => {
    connectPresence();
    return () => disconnectPresence();
  });

  // --- Intelligence cache ---
  $effect(() => {
    untrack(() => startIntelligenceRefresh());
    return () => stopIntelligenceRefresh();
  });

  // --- Audio engine ---
  $effect(() => {
    const handler = (e: Event) => audioEngine.play((e as CustomEvent).detail);
    document.body.addEventListener('workspace-sound', handler);
    return () => document.body.removeEventListener('workspace-sound', handler);
  });

  // --- Dynamic tab title ---
  $effect(() => {
    const phase = agentPresence.phase;
    const activeCount = agentPresence.activeRuns.length;
    const pending = agentPresence.pendingDecisionCount;

    if (pending > 0) {
      document.title = `(${pending}) Review Needed — Claude Station`;
    } else if (phase !== 'idle' && activeCount > 0) {
      document.title = `Working (${activeCount}) — Claude Station`;
    } else {
      document.title = 'Claude Station';
    }
  });

  // --- Keyboard shortcuts ---
  function handleKeydown(e: KeyboardEvent) {
    // Let shortcut registry handle first
    if (handleShortcutKeydown(e)) return;

    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;

    if (e.key === 'Escape') {
      if (paletteOpen) { paletteOpen = false; return; }
      return;
    }
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      paletteOpen = !paletteOpen;
      return;
    }
    // Number keys for navigation
    if (e.key === '1') { navigate('/'); return; }
    if (e.key === '2') { navigate('/theater'); return; }
    if (e.key === '3') { navigate('/runs'); return; }
    if (e.key === '4') { navigate('/queue'); return; }
    if (e.key === '5') { navigate('/intelligence'); return; }
    if (e.key === '6') { navigate('/projects'); return; }
    if (e.key === '7') { navigate('/integration'); return; }
    if (e.key === '8') { navigate('/settings'); return; }
    if (e.key === '?') {
      shortcutRefOpen = !shortcutRefOpen;
      return;
    }
  }
</script>

<svelte:window onkeydown={handleKeydown} />

<!-- Skip to content -->
<a href="#main-content" class="skip-to-content">Skip to content</a>

<!-- Ambient background -->
<div class="fixed inset-0 z-cortex" aria-hidden="true">
  {#if backgroundMode === 'rich'}
    <NeuralAurora phase={agentPresence.phase} />
  {:else if backgroundMode === 'lite'}
    <AmbientGlow phase={agentPresence.phase} />
  {/if}
</div>

<!-- Main app -->
<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="relative z-content cortex-overlay min-h-screen" onclick={handleLinkClick}>
  <Shell>
    {#if route.page === 'command-center'}
      <CommandCenter {triggering} onTrigger={handleTrigger} />
    {:else if route.page === 'theater'}
      <AgentTheater />
    {:else if route.page === 'runs'}
      <RunsPage />
    {:else if route.page === 'run-detail'}
      <RunDetail runId={route.param ?? ''} />
    {:else if route.page === 'queue'}
      <QueueBoard />
    {:else if route.page === 'queue-detail'}
      <RunDetail runId={route.param ?? ''} />
    {:else if route.page === 'intelligence'}
      <IntelligenceHub />
    {:else if route.page === 'projects'}
      <ProjectsPage />
    {:else if route.page === 'project-detail'}
      <ProjectDetail projectId={route.param ?? ''} />
    {:else if route.page === 'integration'}
      <IntegrationPage />
    {:else if route.page === 'brainstorm'}
      <BrainstormPage />
    {:else if route.page === 'brainstorm-session'}
      <BrainstormSession sessionId={route.param ?? ''} />
    {:else if route.page === 'settings'}
      <SettingsPage tab={route.param} />
    {/if}
  </Shell>

  <ApiKeyModal show={showApiKeyModal} onClose={() => showApiKeyModal = false} />
  <CommandPalette open={paletteOpen} onclose={() => paletteOpen = false} />
  <ShortcutReference show={shortcutRefOpen} onClose={() => shortcutRefOpen = false} />
  <Toast />
</div>
