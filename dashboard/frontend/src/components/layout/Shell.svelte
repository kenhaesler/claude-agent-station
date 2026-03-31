<script lang="ts">
  import VaporBackground from '../background/VaporBackground.svelte';
  import TopNav from './TopNav.svelte';
  import type { Snippet } from 'svelte';

  let {
    children,
    onTrigger,
    triggering = false,
    sseConnected = false,
    activeCount = 0,
  }: {
    children: Snippet;
    onTrigger?: () => void;
    triggering?: boolean;
    sseConnected?: boolean;
    activeCount?: number;
  } = $props();
</script>

<VaporBackground />

<TopNav
  {onTrigger}
  {triggering}
  {sseConnected}
  {activeCount}
/>

<main
  id="main-content"
  class="relative overflow-x-hidden"
  style="z-index: 1; padding: 28px 32px 48px; min-height: calc(100vh - 54px); max-width: 1600px; margin: 0 auto;"
>
  {@render children()}
</main>
