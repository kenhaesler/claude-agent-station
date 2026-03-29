<script lang="ts">
  let {
    value = $bindable(''),
    options = [],
    label = '',
    disabled = false,
  }: {
    value: string;
    options: { value: string; label: string }[];
    label?: string;
    disabled?: boolean;
  } = $props();
</script>

<div class="flex flex-col gap-1">
  {#if label}
    <label class="text-xs text-secondary font-medium">{label}</label>
  {/if}
  <select
    bind:value
    {disabled}
    class="w-full px-3 py-2 rounded-lg bg-void text-primary text-sm border border-border
           focus:border-border-focus focus:outline-none disabled:opacity-50 transition-colors"
  >
    {#each options as opt}
      <option value={opt.value}>{opt.label}</option>
    {/each}
  </select>
</div>
