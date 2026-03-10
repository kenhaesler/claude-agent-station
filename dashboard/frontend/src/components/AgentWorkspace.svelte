<script lang="ts">
  import { WorkspaceRenderer } from '../lib/workspace-renderer';
  import type { RunPhase } from '../lib/workspace-renderer';
  import type { Project, Run, SystemStatus, UsageData } from '../lib/types';

  interface Props {
    projects: Project[];
    runs: Run[];
    latestRun: Run | null;
    systemStatus: SystemStatus | null;
    usage: UsageData | null;
  }

  let { projects, runs, latestRun, systemStatus, usage }: Props = $props();

  let canvas: HTMLCanvasElement;
  let container: HTMLDivElement;
  let renderer: WorkspaceRenderer | null = null;
  let tooltip = $state<{ text: string; x: number; y: number } | null>(null);

  let activeRunProjectIds = $derived(() => {
    const ids = new Set<number>();
    for (const r of runs) {
      if (r.status === 'running' && r.project_id) ids.add(r.project_id);
    }
    return ids;
  });

  let activeProjectModes = $derived(() => {
    const modes = new Map<number, string>();
    for (const r of runs) {
      if (r.status === 'running' && r.project_id && r.mode) {
        modes.set(r.project_id, r.mode);
      }
    }
    return modes;
  });

  let runPhase = $derived((): RunPhase => {
    const runningRuns = runs.filter(r => r.status === 'running');
    if (runningRuns.length === 0) return 'idle';

    // Check if any running run is in manager/verdict mode
    const hasManager = runningRuns.some(r => r.mode === 'manager');
    const hasVerdict = runningRuns.some(r => r.verdict != null);

    if (hasVerdict) return 'executing_verdict';
    if (hasManager) return 'manager_review';
    return 'employee';
  });

  $effect(() => {
    if (!canvas || !container) return;

    renderer = new WorkspaceRenderer(canvas);
    const rect = container.getBoundingClientRect();
    renderer.resize(rect.width, rect.height);
    renderer.start();

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width > 0 && height > 0) {
          renderer?.resize(width, height);
        }
      }
    });
    ro.observe(container);

    return () => {
      renderer?.stop();
      renderer = null;
      ro.disconnect();
    };
  });

  $effect(() => {
    renderer?.setData({
      projects: projects.map(p => ({
        id: p.id,
        repo: p.repo,
        priority: p.priority,
        enabled: p.enabled,
      })),
      activeRunProjectIds: activeRunProjectIds(),
      activeProjectModes: activeProjectModes(),
      runPhase: runPhase(),
      serviceActive: systemStatus?.service.active ?? false,
      usagePercent: usage?.usage_percent ?? 0,
    });
  });

  function handleMouseMove(e: MouseEvent) {
    if (!renderer) return;
    const node = renderer.getNodeAt(e.clientX, e.clientY);
    if (node) {
      const rect = container.getBoundingClientRect();
      tooltip = {
        text: node.repo.split('/').pop() || node.repo,
        x: e.clientX - rect.left,
        y: e.clientY - rect.top - 28,
      };
    } else if (renderer.isHubAt(e.clientX, e.clientY)) {
      const rect = container.getBoundingClientRect();
      const phase = runPhase();
      const phaseText = phase === 'employee' ? 'Employees working'
        : phase === 'manager_review' ? 'Manager reviewing'
        : phase === 'executing_verdict' ? 'Executing verdict'
        : 'Idle';
      tooltip = {
        text: systemStatus?.service.active ? `Manager - ${phaseText}` : 'Manager - Offline',
        x: e.clientX - rect.left,
        y: e.clientY - rect.top - 28,
      };
    } else {
      tooltip = null;
    }
  }

  function handleMouseLeave() {
    tooltip = null;
  }

  function handleClick(e: MouseEvent) {
    if (!renderer) return;
    const node = renderer.getNodeAt(e.clientX, e.clientY);
    if (node) {
      window.location.hash = '/projects';
    } else if (renderer.isHubAt(e.clientX, e.clientY)) {
      window.location.hash = '/system';
    }
  }
</script>

<div
  bind:this={container}
  class="relative w-full h-full"
  role="img"
  aria-label="Agent workspace visualization"
>
  <canvas
    bind:this={canvas}
    class="w-full h-full cursor-pointer"
    onmousemove={handleMouseMove}
    onmouseleave={handleMouseLeave}
    onclick={handleClick}
  ></canvas>

  {#if tooltip}
    <div
      class="absolute pointer-events-none px-2 py-1 rounded text-xs glass text-text-dim whitespace-nowrap"
      style="left: {tooltip.x}px; top: {tooltip.y}px; transform: translateX(-50%)"
    >
      {tooltip.text}
    </div>
  {/if}

  <!-- Node labels -->
  {#each projects as project, i}
    {@const count = projects.length}
    {@const angle = (i / count) * Math.PI * 2 - Math.PI / 2}
    {@const labelRadius = Math.min(container?.clientWidth ?? 300, container?.clientHeight ?? 300) * 0.32 + 28}
    {@const lx = (container?.clientWidth ?? 300) / 2 + Math.cos(angle) * labelRadius}
    {@const ly = (container?.clientHeight ?? 300) / 2 + Math.sin(angle) * labelRadius}
    <span
      class="absolute text-[10px] text-text-dim pointer-events-none whitespace-nowrap"
      style="left: {lx}px; top: {ly}px; transform: translate(-50%, -50%)"
    >
      {project.repo.split('/').pop() ?? project.repo}
    </span>
  {/each}
</div>
