<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    glow?: 'blue' | 'emerald' | 'purple' | 'red' | 'amber' | 'none';
    animated?: boolean;
    /** Elevation level: 1 (default surface), 2 (raised), 3 (overlay) */
    elevation?: 1 | 2 | 3;
    /** Makes the card clickable with hover effect */
    interactive?: boolean;
    /** Shows loading skeleton overlay */
    loading?: boolean;
    class?: string;
    onclick?: (e: MouseEvent) => void;
    children: Snippet;
  }

  let { glow = 'none', animated = false, elevation = 1, interactive = false, loading = false, class: className = '', onclick, children }: Props = $props();

  let animClass = $derived(animated ? 'animate-fade-in-up' : '');

  const glowBorder: Record<string, string> = {
    blue: 'border-l-accent-blue',
    emerald: 'border-l-accent-emerald',
    purple: 'border-l-accent-purple',
    red: 'border-l-reject',
    amber: 'border-l-warning',
    none: '',
  };
  let borderClass = $derived(glowBorder[glow] ?? '');

  const elevationBg: Record<number, string> = {
    1: 'bg-surface',
    2: 'bg-surface-raised',
    3: 'bg-surface-overlay',
  };

  let interactiveClass = $derived(interactive ? 'cursor-pointer hover:bg-white/[0.03] active:scale-[0.995] transition-all' : '');
</script>

{#if interactive}
  <button
    {onclick}
    class="rounded-xl border border-border {elevationBg[elevation]} {borderClass ? `border-l-2 ${borderClass}` : ''} {animClass} {interactiveClass} text-left w-full {className}"
  >
    {#if loading}
      <div class="absolute inset-0 rounded-xl bg-surface/80 flex items-center justify-center z-10">
        <div class="w-5 h-5 border-2 border-text-muted border-t-transparent rounded-full animate-spin"></div>
      </div>
    {/if}
    {@render children()}
  </button>
{:else}
  <div class="rounded-xl border border-border {elevationBg[elevation]} {borderClass ? `border-l-2 ${borderClass}` : ''} {animClass} {className}">
    {#if loading}
      <div class="absolute inset-0 rounded-xl bg-surface/80 flex items-center justify-center z-10">
        <div class="w-5 h-5 border-2 border-text-muted border-t-transparent rounded-full animate-spin"></div>
      </div>
    {/if}
    {@render children()}
  </div>
{/if}
