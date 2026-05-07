<!-- dashboard/frontend/src/components/vision/CoverageChecklist.svelte -->
<script lang="ts">
  let { covered = [] }: { covered: string[] } = $props();

  const SECTIONS = [
    { key: 'problem', label: 'Problem' },
    { key: 'users', label: 'Users' },
    { key: 'end_state', label: 'End-state' },
    { key: 'non_goals', label: 'Non-goals' },
    { key: 'principles', label: 'Principles' },
    { key: 'horizons', label: 'Horizons' },
    { key: 'anti_patterns', label: 'Anti-patterns' },
  ];

  function isCovered(key: string) {
    return covered.includes(key);
  }
</script>

<div class="flex flex-wrap gap-2 text-xs">
  <span class="text-tertiary">Coverage:</span>
  {#each SECTIONS as s}
    <span
      class="px-2 py-0.5 rounded-full border"
      style={isCovered(s.key)
        ? 'background: rgba(46,125,50,0.10); color: #2E7D32; border-color: rgba(46,125,50,0.30);'
        : 'background: var(--color-surface-1); color: var(--color-tertiary); border-color: var(--color-border);'}
    >
      {isCovered(s.key) ? '▣' : '□'} {s.label}
    </span>
  {/each}
</div>
