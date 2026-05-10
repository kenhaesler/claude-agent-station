<script lang="ts">
  import VaporBackground from '../background/VaporBackground.svelte';
  import CyberpunkFX from '../background/CyberpunkFX.svelte';
  import TopNav from './TopNav.svelte';
  import LiveTicker from './LiveTicker.svelte';
  import StationStatusFooter from './StationStatusFooter.svelte';
  import { appearance } from '../../lib/appearance.svelte';
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

{#if appearance.theme === 'cyberpunk'}
  <CyberpunkFX />
{:else}
  <VaporBackground />
{/if}

<TopNav
  {onTrigger}
  {triggering}
  {sseConnected}
  {activeCount}
/>

<LiveTicker />

<main
  id="main-content"
  class="relative overflow-x-hidden"
  style:z-index="1"
  style:flex="1 1 auto"
  style:min-height="0"
>
  {@render children()}
</main>

<StationStatusFooter />
