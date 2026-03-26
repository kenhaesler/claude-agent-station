<script lang="ts">
  let {
    lines = 3,
    height = '0.75rem',
  }: {
    lines?: number;
    height?: string;
  } = $props();

  const widths = ['100%', '85%', '70%', '90%', '60%', '75%', '95%', '80%'];
</script>

<div class="flex flex-col gap-2.5" role="status" aria-label="Loading">
  {#each Array(lines) as _, i}
    <div
      class="rounded bg-surface-2 shimmer"
      style="height: {height}; width: {widths[i % widths.length]};"
    ></div>
  {/each}
</div>

<style>
  .shimmer {
    background: linear-gradient(
      90deg,
      var(--color-surface-2) 25%,
      var(--color-surface-3) 50%,
      var(--color-surface-2) 75%
    );
    background-size: 200% 100%;
    animation: shimmer 1.5s ease-in-out infinite;
  }

  @keyframes shimmer {
    0% { background-position: 200% 0; }
    100% { background-position: -200% 0; }
  }

  @media (prefers-reduced-motion: reduce) {
    .shimmer { animation: none; }
  }
</style>
