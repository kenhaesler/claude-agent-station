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
    PR: 'bg-pr/20 text-pr',
    SKIP: 'bg-text-dim/20 text-text-dim',
    // Statuses
    completed: 'bg-approve/20 text-approve',
    running: 'bg-info/20 text-info',
    reviewing: 'bg-warning/20 text-warning',
    failed: 'bg-reject/20 text-reject',
    interrupted: 'bg-warning/20 text-warning',
    // Queue states
    pending: 'bg-warning/20 text-warning',
    assigned: 'bg-info/20 text-info',
    in_progress: 'bg-info/20 text-info',
    review: 'bg-warning/20 text-warning',
    approved: 'bg-approve/20 text-approve',
    rejected: 'bg-reject/20 text-reject',
    paused: 'bg-text-dim/20 text-text-dim',
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
