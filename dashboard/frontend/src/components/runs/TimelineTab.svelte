<script lang="ts">
  type TimelineEvent = {
    t: string;
    kind: 'lifecycle' | 'tool' | 'teammate' | 'verdict' | 'conflict';
    event: string;
    source: string;
    source_id: string;
    agent: string | null;
    data: Record<string, unknown> | null;
  };

  type TimelinePage = {
    run_id: string;
    events: TimelineEvent[];
    next_cursor: string | null;
    has_more: boolean;
  };

  type Props = { runId: string };
  let { runId }: Props = $props();

  const ALL_KINDS: TimelineEvent['kind'][] = [
    'lifecycle', 'tool', 'teammate', 'verdict', 'conflict',
  ];

  let activeKinds = $state<Set<TimelineEvent['kind']>>(new Set(ALL_KINDS));
  let events = $state<TimelineEvent[]>([]);
  let cursor = $state<string | null>(null);
  let hasMore = $state(false);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let expanded = $state<Set<string>>(new Set());

  function kindParam(): string {
    if (activeKinds.size === ALL_KINDS.length) return '';
    return `&kinds=${[...activeKinds].join(',')}`;
  }

  async function loadPage(append: boolean) {
    loading = true;
    error = null;
    try {
      const cursorPart = append && cursor ? `&cursor=${encodeURIComponent(cursor)}` : '';
      const r = await fetch(
        `/api/runs/${runId}/timeline?limit=500${kindParam()}${cursorPart}`,
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const page: TimelinePage = await r.json();
      events = append ? [...events, ...page.events] : page.events;
      cursor = page.next_cursor;
      hasMore = page.has_more;
    } catch (e) {
      error = (e as Error).message;
    } finally {
      loading = false;
    }
  }

  function toggleKind(k: TimelineEvent['kind']) {
    const next = new Set(activeKinds);
    if (next.has(k)) next.delete(k); else next.add(k);
    activeKinds = next;
    cursor = null;
    loadPage(false);
  }

  function toggleExpand(key: string) {
    const next = new Set(expanded);
    if (next.has(key)) next.delete(key); else next.add(key);
    expanded = next;
  }

  $effect(() => { loadPage(false); });
</script>

<div class="timeline-tab">
  <div class="filters">
    {#each ALL_KINDS as k}
      <button
        class="chip"
        class:active={activeKinds.has(k)}
        onclick={() => toggleKind(k)}
      >{k}</button>
    {/each}
  </div>

  {#if error}
    <div class="error">Failed: {error}</div>
  {:else if events.length === 0 && !loading}
    <div class="empty">No events in this filter — try clearing it.</div>
  {:else}
    <ul class="events">
      {#each events as ev (ev.source + ':' + ev.source_id)}
        {@const key = ev.source + ':' + ev.source_id}
        <li class="event {ev.kind}">
          <button class="row" onclick={() => toggleExpand(key)}>
            <span class="t">{new Date(ev.t).toLocaleString()}</span>
            <span class="kind">{ev.kind}</span>
            <span class="ev">{ev.event}</span>
            {#if ev.agent}<span class="agent">{ev.agent}</span>{/if}
          </button>
          {#if expanded.has(key) && ev.data}
            <pre class="data">{JSON.stringify(ev.data, null, 2)}</pre>
          {/if}
        </li>
      {/each}
    </ul>
    {#if hasMore}
      <button class="load-more" disabled={loading} onclick={() => loadPage(true)}>
        {loading ? 'Loading…' : 'Load more'}
      </button>
    {/if}
  {/if}
</div>

<style>
  .timeline-tab { display: flex; flex-direction: column; gap: 0.75rem; }
  .filters { display: flex; flex-wrap: wrap; gap: 0.25rem; }
  .chip { padding: 0.15rem 0.5rem; border: 1px solid #444; border-radius: 999px; background: transparent; color: inherit; cursor: pointer; }
  .chip.active { background: #0e8a16; border-color: #0e8a16; color: #fff; }
  .events { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.15rem; }
  .event { border-left: 3px solid #444; padding-left: 0.5rem; }
  .event.lifecycle { border-color: #0e8a16; }
  .event.tool { border-color: #0075ca; }
  .event.teammate { border-color: #fbca04; }
  .event.verdict { border-color: #b60205; }
  .event.conflict { border-color: #d93f0b; }
  .row { display: flex; gap: 0.75rem; background: transparent; border: 0; padding: 0.25rem 0; color: inherit; text-align: left; cursor: pointer; width: 100%; font-family: ui-monospace, monospace; font-size: 0.85rem; }
  .t { color: #888; min-width: 12em; }
  .data { background: #111; padding: 0.5rem; overflow: auto; font-size: 0.8rem; }
  .load-more { align-self: flex-start; padding: 0.4rem 0.9rem; }
  .error { color: #b60205; }
  .empty { color: #888; }
</style>
