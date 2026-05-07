<script lang="ts">
  let {
    checked = $bindable(false),
    label = '',
    disabled = false,
    onchange,
  }: {
    checked: boolean;
    label?: string;
    disabled?: boolean;
    onchange?: (checked: boolean) => void;
  } = $props();

  function toggle() {
    if (disabled) return;
    checked = !checked;
    onchange?.(checked);
  }
</script>

<label class="inline-flex items-center gap-2 cursor-pointer {disabled ? 'opacity-50 cursor-not-allowed' : ''}">
  <button
    type="button"
    role="switch"
    aria-checked={checked}
    {disabled}
    onclick={toggle}
    class="relative w-9 h-5 rounded-full transition-colors"
    style="background: {checked ? 'var(--color-violet)' : 'var(--color-border-hover)'}; box-shadow: inset 1px 1px 3px rgba(0,0,0,0.08);"
  >
    <span
      class="absolute top-0.5 left-0.5 w-4 h-4 rounded-full transition-transform
             {checked ? 'translate-x-4' : ''}"
      style="background: var(--color-surface-0); box-shadow: 0 1px 2px rgba(0,0,0,0.15);"
    ></span>
  </button>
  {#if label}
    <span class="text-sm text-secondary">{label}</span>
  {/if}
</label>
