<script lang="ts">
  import {
    listQueue,
    getQueueStats,
    getBackpressure,
    updateQueueItem,
    deleteQueueItem,
    purgeQueue,
    batchPauseQueue,
  } from '../lib/api';
  import { addToast } from '../lib/toast.svelte';
  import { navigate } from '../lib/router.svelte';
  import { flap } from '../lib/design/flap';
  import type { QueueItem, QueueStats, BackpressureStatus } from '../lib/types';
  import SlidePanel from '../components/overlays/SlidePanel.svelte';
  import SkeletonLoader from '../components/data-display/SkeletonLoader.svelte';

  let items = $state<QueueItem[]>([]);
  let stats = $state<QueueStats | null>(null);
  let backpressure = $state<BackpressureStatus | null>(null);
  let selectedItem = $state<QueueItem | null>(null);
  let panelOpen = $state(false);
  let loading = $state(true);
  let refreshing = $state(false);

  // Column groupings mirror the draft kanban (5 lanes).
  const columnDefs: { key: string; title: string; states: string[] }[] = [
    { key: 'waiting', title: 'Waiting', states: ['pending', 'assigned'] },
    { key: 'active',  title: 'Active',  states: ['claimed', 'planning', 'in_progress'] },
    { key: 'review',  title: 'Review',  states: ['review', 'verifying'] },
    { key: 'done',    title: 'Done',    states: ['approved', 'completed'] },
    { key: 'problem', title: 'Problem', states: ['rejected', 'escalated', 'failed', 'paused', 'cancelled'] },
  ];

  let columns = $derived(
    columnDefs.map(col => ({
      ...col,
      items: items
        .filter(i => col.states.includes(i.state))
        .sort((a, b) => b.priority - a.priority),
    }))
  );

  // Bucket counts for the qbar — keep in sync with column groupings so
  // the strip and the kanban never disagree.
  let counts = $derived({
    total:    items.length,
    waiting:  columns[0].items.length,
    active:   columns[1].items.length,
    review:   columns[2].items.length,
    done:     columns[3].items.length,
    problem:  columns[4].items.length,
  });

  let avgTime = $derived(stats?.avg_time_to_complete_ms ?? null);

  function fmtAvg(ms: number | null): string {
    if (ms == null) return '—';
    if (ms < 60_000) return (ms / 1000).toFixed(1) + 's';
    const m = Math.floor(ms / 60_000);
    if (m < 60) return m + 'm';
    return Math.floor(m / 60) + 'h';
  }

  function bpColor(level: string | undefined): string {
    if (level === 'GREEN')  return 'var(--go)';
    if (level === 'YELLOW') return 'var(--caution)';
    if (level === 'RED')    return 'var(--abort)';
    if (level === 'BLACK')  return 'var(--critical)';
    return 'var(--ash)';
  }

  function priorityLabel(p: number): { txt: string; cls: 'high' | 'med' | 'low' } {
    if (p >= 8) return { txt: 'P0', cls: 'high' };
    if (p >= 4) return { txt: 'P1', cls: 'med'  };
    if (p > 0)  return { txt: 'P2', cls: 'low'  };
    return       { txt: 'P3', cls: 'low'  };
  }

  function ageOf(iso: string | null | undefined): string {
    if (!iso) return '—';
    const ms = Date.now() - new Date(iso).getTime();
    if (Number.isNaN(ms) || ms < 0) return '—';
    const m = Math.floor(ms / 60000);
    if (m < 60) return m + 'm';
    if (m < 1440) return Math.floor(m / 60) + 'h';
    return Math.floor(m / 1440) + 'd';
  }

  function cardKind(it: QueueItem): 'live' | 'done' | 'fail' | '' {
    if (['claimed', 'planning', 'in_progress'].includes(it.state)) return 'live';
    if (it.state === 'completed' || it.state === 'approved')       return 'done';
    if (['rejected', 'failed', 'escalated'].includes(it.state))    return 'fail';
    return '';
  }

  async function loadData(opts: { silent?: boolean } = {}) {
    if (!opts.silent) refreshing = true;
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
    refreshing = false;
  }

  $effect(() => {
    loadData({ silent: true });
    const interval = setInterval(() => loadData({ silent: true }), 15_000);
    return () => clearInterval(interval);
  });

  function handleItemClick(item: QueueItem) {
    selectedItem = item;
    panelOpen = true;
  }

  // ── Item actions (claim/pause/purge/batch-pause) ───────
  async function handlePauseItem(it: QueueItem) {
    try {
      await updateQueueItem(it.id, { state: 'paused' });
      await loadData({ silent: true });
      // Close the panel if we were looking at it — the row's about to
      // jump to Problem and the open panel feels stale.
      if (selectedItem?.id === it.id) panelOpen = false;
    } catch (e: any) {
      addToast('error', e?.message ?? 'Pause failed');
    }
  }

  async function handleResumeItem(it: QueueItem) {
    try {
      await updateQueueItem(it.id, { state: 'pending' });
      await loadData({ silent: true });
    } catch (e: any) {
      addToast('error', e?.message ?? 'Resume failed');
    }
  }

  async function handleClaimItem(it: QueueItem) {
    try {
      await updateQueueItem(it.id, { state: 'claimed' });
      await loadData({ silent: true });
    } catch (e: any) {
      addToast('error', e?.message ?? 'Claim failed');
    }
  }

  async function handleDeleteItem(it: QueueItem) {
    if (!confirm(`Delete queue item #${it.id}?`)) return;
    try {
      await deleteQueueItem(it.id);
      await loadData({ silent: true });
      if (selectedItem?.id === it.id) panelOpen = false;
    } catch (e: any) {
      addToast('error', e?.message ?? 'Delete failed');
    }
  }

  async function handleBatchPause(it: QueueItem) {
    if (!it.run_id) return;
    try {
      await batchPauseQueue(it.run_id);
      await loadData({ silent: true });
    } catch (e: any) {
      addToast('error', e?.message ?? 'Batch pause failed');
    }
  }

  async function handlePurge() {
    if (!confirm('Purge completed queue items older than 7 days?')) return;
    try {
      await purgeQueue(7);
      await loadData({ silent: true });
    } catch (e: any) {
      addToast('error', e?.message ?? 'Purge failed');
    }
  }
</script>

<div class="queue-pro animate-fade-in" data-testid="queue-board">
  <!-- Page head -->
  <div class="page-head">
    <h1>Queue</h1>
    <div class="meta">
      <span><b>{counts.total}</b> total</span>
      <span class="sep">·</span>
      <span>
        Backpressure
        <b style="color: {bpColor(backpressure?.level)}">{backpressure?.level ?? '—'}</b>
      </span>
      {#if backpressure}
        <span class="sep">·</span>
        <span>
          {backpressure.effective_concurrent}/{backpressure.max_concurrent}
          <span class="dim">slots</span>
        </span>
      {/if}
    </div>
    <div class="actions">
      <button
        type="button"
        class="opbtn"
        onclick={() => loadData()}
        disabled={refreshing}
        title="Refresh queue"
      >Refresh</button>
      <button
        type="button"
        class="opbtn"
        onclick={handlePurge}
        title="Purge completed items older than 7 days"
      >Purge</button>
    </div>
  </div>

  <!-- Stats / backpressure strip -->
  <div class="qbar">
    <div class="qcell">
      <span class="k">Total</span>
      <span class="v" class:nu={counts.total === 0}>{counts.total}</span>
      <span class="sub">all states</span>
    </div>
    <div class="qcell">
      <span class="k">Waiting</span>
      <span class="v" class:nu={counts.waiting === 0}>{counts.waiting}</span>
      <span class="sub">pending · assigned</span>
    </div>
    <div class="qcell">
      <span class="k">Active</span>
      <span class="v go" class:nu={counts.active === 0}>{counts.active}</span>
      <span class="sub">claimed · planning · in_progress</span>
    </div>
    <div class="qcell">
      <span class="k">Review</span>
      <span class="v caution" class:nu={counts.review === 0}>{counts.review}</span>
      <span class="sub">review · verifying</span>
    </div>
    <div class="qcell">
      <span class="k">Done</span>
      <span class="v" class:nu={counts.done === 0}>{counts.done}</span>
      <span class="sub">approved · completed</span>
    </div>
    <div class="qcell">
      <span class="k">Problem</span>
      <span class="v abort" class:nu={counts.problem === 0}>{counts.problem}</span>
      <span class="sub">rejected · escalated · failed · paused</span>
    </div>
    <div class="qcell">
      <span class="k">Avg time</span>
      <span class="v" class:nu={avgTime == null}>{fmtAvg(avgTime)}</span>
      <span class="sub">{avgTime == null ? 'no completed yet' : 'completed runs'}</span>
    </div>
  </div>

  <!-- Kanban -->
  {#if loading}
    <div class="kanban-loading">
      <SkeletonLoader lines={6} />
    </div>
  {:else}
    <section class="kanban">
      {#each columns as col, ci (col.key)}
        <div class="kcol">
          <div class="kcol-head {col.key}">
            <span class="name">{col.title}</span>
            <span class="count">{col.items.length}</span>
          </div>
          {#if col.items.length === 0}
            <div class="kbody empty">
              {#if col.key === 'waiting'}No issues queued.
              {:else if col.key === 'active'}Nothing in flight.
              {:else if col.key === 'review'}Nothing to review.
              {:else if col.key === 'done'}No completed items.
              {:else}No failures or escalations.
              {/if}
            </div>
          {:else}
            <div class="kbody">
              {#each col.items as it, ii (it.id)}
                {@const pri = priorityLabel(it.priority)}
                {@const kind = cardKind(it)}
                {@const titleText = it.issue_title ?? 'untitled · no issue title'}
                {@const repoShort = it.project_repo?.split('/').pop() ?? ''}
                {@const baseDelay = (ci * 80) + (ii * 40)}
                <!-- svelte-ignore a11y_click_events_have_key_events -->
                <!-- svelte-ignore a11y_no_static_element_interactions -->
                <div
                  class="icard {kind}"
                  onclick={() => handleItemClick(it)}
                >
                  <div class="num">
                    <span use:flap={{ text: it.issue_number != null ? `#${it.issue_number}` : `q${it.id}`, baseDelay }}></span>
                  </div>
                  <div class="title" class:nul={!it.issue_title}>
                    <span use:flap={{ text: titleText, baseDelay: baseDelay + 30 }}></span>
                  </div>
                  <div class="meta">
                    {#if repoShort}
                      <span class="repo"><span use:flap={{ text: repoShort, baseDelay: baseDelay + 80 }}></span></span>
                      <span>·</span>
                    {/if}
                    <span class="state"><span use:flap={{ text: it.state.toUpperCase(), baseDelay: baseDelay + 110 }}></span></span>
                    <span>·</span>
                    <span class="pri {pri.cls}"><span use:flap={{ text: pri.txt, baseDelay: baseDelay + 130 }}></span></span>
                    <span>·</span>
                    <span class="age"><span use:flap={{ text: ageOf(it.created_at), baseDelay: baseDelay + 150 }}></span></span>
                    {#if it.retry_count > 0}
                      <span>·</span>
                      <span class="retry">retry {it.retry_count}/{it.max_retries}</span>
                    {/if}
                  </div>
                </div>
              {/each}
            </div>
          {/if}
        </div>
      {/each}
    </section>
  {/if}
</div>

<!-- Item detail panel -->
<SlidePanel open={panelOpen} onClose={() => panelOpen = false} title={selectedItem ? `Issue ${selectedItem.issue_number != null ? '#' + selectedItem.issue_number : '—'} · ${selectedItem.state.toUpperCase()}` : 'Queue Item'}>
  {#if selectedItem}
    {@const item = selectedItem}
    <div class="qpanel">
      <div class="key-row">
        <label>Issue</label>
        <div class="val">
          {#if item.issue_number}#{item.issue_number}{/if}
          <span class="dim">{item.issue_title ?? 'no title'}</span>
        </div>
      </div>
      <div class="key-row">
        <label>Project</label>
        <div class="val">{item.project_repo}</div>
      </div>
      <div class="key-row">
        <label>State</label>
        <div class="val cap">{item.state}</div>
      </div>
      <div class="key-row">
        <label>Priority</label>
        <div class="val mono">{item.priority}</div>
      </div>
      <div class="key-row">
        <label>Mode</label>
        <div class="val">{item.mode ?? '—'}</div>
      </div>
      <div class="key-row">
        <label>Retries</label>
        <div class="val mono">{item.retry_count} / {item.max_retries}</div>
      </div>
      {#if item.run_id}
        <div class="key-row">
          <label>Run</label>
          <div class="val">
            <a href="/runs/{item.run_id}" onclick={(e) => { e.preventDefault(); navigate(`/runs/${item.run_id}`); panelOpen = false; }}>
              {item.run_id}
            </a>
          </div>
        </div>
      {/if}
      <div class="key-row">
        <label>Confidence</label>
        <div class="val" class:dim={item.confidence == null}>
          {item.confidence == null ? '—' : (item.confidence * 100).toFixed(0) + '%'}
        </div>
      </div>
      <div class="key-row">
        <label>Created</label>
        <div class="val mono">{item.created_at?.replace('T', ' · ').replace('Z', '') ?? '—'}</div>
      </div>
      {#if item.error_message}
        <div class="key-row">
          <label>Error</label>
          <div class="val err">{item.error_message}</div>
        </div>
      {/if}
      <div class="key-row">
        <label>Queue ID</label>
        <div class="val mono dim">{item.id}</div>
      </div>

      <div class="actions">
        {#if ['pending', 'assigned'].includes(item.state)}
          <button type="button" class="opbtn" onclick={() => handleClaimItem(item)}>Claim</button>
        {/if}
        {#if ['pending', 'assigned', 'claimed', 'planning', 'in_progress', 'review', 'verifying'].includes(item.state)}
          <button type="button" class="opbtn" onclick={() => handlePauseItem(item)}>Pause</button>
        {/if}
        {#if item.state === 'paused'}
          <button type="button" class="opbtn primary" onclick={() => handleResumeItem(item)}>Resume</button>
        {/if}
        {#if item.run_id && ['claimed', 'planning', 'in_progress'].includes(item.state)}
          <button type="button" class="opbtn" onclick={() => handleBatchPause(item)} title="Pause all items in this run">
            Batch Pause
          </button>
        {/if}
        <button type="button" class="opbtn danger" onclick={() => handleDeleteItem(item)}>Delete</button>
      </div>
    </div>
  {/if}
</SlidePanel>

<style>
  /* Edge-to-edge Pro layout — page owns its own padding. */
  .queue-pro {
    display: flex;
    flex-direction: column;
    min-height: calc(100vh - 40px);
    background: var(--paper);
    color: var(--ink);
    font-family: var(--pro-sans);
  }

  /* Page head */
  .queue-pro :global(.page-head) {
    display: grid;
    grid-template-columns: auto 1fr auto;
    align-items: center;
    gap: 18px;
    padding: 10px 16px;
    border-bottom: 1px solid var(--rule);
    flex-shrink: 0;
  }
  .queue-pro :global(.page-head h1) {
    margin: 0;
    font-family: var(--pro-sans);
    font-size: 14px; font-weight: 700;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--ink);
  }
  .queue-pro :global(.page-head .meta) {
    font-family: var(--pro-mono);
    font-size: 11px;
    color: var(--graphite);
    display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
  }
  .queue-pro :global(.page-head .meta b) { color: var(--ink); font-weight: 500; }
  .queue-pro :global(.page-head .meta .sep) { color: var(--ash); }
  .queue-pro :global(.page-head .meta .dim) { color: var(--ash); }

  .queue-pro :global(.page-head .actions) { display: flex; gap: 8px; }

  .queue-pro :global(.opbtn) {
    font-family: var(--pro-sans);
    font-weight: 700;
    font-size: 10px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    background: transparent;
    color: var(--ink);
    border: 1px solid var(--rule-2);
    padding: 5px 11px;
    cursor: pointer;
    height: 26px;
    line-height: 1;
  }
  .queue-pro :global(.opbtn:hover) { background: var(--paper-2); }
  .queue-pro :global(.opbtn:disabled) { opacity: 0.5; cursor: not-allowed; }
  .queue-pro :global(.opbtn.primary) { background: var(--ink); color: var(--paper); }
  .queue-pro :global(.opbtn.danger) { color: var(--abort); border-color: color-mix(in oklab, var(--abort) 50%, var(--rule-2)); }
  .queue-pro :global(.opbtn.danger:hover) { background: color-mix(in oklab, var(--abort) 8%, var(--paper-2)); }

  /* Stats bar */
  .queue-pro :global(.qbar) {
    display: grid;
    grid-template-columns: repeat(7, 1fr);
    border-bottom: 1px solid var(--rule);
  }
  .queue-pro :global(.qcell) {
    padding: 10px 14px;
    border-right: 1px solid var(--rule);
    display: flex; flex-direction: column; gap: 2px;
    min-width: 0;
  }
  .queue-pro :global(.qcell:last-child) { border-right: none; }
  .queue-pro :global(.qcell .k) {
    font-family: var(--pro-sans);
    font-size: 9px; font-weight: 700;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--graphite);
  }
  .queue-pro :global(.qcell .v) {
    font-family: var(--pro-mono);
    font-size: 18px; font-weight: 600;
    color: var(--ink);
    line-height: 1;
    font-variant-numeric: tabular-nums;
  }
  .queue-pro :global(.qcell .v.go)      { color: var(--go); }
  .queue-pro :global(.qcell .v.caution) { color: var(--caution); }
  .queue-pro :global(.qcell .v.abort)   { color: var(--abort); }
  .queue-pro :global(.qcell .v.nu)      { color: var(--ash); }
  .queue-pro :global(.qcell .sub) {
    font-family: var(--pro-mono);
    font-size: 9px;
    color: var(--ash);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* Kanban */
  .queue-pro :global(.kanban) {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    flex: 1;
    min-height: 320px;
  }
  .queue-pro :global(.kcol) {
    display: flex; flex-direction: column;
    min-height: 0; min-width: 0;
    border-right: 1px solid var(--rule);
  }
  .queue-pro :global(.kcol:last-child) { border-right: none; }

  .queue-pro :global(.kcol-head) {
    height: 32px;
    display: flex; align-items: center; justify-content: space-between;
    padding: 0 14px;
    background: var(--paper-2);
    border-bottom: 1px solid var(--rule);
    font-family: var(--pro-sans);
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.2em; text-transform: uppercase;
    color: var(--ash);
    flex-shrink: 0;
  }
  .queue-pro :global(.kcol-head .name) {
    display: inline-flex; align-items: center; gap: 8px;
    color: var(--ink);
  }
  .queue-pro :global(.kcol-head .name::before) {
    content: "";
    display: inline-block;
    width: 4px; height: 14px;
    background: var(--rule-2);
  }
  .queue-pro :global(.kcol-head.waiting .name::before) { background: var(--graphite); }
  .queue-pro :global(.kcol-head.active  .name::before) { background: var(--go); }
  .queue-pro :global(.kcol-head.review  .name::before) { background: var(--caution); }
  .queue-pro :global(.kcol-head.done    .name::before) { background: var(--graphite); }
  .queue-pro :global(.kcol-head.problem .name::before) { background: var(--abort); }
  .queue-pro :global(.kcol-head .count) {
    font-family: var(--pro-mono);
    color: var(--graphite);
    font-size: 11px; letter-spacing: 0;
  }

  .queue-pro :global(.kbody) {
    flex: 1;
    overflow-y: auto;
    padding: 10px;
    display: flex; flex-direction: column; gap: 8px;
  }
  .queue-pro :global(.kbody.empty) {
    align-items: center; justify-content: center;
    font-family: var(--pro-mono);
    font-size: 11px; color: var(--ash);
    font-style: italic;
    padding: 24px 14px;
  }

  /* Issue card */
  .queue-pro :global(.icard) {
    background: var(--paper-2);
    border: 1px solid var(--rule);
    padding: 10px 12px;
    display: grid; gap: 6px;
    cursor: pointer;
    font-family: var(--pro-mono);
    font-size: 11px;
    transition: border-color 160ms ease, background 160ms ease;
  }
  .queue-pro :global(.icard:hover) {
    border-color: var(--rule-2);
    background: var(--paper-3);
  }
  .queue-pro :global(.icard.live) { border-left: 3px solid var(--go); }
  .queue-pro :global(.icard.done) { border-left: 3px solid var(--graphite); opacity: 0.85; }
  .queue-pro :global(.icard.fail) { border-left: 3px solid var(--abort); }

  .queue-pro :global(.icard .num) {
    font-family: var(--pro-mono);
    font-size: 11px;
    color: var(--data, var(--graphite));
    font-weight: 600;
  }
  .queue-pro :global(.icard .title) {
    font-family: var(--pro-sans);
    font-size: 13px;
    line-height: 1.35;
    color: var(--ink);
    overflow: hidden;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }
  .queue-pro :global(.icard .title.nul) {
    color: var(--ash);
    font-style: italic;
  }
  .queue-pro :global(.icard .meta) {
    display: flex; gap: 6px; flex-wrap: wrap;
    color: var(--graphite);
    font-size: 10px;
    align-items: center;
  }
  .queue-pro :global(.icard .meta .repo) { color: var(--graphite); }
  .queue-pro :global(.icard .meta .pri.high) { color: var(--abort); }
  .queue-pro :global(.icard .meta .pri.med)  { color: var(--caution); }
  .queue-pro :global(.icard .meta .pri.low)  { color: var(--graphite); }
  .queue-pro :global(.icard .meta .retry)    { color: var(--caution); }

  .kanban-loading {
    padding: 32px 16px;
  }

  /* Slide panel content */
  .qpanel :global(.key-row) {
    display: grid;
    grid-template-columns: 100px 1fr;
    gap: 10px;
    padding: 6px 0;
    border-bottom: 1px dashed var(--rule);
    font-family: var(--pro-mono);
    font-size: 12px;
  }
  .qpanel :global(.key-row:last-of-type) { border-bottom: none; }
  .qpanel :global(.key-row label) {
    color: var(--ash);
    font-family: var(--pro-sans);
    font-size: 9px; font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase;
    align-self: center;
  }
  .qpanel :global(.key-row .val) { color: var(--ink); word-break: break-word; }
  .qpanel :global(.key-row .val.cap) { text-transform: capitalize; }
  .qpanel :global(.key-row .val.mono) { font-family: var(--pro-mono); }
  .qpanel :global(.key-row .val.dim) { color: var(--graphite); }
  .qpanel :global(.key-row .val .dim) { color: var(--ash); font-style: italic; margin-left: 8px; }
  .qpanel :global(.key-row .val.err) { color: var(--abort); font-size: 11px; }
  .qpanel :global(.key-row .val a) { color: var(--data, var(--ink)); text-decoration: none; }
  .qpanel :global(.key-row .val a:hover) { text-decoration: underline; }

  .qpanel :global(.actions) {
    display: flex; gap: 8px; flex-wrap: wrap;
    margin-top: 16px;
    padding-top: 12px;
    border-top: 1px solid var(--rule);
  }

  /* Tighter layout on narrow viewports — collapse the kanban to two
     columns so cards remain readable. The qbar drops to four cells
     which keeps Total / Active / Done / Problem visible — the most
     useful at-a-glance signals. */
  @media (max-width: 1180px) {
    .queue-pro :global(.kanban) {
      grid-template-columns: repeat(2, 1fr);
    }
    .queue-pro :global(.kcol) {
      border-bottom: 1px solid var(--rule);
    }
    .queue-pro :global(.qbar) {
      grid-template-columns: repeat(4, 1fr);
    }
  }
</style>
