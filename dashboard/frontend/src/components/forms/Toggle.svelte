<script lang="ts">
  let {
    checked = $bindable(false),
    label = '',
    disabled = false,
  }: {
    checked: boolean;
    label?: string;
    disabled?: boolean;
  } = $props();
</script>

<label class="inline-flex items-center gap-2 cursor-pointer {disabled ? 'opacity-50 cursor-not-allowed' : ''}">
  <button
    type="button"
    role="switch"
    aria-checked={checked}
    {disabled}
    onclick={() => { if (!disabled) checked = !checked; }}
    class="relative w-9 h-5 rounded-full transition-colors duration-normal
           {checked ? 'bg-accent-blue' : 'bg-surface-3'}"
  >
    <span
      class="absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-text transition-transform duration-normal
             {checked ? 'translate-x-4' : ''}"
    ></span>
  </button>
  {#if label}
    <span class="text-sm text-text-dim">{label}</span>
  {/if}
</label>
