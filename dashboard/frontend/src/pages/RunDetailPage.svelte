<script lang="ts">
  import type { Run } from '../lib/types';
  import { getRun } from '../lib/api';
  import { formatDuration, formatTokens, formatDate } from '../lib/format';
  import { toastError } from '../lib/toast.svelte';
  import StatusBadge from '../components/StatusBadge.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';
  import VerdictDetail from '../components/VerdictDetail.svelte';
  import EmployeeReport from '../components/EmployeeReport.svelte';
  import GlassCard from '../components/GlassCard.svelte';

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

<div class="space-y-6 animate-fade-in-up">
  <div class="flex items-center gap-3 min-w-0">
    <a href="#/runs" class="text-text-dim hover:text-text shrink-0">&larr; Runs</a>
    <h1 class="text-lg md:text-2xl font-bold font-data truncate">{runId}</h1>
  </div>

  {#if loading}
    <div class="flex justify-center py-12"><LoadingSpinner /></div>
  {:else if run}
    <!-- Metadata Grid -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4">
      <GlassCard glow={run.verdict === 'APPROVE' ? 'emerald' : run.verdict === 'REJECT' ? 'red' : 'purple'} class="p-4">
        <span class="text-xs text-text-dim">Verdict</span>
        <div class="mt-1"><StatusBadge value={run.verdict} /></div>
      </GlassCard>
      <GlassCard glow="blue" class="p-4">
        <span class="text-xs text-text-dim">Status</span>
        <div class="mt-1"><StatusBadge value={run.status} variant="status" /></div>
      </GlassCard>
      <GlassCard class="p-4">
        <span class="text-xs text-text-dim">Duration</span>
        <p class="mt-1 font-medium">{formatDuration(run.duration_ms)}</p>
      </GlassCard>
      <GlassCard class="p-4">
        <span class="text-xs text-text-dim">Tokens</span>
        <p class="mt-1 font-medium font-data">{formatTokens(run.tokens_total)}</p>
      </GlassCard>
    </div>

    <!-- Details Table -->
    <GlassCard class="overflow-hidden overflow-x-auto">
      <table class="w-full text-sm min-w-[320px]">
        <tbody class="divide-y divide-border/30">
          <tr>
            <td class="px-5 py-3 text-text-dim w-40">Mode</td>
            <td class="px-5 py-3">{run.mode ?? '-'}</td>
          </tr>
          <tr>
            <td class="px-5 py-3 text-text-dim">Model</td>
            <td class="px-5 py-3 font-data text-xs">{run.model ?? '-'}</td>
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
            <td class="px-5 py-3 text-text-dim">Tokens (Input)</td>
            <td class="px-5 py-3 font-data">{formatTokens(run.tokens_input)}</td>
          </tr>
          <tr>
            <td class="px-5 py-3 text-text-dim">Tokens (Output)</td>
            <td class="px-5 py-3 font-data">{formatTokens(run.tokens_output)}</td>
          </tr>
          <tr>
            <td class="px-5 py-3 text-text-dim">Tokens (Total)</td>
            <td class="px-5 py-3 font-data">{formatTokens(run.tokens_total)}</td>
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
              <td class="px-5 py-3 font-data text-xs">{run.log_file}</td>
            </tr>
          {/if}
        </tbody>
      </table>
    </GlassCard>

    <!-- Employee Report -->
    <GlassCard class="p-5">
      <h2 class="font-semibold mb-3">Employee Report</h2>
      <EmployeeReport report={run.employee_report} />
    </GlassCard>

    <!-- Verdict Detail -->
    <GlassCard class="p-5">
      <h2 class="font-semibold mb-3">Verdict Detail</h2>
      <VerdictDetail detail={run.verdict_detail} />
    </GlassCard>

    <!-- Log Link -->
    <div class="flex gap-3">
      <a href="#/logs?run={run.run_id}" class="px-4 py-2 glass rounded-lg text-sm hover:bg-white/[0.03] transition-colors">
        View Logs
      </a>
    </div>
  {:else}
    <p class="text-text-dim">Run not found</p>
  {/if}
</div>
