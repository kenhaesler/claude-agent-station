<script lang="ts">
  import NavRail from './NavRail.svelte';
  import HeaderBar from './HeaderBar.svelte';
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

<div class="flex h-screen overflow-hidden">
  <!-- Navigation Rail -->
  <NavRail />

  <!-- Main content area -->
  <div class="flex flex-1 flex-col min-w-0">
    <HeaderBar
      {onTrigger}
      {triggering}
      {sseConnected}
      {activeCount}
    />
    <main
      id="main-content"
      class="flex-1 overflow-y-auto overflow-x-hidden"
    >
      <div class="p-6 max-w-[1600px] mx-auto">
        {@render children()}
      </div>
    </main>
  </div>
</div>
