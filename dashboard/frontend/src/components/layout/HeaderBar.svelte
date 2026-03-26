<script lang="ts">
  import { route, getPageTitle } from '../../lib/router.svelte';
  import { agentPresence } from '../../lib/agent-presence.svelte';
  import Breadcrumbs from './Breadcrumbs.svelte';

  let {
    onTrigger,
    triggering = false,
    onAuthClick,
    onPaletteOpen,
  }: {
    onTrigger?: () => void;
    triggering?: boolean;
    onAuthClick?: () => void;
    onPaletteOpen?: () => void;
  } = $props();

  let sseStatus = $derived(agentPresence.sseConnected ? 'connected' : 'disconnected');
  let activeCount = $derived(agentPresence.activeRuns.length);
</script>

<header class="flex items-center gap-3 px-4 h-12 border-b border-border-subtle bg-surface-solid/80 backdrop-blur-sm shrink-0 z-nav">
  <!-- Breadcrumbs -->
  <Breadcrumbs />

  <div class="flex-1"></div>

  <!-- Status indicators -->
  <div class="flex items-center gap-3">
    <!-- Active agents indicator -->
    {#if activeCount > 0}
      <div class="flex items-center gap-1.5 text-xs">
        <span class="w-2 h-2 rounded-full bg-status-active animate-pulse"></span>
        <span class="text-text-dim data-readout">{activeCount} active</span>
      </div>
    {/if}

    <!-- SSE connection status -->
    <div
      class="w-2 h-2 rounded-full {sseStatus === 'connected' ? 'bg-status-active' : 'bg-status-inactive'}"
      title="SSE: {sseStatus}"
    ></div>

    <!-- Command palette trigger -->
    <button
      onclick={() => onPaletteOpen?.()}
      class="flex items-center gap-1.5 px-2 py-1 rounded-md text-xs text-text-muted
             bg-surface hover:bg-surface-2 border border-border-subtle transition-colors"
      title="Command Palette (Cmd+K)"
    >
      <span class="text-text-dim">⌘K</span>
    </button>

    <!-- Trigger run -->
    {#if onTrigger}
      <button
        onclick={onTrigger}
        disabled={triggering}
        class="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium
               bg-accent-blue/20 text-accent-blue hover:bg-accent-blue/30
               disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        {#if triggering}
          <span class="animate-spin">↻</span> Triggering...
        {:else}
          ▶ Run
        {/if}
      </button>
    {/if}
  </div>
</header>
