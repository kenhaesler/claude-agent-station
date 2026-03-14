<script lang="ts">
  import { Canvas } from '@threlte/core';
  import SpaceScene from './SpaceScene.svelte';

  interface Props {
    enabled?: boolean;
    phase?: string;
  }

  let { enabled = true, phase = 'idle' }: Props = $props();
</script>

{#if enabled}
  <div class="w-full h-full">
    <Canvas>
      <SpaceScene {phase} />
    </Canvas>
  </div>
{/if}
