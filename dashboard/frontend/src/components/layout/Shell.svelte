<script lang="ts">
  import NavRail from './NavRail.svelte';
  import HeaderBar from './HeaderBar.svelte';
  import type { Snippet } from 'svelte';

  let { children }: { children: Snippet } = $props();
</script>

<div class="flex h-screen overflow-hidden">
  <!-- Nav Rail -->
  <NavRail />

  <!-- Main content area -->
  <div class="flex flex-1 flex-col min-w-0">
    <HeaderBar />
    <main id="main-content" class="flex-1 overflow-y-auto overflow-x-hidden p-4 lg:p-6">
      {@render children()}
    </main>
  </div>
</div>
