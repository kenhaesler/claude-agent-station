<script lang="ts">
  let {
    value = $bindable(''),
    placeholder = '',
    label = '',
    type = 'text',
    disabled = false,
  }: {
    value: string;
    placeholder?: string;
    label?: string;
    type?: string;
    disabled?: boolean;
  } = $props();
</script>

<div class="flex flex-col gap-1">
  {#if label}
    <label class="text-xs text-text-dim font-medium">{label}</label>
  {/if}
  <input
    bind:value
    {type}
    {placeholder}
    {disabled}
    class="w-full px-3 py-2 rounded-lg bg-bg text-text text-sm border border-border
           focus:border-focus focus:outline-none disabled:opacity-50 transition-colors
           placeholder:text-text-muted"
  />
</div>
