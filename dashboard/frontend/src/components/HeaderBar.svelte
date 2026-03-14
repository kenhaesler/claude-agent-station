<script lang="ts">
  import StatusOrb from './StatusOrb.svelte';
  import ArcGauge from './ArcGauge.svelte';
  import AgentAvatar from './AgentAvatar.svelte';
  import IntelligenceChip from './IntelligenceChip.svelte';
  import { agentPresence } from '../lib/agent-presence.svelte';
  import { isBackpressureElevated } from '../lib/intelligence-cache.svelte';
  import { audioEngine } from '../lib/audio-engine';
  import { getStoredApiKey } from '../lib/api';

  let showVolumeSlider = $state(false);
  let audioMuted = $state(audioEngine.isMuted());
  let audioVolume = $state(audioEngine.getVolume());

  function toggleAudioMute() {
    audioEngine.toggleMute();
    audioMuted = audioEngine.isMuted();
  }

  function handleVolumeChange(e: Event) {
    const val = parseFloat((e.target as HTMLInputElement).value);
    audioVolume = val;
    audioEngine.setVolume(val);
    if (val > 0 && audioMuted) {
      audioEngine.setMuted(false);
      audioMuted = false;
    }
  }

  type BackgroundMode = '3d' | '2d' | 'off';

  interface Props {
    serviceActive: boolean;
    authOk: boolean;
    usagePercent: number;
    sessionsUsed: number;
    sessionLimit: number;
    onTrigger: () => void;
    triggering: boolean;
    onPanelToggle?: () => void;
    onAuthClick?: () => void;
    backgroundMode?: BackgroundMode;
    onBackgroundModeChange?: (mode: BackgroundMode) => void;
  }

  let {
    serviceActive,
    authOk,
    usagePercent,
    sessionsUsed,
    sessionLimit,
    onTrigger,
    triggering,
    onPanelToggle,
    onAuthClick,
    backgroundMode = '3d',
    onBackgroundModeChange,
  }: Props = $props();

  function cycleBackgroundMode() {
    const modes: BackgroundMode[] = ['3d', '2d', 'off'];
    const next = modes[(modes.indexOf(backgroundMode) + 1) % modes.length];
    onBackgroundModeChange?.(next);
  }

  let bgModeLabel = $derived(
    backgroundMode === '3d' ? '3D Space' :
    backgroundMode === '2d' ? '2D Particles' : 'Off'
  );

  let hasApiKey = $derived(getStoredApiKey() !== null);

  let usageColor = $derived(
    usagePercent > 80 ? '#ef4444' :
    usagePercent > 60 ? '#f59e0b' : '#6366f1'
  );

  let phaseLabel = $derived(
    agentPresence.phase === 'idle' ? null :
    agentPresence.phase === 'employee' ? 'Working' :
    agentPresence.phase === 'manager_review' ? 'Reviewing' :
    agentPresence.phase === 'executing_verdict' ? 'Verdict' :
    agentPresence.phase === 'coordinating' ? 'Coordinating' : null
  );

  let phaseColor = $derived(
    agentPresence.phase === 'employee' ? 'text-info' :
    agentPresence.phase === 'manager_review' ? 'text-warning' :
    agentPresence.phase === 'executing_verdict' ? 'text-approve' :
    agentPresence.phase === 'coordinating' ? 'text-accent-purple' : ''
  );
</script>

<header class="h-12 w-full flex items-center justify-between px-3 md:px-4 bg-surface border-b border-border shrink-0">
  <!-- Left: phase + agent presence -->
  <div class="flex items-center gap-3">
    <!-- Phase indicator -->
    {#if phaseLabel}
      <div class="flex items-center gap-1.5">
        <div class="w-2 h-2 rounded-full animate-pulse {agentPresence.phase === 'employee' ? 'bg-info' : agentPresence.phase === 'manager_review' ? 'bg-warning' : agentPresence.phase === 'executing_verdict' ? 'bg-approve' : 'bg-accent-purple'}"></div>
        <span class="text-xs font-medium {phaseColor}">{phaseLabel}</span>
      </div>
    {:else}
      <span class="text-xs text-text-muted">Idle</span>
    {/if}

    <!-- Agent presence dots (desktop) -->
    <div class="hidden md:flex items-center gap-1">
      {#each agentPresence.agents.slice(0, 4) as agent}
        <button
          onclick={() => onPanelToggle?.()}
          class="cursor-pointer opacity-80 hover:opacity-100 transition-opacity"
          title="{agent.name} — {agent.status}"
        >
          <AgentAvatar name={agent.name} role={agent.role} color={agent.color} status={agent.status} size="sm" />
        </button>
      {/each}
    </div>
  </div>

  <!-- Right: status + trigger + panel toggle -->
  <div class="flex items-center gap-2 md:gap-3">
    <!-- Service status -->
    <div class="flex items-center gap-1">
      <StatusOrb active={serviceActive} />
      <span class="text-[10px] text-text-dim hidden sm:inline">
        {serviceActive ? 'Active' : 'Down'}
      </span>
    </div>

    <!-- Auth status -->
    <div class="flex items-center gap-1 hidden sm:flex">
      <StatusOrb active={authOk} />
      <span class="text-[10px] text-text-dim hidden md:inline">
        {authOk ? 'Auth' : 'Auth Err'}
      </span>
    </div>

    <!-- Usage (desktop) -->
    <div class="hidden md:flex items-center gap-1">
      <span class="text-[10px] text-text-dim font-data hidden lg:inline">{sessionsUsed}/{sessionLimit}</span>
      <ArcGauge value={usagePercent} size={28} color={usageColor} />
    </div>

    <!-- Backpressure indicator -->
    <IntelligenceChip type="backpressure" class="hidden md:inline-flex" />

    <!-- Trigger Run -->
    <button
      onclick={onTrigger}
      disabled={triggering}
      class="px-2.5 py-1 text-xs font-medium rounded-md text-white transition-all cursor-pointer
        {isBackpressureElevated() ? 'bg-warning hover:bg-warning/80' : 'bg-accent-blue hover:opacity-90'}
        {triggering ? 'opacity-50 cursor-not-allowed' : 'active:scale-95'}"
    >
      {triggering ? '...' : 'Run'}
    </button>

    <!-- Background mode toggle -->
    <button
      onclick={cycleBackgroundMode}
      class="p-1.5 rounded-md text-text-dim hover:text-text hover:bg-white/5 cursor-pointer transition-colors"
      title="Background: {bgModeLabel}"
    >
      <svg class="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        {#if backgroundMode === '3d'}
          <!-- Globe/stars icon -->
          <circle cx="8" cy="8" r="6" />
          <ellipse cx="8" cy="8" rx="2.5" ry="6" />
          <line x1="2" y1="8" x2="14" y2="8" />
          <circle cx="12" cy="3" r="0.8" fill="currentColor" stroke="none" />
          <circle cx="4" cy="4" r="0.5" fill="currentColor" stroke="none" />
        {:else if backgroundMode === '2d'}
          <!-- Sparkle/dots icon -->
          <circle cx="8" cy="4" r="1" fill="currentColor" stroke="none" />
          <circle cx="4" cy="8" r="0.8" fill="currentColor" stroke="none" />
          <circle cx="12" cy="7" r="0.6" fill="currentColor" stroke="none" />
          <circle cx="6" cy="12" r="0.7" fill="currentColor" stroke="none" />
          <circle cx="11" cy="11" r="0.9" fill="currentColor" stroke="none" />
          <circle cx="8" cy="8" r="1.2" fill="currentColor" stroke="none" />
          <path d="M8 1v2M8 13v2M1 8h2M13 8h2" />
        {:else}
          <!-- Off icon (empty circle with line) -->
          <circle cx="8" cy="8" r="6" />
          <line x1="4" y1="12" x2="12" y2="4" />
        {/if}
      </svg>
    </button>

    <!-- Volume control -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="relative"
      role="group"
      aria-label="Volume control"
      onmouseenter={() => showVolumeSlider = true}
      onmouseleave={() => showVolumeSlider = false}
    >
      <button
        onclick={toggleAudioMute}
        class="p-1.5 rounded-md text-text-dim hover:text-text hover:bg-white/5 cursor-pointer transition-colors"
        title={audioMuted ? 'Unmute sounds' : 'Mute sounds'}
      >
        <svg class="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          {#if audioMuted}
            <path d="M2 5.5h2.5L8 2.5v11l-3.5-3H2z" />
            <line x1="11" y1="5" x2="15" y2="11" />
            <line x1="15" y1="5" x2="11" y2="11" />
          {:else}
            <path d="M2 5.5h2.5L8 2.5v11l-3.5-3H2z" />
            <path d="M11 4.5a4 4 0 0 1 0 7" />
            <path d="M12.5 2.5a7 7 0 0 1 0 11" />
          {/if}
        </svg>
      </button>
      {#if showVolumeSlider}
        <div class="absolute right-0 top-full mt-1 p-2 glass rounded-lg border border-border/50 shadow-lg z-50 w-32">
          <input
            type="range"
            min="0"
            max="1"
            step="0.05"
            value={audioVolume}
            oninput={handleVolumeChange}
            class="w-full h-1 accent-info cursor-pointer"
          />
          <div class="text-[10px] text-text-muted text-center mt-1">{Math.round(audioVolume * 100)}%</div>
        </div>
      {/if}
    </div>

    <!-- API Key lock icon -->
    <button
      onclick={() => onAuthClick?.()}
      class="p-1.5 rounded-md text-text-dim hover:text-text hover:bg-white/5 cursor-pointer transition-colors"
      title={hasApiKey ? 'API Key (set)' : 'Set API Key'}
    >
      {#if hasApiKey}
        <!-- Locked icon -->
        <svg class="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="7" width="10" height="7" rx="1.5" />
          <path d="M5 7V5a3 3 0 0 1 6 0v2" />
        </svg>
      {:else}
        <!-- Unlocked icon -->
        <svg class="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="7" width="10" height="7" rx="1.5" />
          <path d="M5 7V5a3 3 0 0 1 6 0" />
        </svg>
      {/if}
    </button>

    <!-- Panel toggle -->
    <button
      onclick={() => onPanelToggle?.()}
      class="p-1.5 rounded-md text-text-dim hover:text-text hover:bg-white/5 cursor-pointer transition-colors
        {agentPresence.panelOpen ? 'bg-white/5 text-text' : ''}"
      title="Agent Panel (Cmd+K)"
    >
      <svg class="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <rect x="1" y="2" width="14" height="12" rx="1.5" />
        <line x1="10" y1="2" x2="10" y2="14" />
      </svg>
    </button>
  </div>
</header>
