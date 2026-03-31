<script lang="ts">
  let {
    sender,
    message,
    time,
    type = 'peer',
  }: {
    sender: string;
    message: string;
    time: string;
    type?: 'peer' | 'lead';
  } = $props();

  let isPeer = $derived(type === 'peer');
</script>

<div
  style="margin-top: 10px; padding: 8px 10px; border-radius: 8px;
    background: {isPeer ? 'rgba(99,102,180,0.04)' : 'rgba(176,96,48,0.04)'};
    border: 1px solid {isPeer ? 'rgba(99,102,180,0.08)' : 'rgba(176,96,48,0.08)'};
    font-size: 13px; color: #3D2A1A; line-height: 1.5;"
>
  <span style="color: {isPeer ? 'rgba(80,82,150,0.8)' : '#B06030'}; font-weight: 600;">{sender}:</span>
  "{message}"
  <div style="font-size: 11px; color: #A08E7A; margin-top: 4px;">{time}</div>
</div>
