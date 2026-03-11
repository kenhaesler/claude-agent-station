<script lang="ts">
  interface Props {
    value: string | null;
    variant?: 'verdict' | 'status' | 'mode';
  }

  let { value, variant = 'verdict' }: Props = $props();

  const colorMap: Record<string, string> = {
    // Verdicts
    APPROVE: 'bg-approve/20 text-approve shadow-[0_0_8px_rgba(34,197,94,0.2)]',
    REJECT: 'bg-reject/20 text-reject shadow-[0_0_8px_rgba(239,68,68,0.2)]',
    PR: 'bg-pr/20 text-pr shadow-[0_0_8px_rgba(168,85,247,0.2)]',
    // Statuses
    completed: 'bg-approve/20 text-approve shadow-[0_0_8px_rgba(34,197,94,0.15)]',
    running: 'bg-info/20 text-info shadow-[0_0_8px_rgba(59,130,246,0.25)]',
    reviewing: 'bg-warning/20 text-warning shadow-[0_0_8px_rgba(245,158,11,0.25)]',
    failed: 'bg-reject/20 text-reject shadow-[0_0_8px_rgba(239,68,68,0.15)]',
    interrupted: 'bg-warning/20 text-warning shadow-[0_0_8px_rgba(245,158,11,0.15)]',
    // Queue states
    pending: 'bg-warning/20 text-warning shadow-[0_0_8px_rgba(245,158,11,0.15)]',
    assigned: 'bg-info/20 text-info shadow-[0_0_8px_rgba(59,130,246,0.15)]',
    in_progress: 'bg-info/20 text-info shadow-[0_0_8px_rgba(59,130,246,0.25)]',
    review: 'bg-warning/20 text-warning shadow-[0_0_8px_rgba(245,158,11,0.25)]',
    approved: 'bg-approve/20 text-approve shadow-[0_0_8px_rgba(34,197,94,0.15)]',
    rejected: 'bg-reject/20 text-reject shadow-[0_0_8px_rgba(239,68,68,0.15)]',
    paused: 'bg-text-dim/20 text-text-dim',
    // Modes
    full: 'bg-pr/20 text-pr shadow-[0_0_8px_rgba(168,85,247,0.15)]',
    analyze: 'bg-info/20 text-info shadow-[0_0_8px_rgba(59,130,246,0.15)]',
    // Priorities
    high: 'bg-reject/20 text-reject shadow-[0_0_8px_rgba(239,68,68,0.15)]',
    medium: 'bg-warning/20 text-warning shadow-[0_0_8px_rgba(245,158,11,0.15)]',
    low: 'bg-text-dim/20 text-text-dim',
  };

  let classes = $derived(colorMap[value ?? ''] ?? 'bg-surface-2 text-text-dim');
</script>

{#if value}
  <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium {classes}">
    {value}
  </span>
{/if}
