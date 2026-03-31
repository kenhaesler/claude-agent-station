<script lang="ts">
  let {
    direction = 'out',
    target,
    type = 'peer',
  }: {
    direction?: 'in' | 'out' | 'both';
    target: string;
    type?: 'peer' | 'lead';
  } = $props();

  const arrows: Record<string, string> = { in: '\u2190', out: '\u2192', both: '\u2194' };
  let isPeer = $derived(type === 'peer');
</script>

<span
  style="display: inline-flex; align-items: center; gap: 4px; padding: 5px 12px; border-radius: 8px; font-size: 12px; font-weight: 600;
    background: {isPeer ? 'rgba(99,102,180,0.08)' : 'rgba(176,96,48,0.08)'};
    color: {isPeer ? 'rgba(80,82,150,0.8)' : '#B06030'};"
>
  <span style="font-size: 10px; opacity: 0.5;">{arrows[direction]}</span>
  {target}
</span>
