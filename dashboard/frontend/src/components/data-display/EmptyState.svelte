<script lang="ts">
  let {
    title,
    description,
    icon,
  }: {
    title: string;
    description?: string;
    icon?: string;
  } = $props();
</script>

<div class="flex flex-col items-center justify-center py-16 px-4 text-center gap-3">
  {#if icon}
    <span class="text-4xl opacity-40">{icon}</span>
  {/if}
  <h3 class="text-secondary text-base font-medium">{title}</h3>
  {#if description}
    <p class="text-tertiary text-sm max-w-xs">{description}</p>
  {/if}
</div>
