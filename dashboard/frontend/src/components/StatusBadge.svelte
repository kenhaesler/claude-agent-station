<script lang="ts">
  interface Props {
    value: string | null;
    variant?: 'verdict' | 'status' | 'mode';
  }

  let { value, variant = 'verdict' }: Props = $props();

  const colorMap: Record<string, string> = {
    // Verdicts
    APPROVE: 'bg-approve/20 text-approve',
    REJECT: 'bg-reject/20 text-reject',
    OPEN_PR: 'bg-pr/20 text-pr',
    // Statuses
    completed: 'bg-approve/20 text-approve',
    running: 'bg-info/20 text-info',
    failed: 'bg-reject/20 text-reject',
    // Modes
    full: 'bg-pr/20 text-pr',
    analyze: 'bg-info/20 text-info',
    // Priorities
    high: 'bg-reject/20 text-reject',
    medium: 'bg-warning/20 text-warning',
    low: 'bg-text-dim/20 text-text-dim',
  };

  let classes = $derived(colorMap[value ?? ''] ?? 'bg-surface-2 text-text-dim');
</script>

{#if value}
  <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium {classes}">
    {value}
  </span>
{/if}
