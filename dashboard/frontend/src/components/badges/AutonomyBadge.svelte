<script lang="ts">
  import type { AutonomyLevel } from '../../lib/types';

  let {
    level,
    size = 'sm',
  }: {
    level: AutonomyLevel | null | undefined;
    size?: 'xs' | 'sm';
  } = $props();

  // Falsy / unknown levels render as the default 'assisted' pill so we never
  // show a blank space on legacy rows.
  const resolved = $derived(
    level === 'manual' || level === 'assisted' || level === 'auto'
      ? level
      : 'assisted'
  );

  const styles: Record<AutonomyLevel, string> = {
    // Manual — operator drives every decision. Neutral, slightly warm.
    manual: 'background: rgba(160,142,122,0.10); color: #6E5D4A;',
    // Assisted — today's de-facto baseline. Quiet, readable.
    assisted: 'background: rgba(99,102,180,0.10); color: #5D5F94;',
    // Auto — full autonomy. Clearly distinct green so the operator notices.
    auto: 'background: rgba(46,125,50,0.14); color: #2E7D32;',
  };

  const padding = $derived(size === 'xs' ? '2px 8px' : '4px 10px');
  const fontSize = $derived(size === 'xs' ? '11px' : '12px');
</script>

<span
  class="autonomy-badge"
  data-level={resolved}
  title="Autonomy level: {resolved}"
  style="
    display: inline-flex; align-items: center; gap: 4px;
    padding: {padding}; border-radius: 999px;
    font-size: {fontSize}; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.05em;
    white-space: nowrap; line-height: 1;
    {styles[resolved]}
  "
>
  {resolved}
</span>
