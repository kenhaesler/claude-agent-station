<script lang="ts">
  import type { Run } from '../lib/types';
  import { getRun } from '../lib/api';
  import { formatDuration, formatCost, formatDate } from '../lib/format';
  import { toastError } from '../lib/toast.svelte';
  import StatusBadge from '../components/StatusBadge.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';
  import VerdictDetail from '../components/VerdictDetail.svelte';

  interface Props { runId: string; }
  let { runId }: Props = $props();

  let run = $state<Run | null>(null);
  let loading = $state(true);

  async function load(id: string) {
    loading = true;
    try {
      run = await getRun(id);
    } catch (e: any) {
      toastError(e.message);
    } finally {
      loading = false;
    }
  }

  $effect(() => { load(runId); });
</script>

<div class="space-y-6">
  <div class="flex items-center gap-3 min-w-0">
    <a href="#/runs" class="text-text-dim hover:text-text shrink-0">&larr; Runs</a>
    <h1 class="text-lg md:text-2xl font-bold font-mono truncate">{runId}</h1>
  </div>

  {#if loading}
    <div class="flex justify-center py-12"><LoadingSpinner /></div>
  {:else if run}
    <!-- Metadata Grid -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div class="bg-surface rounded-xl p-4 border border-border">
        <span class="text-xs text-text-dim">Verdict</span>
        <div class="mt-1"><StatusBadge value={run.verdict} /></div>
      </div>
      <div class="bg-surface rounded-xl p-4 border border-border">
        <span class="text-xs text-text-dim">Status</span>
        <div class="mt-1"><StatusBadge value={run.status} variant="status" /></div>
      </div>
      <div class="bg-surface rounded-xl p-4 border border-border">
        <span class="text-xs text-text-dim">Duration</span>
        <p class="mt-1 font-medium">{formatDuration(run.duration_ms)}</p>
      </div>
      <div class="bg-surface rounded-xl p-4 border border-border">
        <span class="text-xs text-text-dim">Cost</span>
        <p class="mt-1 font-medium">{formatCost(run.cost_usd)}</p>
      </div>
    </div>

    <!-- Details Table -->
    <div class="bg-surface rounded-xl border border-border overflow-hidden overflow-x-auto">
      <table class="w-full text-sm min-w-[320px]">
        <tbody class="divide-y divide-border">
          <tr>
            <td class="px-5 py-3 text-text-dim w-40">Mode</td>
            <td class="px-5 py-3">{run.mode ?? '-'}</td>
          </tr>
          <tr>
            <td class="px-5 py-3 text-text-dim">Model</td>
            <td class="px-5 py-3 font-mono text-xs">{run.model ?? '-'}</td>
          </tr>
          <tr>
            <td class="px-5 py-3 text-text-dim">Branch</td>
            <td class="px-5 py-3">{run.branch ?? '-'}</td>
          </tr>
          <tr>
            <td class="px-5 py-3 text-text-dim">Issue</td>
            <td class="px-5 py-3">{run.issue_number ?? '-'}</td>
          </tr>
          <tr>
            <td class="px-5 py-3 text-text-dim">Turns</td>
            <td class="px-5 py-3">{run.turns ?? '-'}</td>
          </tr>
          <tr>
            <td class="px-5 py-3 text-text-dim">Started</td>
            <td class="px-5 py-3">{formatDate(run.started_at)}</td>
          </tr>
          <tr>
            <td class="px-5 py-3 text-text-dim">Finished</td>
            <td class="px-5 py-3">{formatDate(run.finished_at)}</td>
          </tr>
          {#if run.log_file}
            <tr>
              <td class="px-5 py-3 text-text-dim">Log File</td>
              <td class="px-5 py-3 font-mono text-xs">{run.log_file}</td>
            </tr>
          {/if}
        </tbody>
      </table>
    </div>

    <!-- Employee Report -->
    {#if run.employee_report}
      <div class="bg-surface rounded-xl border border-border p-5">
        <h2 class="font-semibold mb-3">Employee Report</h2>
        <pre class="text-sm whitespace-pre-wrap text-text-dim">{run.employee_report}</pre>
      </div>
    {/if}

    <!-- Verdict Detail -->
    <div class="bg-surface rounded-xl border border-border p-5">
      <h2 class="font-semibold mb-3">Verdict Detail</h2>
      <VerdictDetail detail={run.verdict_detail} />
    </div>

    <!-- Log Link -->
    <div class="flex gap-3">
      <a href="#/logs?run={run.run_id}" class="px-4 py-2 bg-surface-2 rounded-lg text-sm hover:bg-border transition-colors">
        View Logs
      </a>
    </div>
  {:else}
    <p class="text-text-dim">Run not found</p>
  {/if}
</div>
