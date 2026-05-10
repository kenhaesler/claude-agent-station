<script lang="ts">
  import VaporBackground from '../background/VaporBackground.svelte';
  import TopNav from './TopNav.svelte';
  import { route } from '../../lib/router.svelte';
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

  // The Pro Dispatch board owns its own dense, edge-to-edge layout
  // (strip + ticker + filters + telemetry + board + footer). Drop the
  // shell's max-width / padding container for it so the bleed isn't
  // boxed in. Other pages keep the centered, padded canvas.
  let isDispatch = $derived(
    route.page === 'command-center' || route.page === 'runs'
  );
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
  style:z-index="1"
  style:min-height="calc(100vh - 40px)"
  style:padding={isDispatch ? '0' : '28px 32px 48px'}
  style:max-width={isDispatch ? 'none' : '1600px'}
  style:margin={isDispatch ? '0' : '0 auto'}
>
  {@render children()}
</main>
