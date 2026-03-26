<script lang="ts">
  let {
    value = $bindable(''),
    options = [],
  }: {
    value: string;
    options: { value: string; label: string }[];
  } = $props();
</script>

<div class="inline-flex rounded-lg border border-border overflow-hidden">
  {#each options as opt, i}
    <button
      type="button"
      class="px-3 py-1.5 text-xs font-medium transition-colors
             {value === opt.value
               ? 'bg-accent-blue/20 text-accent-blue'
               : 'bg-surface text-text-muted hover:text-text-dim hover:bg-surface-2'}
             {i > 0 ? 'border-l border-border' : ''}"
      onclick={() => value = opt.value}
    >
      {opt.label}
    </button>
  {/each}
</div>
