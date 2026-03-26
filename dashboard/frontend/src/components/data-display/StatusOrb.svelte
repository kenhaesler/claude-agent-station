<script lang="ts">
  let {
    status,
    size = 'sm',
    pulse = false,
  }: {
    status: 'active' | 'inactive' | 'thinking' | 'error' | 'idle';
    size?: 'sm' | 'md';
    pulse?: boolean;
  } = $props();

  const statusColors: Record<string, string> = {
    active: 'var(--color-status-active)',
    inactive: 'var(--color-status-inactive)',
    thinking: 'var(--color-status-thinking)',
    error: 'var(--color-status-error)',
    idle: 'var(--color-status-idle)',
  };

  let color = $derived(statusColors[status] ?? statusColors.idle);
  let px = $derived(size === 'md' ? 10 : 6);
  let shouldPulse = $derived(pulse || status === 'active' || status === 'thinking');
</script>

<span
  class="relative inline-flex items-center justify-center shrink-0"
  style="width: {px}px; height: {px}px;"
  role="status"
  aria-label="{status}"
>
  <span
    class="absolute inset-0 rounded-full"
    style="background: {color};"
  ></span>
  {#if shouldPulse}
    <span
      class="absolute inset-0 rounded-full animate-ping"
      style="background: {color}; opacity: 0.4; animation-duration: 1.5s;"
    ></span>
  {/if}
</span>
