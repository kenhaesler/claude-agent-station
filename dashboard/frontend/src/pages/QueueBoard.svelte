<script lang="ts">
  import { listQueue, getQueueStats, getBackpressure, updateQueueItem } from '../lib/api';
  import { navigate } from '../lib/router.svelte';
  import type { QueueItem, QueueStats, BackpressureStatus } from '../lib/types';
  import QueueColumn from '../components/queue/QueueColumn.svelte';
  import QueueStatsBar from '../components/queue/QueueStatsBar.svelte';
  import SlidePanel from '../components/overlays/SlidePanel.svelte';
  import EmptyState from '../components/data-display/EmptyState.svelte';
  import SkeletonLoader from '../components/data-display/SkeletonLoader.svelte';

  let items = $state<QueueItem[]>([]);
  let stats = $state<QueueStats | null>(null);
  let backpressure = $state<BackpressureStatus | null>(null);
  let selectedItem = $state<QueueItem | null>(null);
  let panelOpen = $state(false);
  let loading = $state(true);

  // Group items into kanban columns
  const columnDefs = [
    { title: 'Waiting', states: ['pending', 'assigned'], color: 'var(--color-queue-pending)' },
    { title: 'Active', states: ['claimed', 'planning', 'in_progress'], color: 'var(--color-queue-active)' },
    { title: 'Review', states: ['review', 'verifying'], color: 'var(--color-queue-review)' },
    { title: 'Done', states: ['approved', 'completed'], color: 'var(--color-queue-completed)' },
    { title: 'Problem', states: ['rejected', 'escalated', 'failed', 'paused', 'cancelled'], color: 'var(--color-queue-failed)' },
  ];

  let columns = $derived(
    columnDefs.map(col => ({
      ...col,
      items: items.filter(i => col.states.includes(i.state)).sort((a, b) => b.priority - a.priority),
    }))
  );

  let allEmpty = $derived(columns.every(c => c.items.length === 0));

  async function loadData() {
    // ``limit=100`` matches the backend cap on /api/queue (Pydantic
    // ``Query(le=100)``). Asking for more makes the request 422 and
    // — because Promise.allSettled swallows the rejection — the board
    // silently shows "Queue is empty" even when there are pending
    // items, while the KPI card (which uses getQueueStats) shows the
    // real count. If we ever need >100, raise the backend cap first.
    const [qRes, sRes, bRes] = await Promise.allSettled([
      listQueue({ limit: 100 }),
      getQueueStats(),
      getBackpressure(),
    ]);
    if (qRes.status === 'fulfilled') items = qRes.value.items;
    if (sRes.status === 'fulfilled') stats = sRes.value;
    if (bRes.status === 'fulfilled') backpressure = bRes.value;
    loading = false;
  }

  $effect(() => {
    loadData();
    const interval = setInterval(loadData, 15_000);
    return () => clearInterval(interval);
  });

  function handleItemClick(item: QueueItem) {
    selectedItem = item;
    panelOpen = true;
  }
</script>

<div class="space-y-4 animate-fade-in-up queue-board-pro">
  <div class="qb-page-head">
    <h1 class="qb-title">Queue</h1>
    <div class="qb-meta">
      {#if stats?.total != null}<span><b>{stats.total}</b> total</span>{/if}
      {#if backpressure}<span class="sep">·</span><span>Backpressure <b style="color: var(--{backpressure.level === 'GREEN' ? 'go' : backpressure.level === 'YELLOW' ? 'caution' : backpressure.level === 'RED' ? 'abort' : 'critical'})">{backpressure.level}</b></span>{/if}
    </div>
  </div>

  <!-- Stats bar (legacy component, scoped restyle below) -->
  <div class="qb-stats">
    <QueueStatsBar {stats} {backpressure} />
  </div>

  <!-- Kanban columns -->
  {#if loading}
    <div class="card p-8"><SkeletonLoader lines={6} /></div>
  {:else if allEmpty}
    <div class="card">
      <EmptyState
        title="Queue is empty"
        description="Issues will appear here when the agent picks them up"
        icon="☰"
      />
    </div>
  {:else}
    <div class="qb-kanban">
      {#each columns as col}
        <QueueColumn
          title={col.title}
          color={col.color}
          items={col.items}
          onItemClick={handleItemClick}
        />
      {/each}
    </div>
  {/if}
</div>

<style>
  /* Pro restyle for the page chrome + column treatment */
  .queue-board-pro :global(.qb-page-head) {
    display: flex; align-items: center; justify-content: space-between;
    padding: 6px 0 10px;
    border-bottom: 1px solid var(--rule);
  }
  .queue-board-pro :global(.qb-title) {
    margin: 0;
    font-family: var(--pro-sans);
    font-size: 14px; font-weight: 700;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--ink);
  }
  .queue-board-pro :global(.qb-meta) {
    font-family: var(--pro-mono);
    font-size: 11px; color: var(--graphite);
    display: flex; gap: 8px; align-items: center;
  }
  .queue-board-pro :global(.qb-meta b)   { color: var(--ink); font-weight: 500; }
  .queue-board-pro :global(.qb-meta .sep){ color: var(--ash); }

  .queue-board-pro :global(.qb-kanban) {
    display: grid;
    grid-template-columns: repeat(5, minmax(220px, 1fr));
    gap: 0;
    border: 1px solid var(--rule);
    background: var(--paper);
    min-height: 320px;
    overflow-x: auto;
  }
  /* Override QueueColumn defaults to look like a Pro kanban column */
  .queue-board-pro :global(.qb-kanban > div) {
    min-width: 0;
    max-width: none;
    border-right: 1px solid var(--rule);
    background: var(--paper);
  }
  .queue-board-pro :global(.qb-kanban > div:last-child) { border-right: none; }
  /* Column header (rendered by QueueColumn): retune to Pro stencil */
  .queue-board-pro :global(.qb-kanban > div > div:first-child) {
    height: 32px;
    padding: 0 14px !important;
    margin: 0 !important;
    background: var(--paper-2);
    border-bottom: 1px solid var(--rule);
    font-family: var(--pro-sans);
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.2em; text-transform: uppercase;
    color: var(--ash);
  }
  .queue-board-pro :global(.qb-kanban > div > div:first-child > span:first-child) {
    width: 4px !important; height: 14px !important;
    border-radius: 0 !important;
  }
  .queue-board-pro :global(.qb-kanban > div > div:first-child > span:nth-child(2)) {
    color: var(--ink) !important;
    font-family: var(--pro-sans) !important;
  }
  .queue-board-pro :global(.qb-kanban > div > div:first-child > span:last-child) {
    color: var(--graphite) !important;
    font-family: var(--pro-mono) !important;
    font-size: 11px !important;
  }
  /* Cards body: padding adjustment */
  .queue-board-pro :global(.qb-kanban > div > div:nth-child(2)) {
    padding: 10px !important;
  }

  /* Stats bar — restyle to flat paper strip.
     Scoped to .qb-stats so QueueCards (also using .glass) keep their original styling. */
  .queue-board-pro :global(.qb-stats .glass) {
    background: var(--paper-2) !important;
    border: 1px solid var(--rule) !important;
    border-radius: 0 !important;
    box-shadow: none !important;
  }
</style>

<!-- Item detail panel -->
<SlidePanel open={panelOpen} onClose={() => panelOpen = false} title="Queue Item">
  {#if selectedItem}
    {@const item = selectedItem}
    <div class="space-y-4 text-sm">
      <div>
        <div class="text-tertiary text-xs mb-1">Issue</div>
        <div class="text-primary font-medium">
          {#if item.issue_number}<span class="text-indigo">#{item.issue_number}</span>{/if}
          {item.issue_title ?? 'Untitled'}
        </div>
      </div>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <div class="text-tertiary text-xs mb-1">State</div>
          <div class="text-secondary capitalize">{item.state}</div>
        </div>
        <div>
          <div class="text-tertiary text-xs mb-1">Priority</div>
          <div class="text-secondary font-mono">{item.priority}</div>
        </div>
        <div>
          <div class="text-tertiary text-xs mb-1">Mode</div>
          <div class="text-secondary">{item.mode ?? '-'}</div>
        </div>
        <div>
          <div class="text-tertiary text-xs mb-1">Retries</div>
          <div class="text-secondary font-mono">{item.retry_count}/{item.max_retries}</div>
        </div>
      </div>
      <div>
        <div class="text-tertiary text-xs mb-1">Project</div>
        <div class="text-secondary">{item.project_repo}</div>
      </div>
      {#if item.run_id}
        <div>
          <div class="text-tertiary text-xs mb-1">Run</div>
          <a href="/runs/{item.run_id}" class="text-indigo hover:underline">{item.run_id}</a>
        </div>
      {/if}
      {#if item.error_message}
        <div>
          <div class="text-tertiary text-xs mb-1">Error</div>
          <div class="text-reject text-xs">{item.error_message}</div>
        </div>
      {/if}
      {#if item.confidence != null}
        <div>
          <div class="text-tertiary text-xs mb-1">Confidence</div>
          <div class="text-secondary font-mono">{(item.confidence * 100).toFixed(0)}%</div>
        </div>
      {/if}
    </div>
  {/if}
</SlidePanel>
