<script lang="ts">
  interface Props {
    /** Number of skeleton rows to render */
    rows?: number;
    /** Height of each row in pixels */
    height?: number;
    /** Whether to show a circular avatar placeholder */
    avatar?: boolean;
  }

  let { rows = 3, height = 16, avatar = false }: Props = $props();
</script>

<div class="space-y-3 animate-pulse" role="status" aria-label="Loading">
  {#each Array(rows) as _, i}
    <div class="flex items-center gap-3">
      {#if avatar && i === 0}
        <div class="w-8 h-8 rounded-full bg-surface-2 shrink-0"></div>
      {/if}
      <div
        class="bg-surface-2 rounded"
        style="height: {height}px; width: {i === 0 ? '75%' : i === rows - 1 ? '50%' : '90%'}"
      ></div>
    </div>
  {/each}
</div>
