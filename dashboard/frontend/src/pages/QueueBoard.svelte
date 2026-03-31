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
    const [qRes, sRes, bRes] = await Promise.allSettled([
      listQueue({ limit: 200 }),
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

<div class="space-y-4 animate-fade-in-up">
  <div class="flex items-center justify-between">
    <h1 class="text-lg font-semibold text-primary">Queue Board</h1>
  </div>

  <!-- Stats bar -->
  <QueueStatsBar {stats} {backpressure} />

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
    <div class="flex gap-3 overflow-x-auto pb-4" style="min-height: 300px;">
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
