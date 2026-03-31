<script lang="ts">
  let {
    variant = 'default',
    children,
  }: {
    variant?: 'approved' | 'pr' | 'running' | 'failed' | 'pending' | 'active' | 'disabled' | 'plan-review' | 'blocked' | 'default';
    children: import('svelte').Snippet;
  } = $props();

  const styles: Record<string, string> = {
    approved: 'background: rgba(46,125,50,0.14); color: #2E7D32;',
    pr: 'background: rgba(99,102,180,0.12); color: rgba(80,82,150,1);',
    running: 'background: rgba(46,125,50,0.10); color: #2E7D32;',
    failed: 'background: rgba(208,96,80,0.12); color: #D06050;',
    pending: 'background: rgba(176,96,48,0.10); color: #B06030;',
    active: 'background: rgba(46,125,50,0.14); color: #2E7D32;',
    disabled: 'background: rgba(0,0,0,0.04); color: #8C7A66;',
    'plan-review': 'background: rgba(176,96,48,0.10); color: #B06030;',
    blocked: 'background: rgba(160,142,122,0.08); color: #8C7A66;',
    default: 'background: rgba(160,142,122,0.06); color: #A08E7A;',
  };
</script>

<span
  style="display: inline-flex; align-items: center; padding: 4px 12px; border-radius: 999px; font-size: 13px; font-weight: 700; white-space: nowrap; {styles[variant] ?? styles.default}"
>
  {@render children()}
</span>
