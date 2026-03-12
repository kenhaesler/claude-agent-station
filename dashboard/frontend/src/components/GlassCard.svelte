<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    glow?: 'blue' | 'emerald' | 'purple' | 'red' | 'amber' | 'none';
    animated?: boolean;
    class?: string;
    children: Snippet;
  }

  let { glow = 'none', animated = false, class: className = '', children }: Props = $props();

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
</script>

<div class="rounded-xl border border-border bg-surface {borderClass ? `border-l-2 ${borderClass}` : ''} {animClass} {className}">
  {@render children()}
</div>
