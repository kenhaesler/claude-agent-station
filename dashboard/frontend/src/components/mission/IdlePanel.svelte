<script lang="ts">
  import { triggerRun } from '../../lib/api';
  import { addToast } from '../../lib/toast.svelte';
  import { navigate } from '../../lib/router.svelte';
  import { timeAgo } from '../../lib/format';
  import type { Run } from '../../lib/types';

  let { lastRun }: { lastRun: Run | null } = $props();

  let triggering = $state(false);

  async function handleTrigger() {
    if (triggering) return;
    triggering = true;
    try {
      const res = await triggerRun();
      addToast('success', `Triggered run ${res.run_id ?? ''}`);
    } catch (e: any) {
      addToast('error', e.message ?? 'Trigger failed');
    } finally {
      triggering = false;
    }
  }

  function viewLast() {
    if (lastRun) navigate(`/runs/${lastRun.run_id}`);
  }
</script>

<section class="idle-panel" data-testid="mission-idle-panel">
  <div class="idle-head">
    <h2>● Agent is idle</h2>
    <button type="button"
            class="trigger-btn primary"
            onclick={handleTrigger}
            disabled={triggering}
            data-testid="idle-trigger-btn">
      {triggering ? 'Triggering…' : 'Trigger Run'}
    </button>
  </div>

  {#if lastRun}
    <div class="last-run">
      <div class="row">
        <span class="lbl">Last run</span>
        <a href={`/runs/${lastRun.run_id}`} onclick={(e) => { e.preventDefault(); viewLast(); }}>
          {lastRun.run_id}
        </a>
      </div>
      <div class="row">
        <span class="lbl">Status</span>
        <span class="val">{lastRun.status}{lastRun.verdict ? ` · ${lastRun.verdict}` : ''}</span>
      </div>
      {#if lastRun.finished_at}
        <div class="row">
          <span class="lbl">Finished</span>
          <span class="val">{timeAgo(lastRun.finished_at)}</span>
        </div>
      {/if}
    </div>
  {:else}
    <p class="desc">No runs yet. Click <b>Trigger Run</b> to start the agent.</p>
  {/if}
</section>

<style>
  .idle-panel {
    border: 1px dashed var(--graphite);
    background: var(--paper-2);
    padding: 32px;
    border-radius: 8px;
    margin: 24px 0;
    color: var(--ink);
  }
  .idle-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    margin-bottom: 16px;
  }
  .idle-head h2 {
    margin: 0;
    color: var(--ash);
    font-size: 1.1rem;
    font-weight: 500;
  }
  .trigger-btn {
    padding: 8px 24px;
    background: var(--data);
    color: var(--paper);
    border: 0;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.95rem;
  }
  .trigger-btn:disabled { opacity: 0.6; cursor: not-allowed; }
  .last-run .row { display: flex; gap: 12px; margin: 4px 0; }
  .last-run .lbl { color: var(--graphite); min-width: 90px; }
  .last-run a { color: var(--data); text-decoration: none; }
  .desc { color: var(--graphite); margin: 8px 0 0; }
</style>
