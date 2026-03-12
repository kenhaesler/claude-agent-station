<script lang="ts">
  import type { Run } from '../lib/types';
  import { getLatestRun, listProjects } from '../lib/api';
  import type { RunPhase } from '../lib/workspace-renderer';

  let activeRun = $state<Run | null>(null);
  let projectName = $state<string | null>(null);
  let elapsed = $state(0);

  let phase = $derived((): RunPhase => {
    if (!activeRun) return 'idle';
    if (activeRun.status === 'reviewing') return 'manager_review';
    if (activeRun.status === 'running') return 'employee';
    return 'idle';
  });

  let isActive = $derived(phase() !== 'idle');

  let phaseLabel = $derived(() => {
    switch (phase()) {
      case 'coordinating': return 'Coordinating';
      case 'employee': return 'Employees Working';
      case 'manager_review': return 'Manager Review';
      case 'executing_verdict': return 'Executing Verdict';
      default: return '';
    }
  });

  let phaseColor = $derived(() => {
    switch (phase()) {
      case 'coordinating': return 'rgb(168, 85, 247)';
      case 'employee': return 'rgb(59, 130, 246)';
      case 'manager_review': return 'rgb(245, 158, 11)';
      case 'executing_verdict': return 'rgb(16, 185, 129)';
      default: return 'rgb(6, 182, 212)';
    }
  });

  function formatElapsed(s: number): string {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    if (m > 0) return `${m}m ${sec}s`;
    return `${sec}s`;
  }

  async function poll() {
    try {
      const run = await getLatestRun();
      if (run && (run.status === 'running' || run.status === 'reviewing')) {
        activeRun = run;
        // Resolve project name
        if (run.project_id && !projectName) {
          try {
            const projects = await listProjects();
            const proj = projects.find(p => p.id === run.project_id);
            if (proj) projectName = proj.repo;
          } catch { /* ignore */ }
        }
      } else {
        activeRun = null;
        projectName = null;
      }
    } catch { /* ignore */ }
  }

  // Elapsed timer
  $effect(() => {
    if (!activeRun?.started_at) { elapsed = 0; return; }
    const start = new Date(activeRun.started_at).getTime();
    elapsed = Math.max(0, Math.floor((Date.now() - start) / 1000));
    const interval = setInterval(() => {
      elapsed = Math.max(0, Math.floor((Date.now() - start) / 1000));
    }, 1000);
    return () => clearInterval(interval);
  });

  // Poll for active run
  $effect(() => {
    poll();
    const interval = setInterval(poll, 8000);
    return () => clearInterval(interval);
  });
</script>

{#if isActive && activeRun}
  <a
    href="#/runs/{activeRun.run_id}"
    class="active-run-banner flex items-center gap-2 px-3 py-1 rounded-md text-xs transition-all
      hover:bg-white/[0.06] no-underline cursor-pointer group"
    style="border: 1px solid {phaseColor()}30; background: {phaseColor()}08;"
  >
    <!-- Pulsing dot -->
    <span class="relative flex h-2 w-2 shrink-0">
      <span class="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style="background: {phaseColor()};"></span>
      <span class="relative inline-flex rounded-full h-2 w-2" style="background: {phaseColor()};"></span>
    </span>

    <!-- Phase label -->
    <span class="font-medium hidden md:inline" style="color: {phaseColor()};">{phaseLabel()}</span>

    <!-- Project name -->
    {#if projectName}
      <span class="text-text-dim hidden lg:inline">{projectName.split('/').pop()}</span>
    {/if}

    <!-- Elapsed time -->
    <span class="font-data tabular-nums text-text-dim">{formatElapsed(elapsed)}</span>

    <!-- Arrow indicator -->
    <span class="text-text-dim group-hover:text-text transition-colors">&rarr;</span>
  </a>
{/if}
