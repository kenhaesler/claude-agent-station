<script lang="ts">
  import { getActiveEmployees, getAgentEvents } from '../../lib/api';
  import type { ActiveEmployee, AgentEvent } from '../../lib/types';

  let active = $state<ActiveEmployee[]>([]);
  let events = $state<AgentEvent[]>([]);
  let clockNow = $state('00:00:00');

  function pad2(n: number) { return String(n).padStart(2, '0'); }
  function fmtTok(n: number | null | undefined): string {
    if (n == null || n === 0) return '—';
    if (n < 1000) return String(n);
    if (n < 10_000) return (n / 1000).toFixed(1) + 'K';
    if (n < 1_000_000) return Math.round(n / 1000) + 'K';
    return (n / 1_000_000).toFixed(1) + 'M';
  }
  function shortId(id: string | null | undefined): string {
    if (!id) return '—';
    return id.replace(/^run-(vb-)?/, '…');
  }

  let latest = $derived(active[0] ?? null);

  let footerEvent = $derived.by(() => {
    const e = events[0];
    if (!e) return { actor: '—', tool: '', target: 'awaiting events' };
    const actor = (e.agent_id ?? 'station').split(':').pop() ?? 'station';
    const tool = ((e.event_data && (e.event_data as Record<string, unknown>).tool as string) ?? e.event_type ?? '').toString();
    const target = ((e.event_data && (e.event_data as Record<string, unknown>).summary as string)
      ?? (e.event_data && (e.event_data as Record<string, unknown>).file_path as string)
      ?? '').toString();
    return { actor, tool, target };
  });

  let liveTokens = $derived(latest?.tokens_total ?? 0);

  async function load() {
    try {
      const [a, e] = await Promise.allSettled([
        getActiveEmployees(),
        getAgentEvents({ limit: 5 }),
      ]);
      if (a.status === 'fulfilled') active = a.value;
      if (e.status === 'fulfilled') events = e.value;
    } catch {
      // Keep last-good state.
    }
  }

  $effect(() => {
    load();
    const isHidden = () => typeof document !== 'undefined' && document.visibilityState === 'hidden';
    const t = setInterval(() => { if (!isHidden()) load(); }, 10_000);
    const c = setInterval(() => {
      const d = new Date();
      clockNow = `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
    }, 1000);
    const onVis = () => { if (!isHidden()) load(); };
    document.addEventListener('visibilitychange', onVis);
    return () => {
      clearInterval(t);
      clearInterval(c);
      document.removeEventListener('visibilitychange', onVis);
    };
  });
</script>

<footer class="tele" aria-label="Station status">
  <span class="now">
    <span class="run-tick"></span>
    <span>{clockNow}</span>
  </span>
  <span><b>{latest ? shortId(latest.run_id) : '—'}</b></span>
  <span class="ev">
    {#if footerEvent.actor !== '—'}
      {footerEvent.actor} · <em>{footerEvent.tool}</em> {footerEvent.target}
    {:else}
      awaiting events
    {/if}
  </span>
  <span data-testid="footer-tokens">{liveTokens > 0 ? `+${fmtTok(liveTokens)}` : '—'}</span>
</footer>
