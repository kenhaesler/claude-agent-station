<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    glow?: 'blue' | 'emerald' | 'purple' | 'red' | 'cyan' | 'none';
    animated?: boolean;
    class?: string;
    children: Snippet;
  }

  let { glow = 'none', animated = false, class: className = '', children }: Props = $props();

  let animClass = $derived(animated ? 'animate-fade-in-up' : '');
</script>

<div class="rounded-xl border border-border bg-surface {animClass} {className}">
  {@render children()}
</div>
