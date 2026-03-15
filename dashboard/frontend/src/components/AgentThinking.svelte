<script lang="ts">
  import { themeStore } from '../lib/theme.svelte';

  interface Props {
    color?: string;
  }

  let { color }: Props = $props();

  let resolvedColor = $derived(color ?? themeStore.theme.colors['--color-agent-dev-0']);
</script>

<div class="flex items-center gap-1 py-1">
  {#each [0, 1, 2] as i}
    <div
      class="w-1.5 h-1.5 rounded-full"
      style="background: {resolvedColor}; animation: pulse-dot 1.2s ease-in-out {i * 0.2}s infinite"
    ></div>
  {/each}
</div>
