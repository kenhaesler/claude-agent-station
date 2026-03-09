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
</script>

<div class="glass rounded-xl {glowClass} {animClass} {className}">
  {@render children()}
</div>
