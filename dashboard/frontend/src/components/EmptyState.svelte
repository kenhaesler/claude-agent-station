<script lang="ts">
  interface Props { message?: string; }
  let { message = 'No data available' }: Props = $props();
</script>

<div class="flex flex-col items-center justify-center py-12 text-text-dim">
  <span class="text-4xl mb-3">∅</span>
  <p class="text-sm">{message}</p>
</div>
