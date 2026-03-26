<script lang="ts">
  let {
    name = '',
    color = 'var(--color-info)',
    status = 'idle',
    size = 'md',
  }: {
    name: string;
    color?: string;
    status?: 'active' | 'thinking' | 'idle' | 'error';
    size?: 'sm' | 'md' | 'lg';
  } = $props();

  let initial = $derived(name.charAt(0).toUpperCase());
  let sizeClass = $derived(
    size === 'sm' ? 'w-6 h-6 text-[10px]' :
    size === 'lg' ? 'w-10 h-10 text-sm' :
    'w-8 h-8 text-xs'
  );
</script>

<div class="relative inline-flex items-center justify-center rounded-full font-semibold {sizeClass}"
     style="background: color-mix(in oklch, {color} 25%, transparent); color: {color}">
  {initial}
  {#if status === 'active' || status === 'thinking'}
    <span
      class="absolute -bottom-0.5 -right-0.5 rounded-full border-2 border-surface-solid
             {status === 'active' ? 'bg-status-active' : 'bg-status-thinking'}
             {size === 'sm' ? 'w-2 h-2' : 'w-2.5 h-2.5'}"
    >
      {#if status === 'active'}
        <span class="absolute inset-0 rounded-full bg-status-active animate-ping opacity-40"></span>
      {/if}
    </span>
  {:else if status === 'error'}
    <span class="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-surface-solid bg-status-error"></span>
  {/if}
</div>
