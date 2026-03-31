<script lang="ts">
  let {
    title,
    description,
    icon,
    compact = false,
  }: {
    title: string;
    description?: string;
    icon?: string;
    compact?: boolean;
  } = $props();
</script>

<div class="flex flex-col items-center justify-center {compact ? 'py-6 px-3' : 'py-16 px-4'} text-center gap-2">
  {#if icon}
    <span class="{compact ? 'text-2xl' : 'text-4xl'} opacity-30">{icon}</span>
  {/if}
  <h3 class="text-secondary {compact ? 'text-sm' : 'text-base'} font-medium">{title}</h3>
  {#if description}
    <p class="text-tertiary {compact ? 'text-xs' : 'text-sm'} max-w-xs">{description}</p>
  {/if}
</div>
