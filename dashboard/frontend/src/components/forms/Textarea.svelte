<script lang="ts">
  let {
    value = $bindable(''),
    placeholder = '',
    label = '',
    rows = 4,
    disabled = false,
  }: {
    value: string;
    placeholder?: string;
    label?: string;
    rows?: number;
    disabled?: boolean;
  } = $props();
</script>

<div class="flex flex-col gap-1">
  {#if label}
    <label class="text-xs text-secondary font-medium">{label}</label>
  {/if}
  <textarea
    bind:value
    {placeholder}
    {rows}
    {disabled}
    class="w-full px-3 py-2 rounded-lg bg-void text-primary text-sm border border-border
           focus:border-border-focus focus:outline-none disabled:opacity-50 transition-colors
           placeholder:text-tertiary resize-y font-mono"
  ></textarea>
</div>
