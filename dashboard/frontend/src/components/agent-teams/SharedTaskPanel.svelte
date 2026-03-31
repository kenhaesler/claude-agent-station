<script lang="ts">
  interface TaskData {
    name: string;
    status: 'progress' | 'plan-review' | 'blocked' | 'pending';
    owner: string;
    dependency?: string;
  }

  let {
    tasks = [],
  }: {
    tasks?: TaskData[];
  } = $props();

  const badgeStyles: Record<string, { bg: string; color: string; label: string }> = {
    progress: { bg: 'rgba(46,125,50,0.08)', color: '#2E7D32', label: 'In Progress' },
    'plan-review': { bg: 'rgba(176,96,48,0.08)', color: '#B06030', label: 'Plan Review' },
    blocked: { bg: 'rgba(160,142,122,0.08)', color: '#8C7A66', label: 'Blocked' },
    pending: { bg: 'rgba(160,142,122,0.06)', color: '#A08E7A', label: 'Pending' },
  };
</script>

<div style="padding: 14px 16px; padding-top: 20px; border-bottom: 1px solid rgba(240,220,200,0.15);">
  <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: #B06030; margin-bottom: 10px;">Shared Tasks</div>

  {#each tasks as task}
    {@const badge = badgeStyles[task.status] ?? badgeStyles.pending}
    <div
      style="padding: 10px 12px; border-radius: 10px; margin-bottom: 7px;
        background: rgba(255,251,247,0.50); border: 1px solid rgba(240,220,200,0.20);
        box-shadow: 1px 1px 4px rgba(0,0,0,0.02), -1px -1px 4px rgba(255,255,255,0.25);
        {task.status === 'blocked' || task.status === 'pending' ? 'opacity: 0.45;' : ''}"
    >
      <div style="font-size: 13px; font-weight: 600; color: #3D2A1A;">{task.name}</div>
      <div style="display: flex; align-items: center; gap: 6px; margin-top: 5px;">
        <span style="padding: 2px 8px; border-radius: 6px; font-size: 10px; font-weight: 600; background: {badge.bg}; color: {badge.color};">{badge.label}</span>
        <span style="font-size: 11px; color: #7A6652;">{task.owner}</span>
      </div>
      {#if task.dependency}
        <div style="font-size: 10px; color: #A08E7A; margin-top: 3px; font-style: italic;">{task.dependency}</div>
      {/if}
    </div>
  {/each}
</div>
