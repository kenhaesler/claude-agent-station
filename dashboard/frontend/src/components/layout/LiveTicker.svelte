<script lang="ts">
  import { getAgentEvents } from '../../lib/api';
  import type { AgentEvent } from '../../lib/types';

  let events = $state<AgentEvent[]>([]);

  let segments = $derived.by(() => {
    const segs = events.slice(0, 20).map((e) => {
      const actor = (e.agent_id ?? 'station').split(':').pop() ?? 'station';
      const tool =
        (e.event_data && (e.event_data as Record<string, unknown>).tool as string)
        ?? e.event_type
        ?? 'event';
      const target =
        (e.event_data && (e.event_data as Record<string, unknown>).summary as string)
        ?? (e.event_data && (e.event_data as Record<string, unknown>).file_path as string)
        ?? '';
      return { actor, tool, target };
    });
    if (segs.length === 0) {
      return [{ actor: 'station', tool: 'idle', target: 'no recent activity' }];
    }
    return segs;
  });

  async function load() {
    try {
      events = await getAgentEvents({ limit: 20 });
    } catch {
      // Keep last-good events; ticker just stops advancing.
    }
  }

  $effect(() => {
    load();
    const isHidden = () => typeof document !== 'undefined' && document.visibilityState === 'hidden';
    const t = setInterval(() => { if (!isHidden()) load(); }, 10_000);
    const onVis = () => { if (!isHidden()) load(); };
    document.addEventListener('visibilitychange', onVis);
    return () => {
      clearInterval(t);
      document.removeEventListener('visibilitychange', onVis);
    };
  });
</script>

<div class="ticker" aria-label="Station activity">
  <span class="ticker-tag">// Live</span>
  <div class="ticker-track">
    {#each [...segments, ...segments] as seg}
      <span><b>{seg.actor}</b> · <em>{seg.tool}</em> {seg.target}</span>
    {/each}
  </div>
</div>
