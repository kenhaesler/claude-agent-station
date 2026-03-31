<script lang="ts">
  interface ActivityEvent {
    type: 'peer' | 'lead' | 'system';
    sender?: string;
    target?: string;
    message: string;
    time: string;
  }

  let {
    events = [],
  }: {
    events?: ActivityEvent[];
  } = $props();

  const typeColors: Record<string, string> = {
    peer: 'rgba(80,82,150,0.8)',
    lead: '#B06030',
    system: '#2E7D32',
  };
</script>

<div style="padding: 14px 16px; flex: 1;">
  <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: #B06030; margin-bottom: 10px;">Activity</div>

  {#each events as event, i}
    <div
      style="padding: 8px 0; font-size: 12px; color: #3D2A1A; line-height: 1.4;
        border-bottom: 1px solid rgba(240,220,200,0.10);
        animation: fade-in 0.4s ease both;
        animation-delay: {i * 0.1}s;"
    >
      {#if event.sender && event.target}
        <span style="color: {typeColors[event.type]}; font-weight: 600;">{event.sender} &rarr; {event.target}</span>: {event.message}
      {:else if event.sender}
        <span style="color: {typeColors[event.type]}; font-weight: 600;">{event.sender}</span> {event.message}
      {:else}
        {event.message}
      {/if}
      <div style="font-size: 10px; color: #A08E7A; margin-top: 3px;">{event.time}</div>
    </div>
  {/each}

  {#if events.length === 0}
    <div style="font-size: 13px; color: #8C7A66; text-align: center; padding: 20px 0;">No activity yet</div>
  {/if}
</div>

<style>
  @keyframes fade-in {
    from { opacity: 0; transform: translateY(4px); }
    to { opacity: 1; transform: translateY(0); }
  }
</style>
