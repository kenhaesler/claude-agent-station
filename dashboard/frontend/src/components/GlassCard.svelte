<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    glow?: 'blue' | 'emerald' | 'purple' | 'red' | 'cyan' | 'none';
    animated?: boolean;
    class?: string;
    children: Snippet;
  }

  let { glow = 'none', animated = false, class: className = '', children }: Props = $props();

  const glowMap: Record<string, string> = {
    blue: 'glow-blue',
    emerald: 'glow-emerald',
    purple: 'glow-purple',
    red: 'glow-red',
    cyan: 'glow-cyan',
    none: '',
  };

  let glowClass = $derived(glowMap[glow] ?? '');
  let animClass = $derived(animated ? 'animate-fade-in-up' : '');
  let hasGlow = $derived(glow !== 'none');
</script>

<div class="glass-card glass rounded-lg {glowClass} {animClass} {className}" class:hud-corners={hasGlow}>
  {@render children()}
</div>

<style>
  .glass-card {
    position: relative;
    border-image: linear-gradient(90deg, transparent, rgba(6, 182, 212, 0.15), transparent) 1;
  }

  /* HUD corner brackets — only on glowing cards */
  .hud-corners::before,
  .hud-corners::after {
    content: '';
    position: absolute;
    width: 12px;
    height: 12px;
    pointer-events: none;
    z-index: 1;
  }

  .hud-corners::before {
    top: -1px;
    left: -1px;
    border-top: 1.5px solid rgba(6, 182, 212, 0.4);
    border-left: 1.5px solid rgba(6, 182, 212, 0.4);
  }

  .hud-corners::after {
    bottom: -1px;
    right: -1px;
    border-bottom: 1.5px solid rgba(6, 182, 212, 0.4);
    border-right: 1.5px solid rgba(6, 182, 212, 0.4);
  }
</style>
