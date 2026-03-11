<script lang="ts">
  import { WorkspaceRenderer } from '../lib/workspace-renderer';
  import type { RunPhase } from '../lib/workspace-renderer';
  import type { Project, Run, SystemStatus, UsageData } from '../lib/types';
  import { liveActivity } from '../lib/live-activity.svelte';

  interface Props {
    projects: Project[];
    runs: Run[];
    latestRun: Run | null;
    systemStatus: SystemStatus | null;
    usage: UsageData | null;
    activityIntensity?: number;
    currentToolSummary?: string | null;
    overridePhase?: RunPhase | null;
  }

  let { projects, runs, latestRun, systemStatus, usage, activityIntensity = 0, currentToolSummary = null, overridePhase = null }: Props = $props();

  let canvas: HTMLCanvasElement;
  let container: HTMLDivElement;
  let renderer: WorkspaceRenderer | null = null;
  let tooltip = $state<{ text: string; x: number; y: number } | null>(null);
  let containerW = $state(300);
  let containerH = $state(300);

  let activeRunProjectIds = $derived(() => {
    const ids = new Set<number>();
    for (const r of runs) {
      if ((r.status === 'running' || r.status === 'reviewing') && r.project_id) ids.add(r.project_id);
    }
    return ids;
  });

  let activeProjectModes = $derived(() => {
    const modes = new Map<number, string>();
    for (const r of runs) {
      if ((r.status === 'running' || r.status === 'reviewing') && r.project_id && r.mode) {
        modes.set(r.project_id, r.mode);
      }
    }
    return modes;
  });

  let runPhase = $derived((): RunPhase => {
    // Use parent-provided phase if available (has coordinator context)
    if (overridePhase) return overridePhase;
    const activeRuns = runs.filter(r => r.status === 'running' || r.status === 'reviewing');
    if (activeRuns.length === 0) return 'idle';
    const hasReviewing = activeRuns.some(r => r.status === 'reviewing');
    const hasManager = activeRuns.some(r => r.mode === 'manager');
    const hasVerdict = activeRuns.some(r => r.verdict != null);
    if (hasVerdict) return 'executing_verdict';
    if (hasReviewing || hasManager) return 'manager_review';
    return 'employee';
  });

  $effect(() => {
    if (!canvas || !container) return;

    renderer = new WorkspaceRenderer(canvas);
    const rect = container.getBoundingClientRect();
    containerW = rect.width;
    containerH = rect.height;
    renderer.resize(rect.width, rect.height);
    renderer.start();

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width > 0 && height > 0) {
          containerW = width;
          containerH = height;
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

  $effect(() => {
    renderer?.setActivity({
      intensity: activityIntensity,
      currentTool: currentToolSummary ?? null,
      activeAgent: runPhase() === 'idle' ? null : runPhase() === 'manager_review' ? 'manager' : 'employee',
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

  // HUD state
  let phase = $derived(runPhase());
  let isActive = $derived(phase !== 'idle');
  let phaseLabel = $derived(
    phase === 'coordinating' ? 'COORDINATOR ACTIVE'
    : phase === 'employee' ? 'EMPLOYEES WORKING'
    : phase === 'manager_review' ? 'MANAGER REVIEW'
    : phase === 'executing_verdict' ? 'EXECUTING VERDICT'
    : 'STANDBY'
  );
  let phaseColorClass = $derived(
    phase === 'coordinating' ? 'text-accent-purple'
    : phase === 'employee' ? 'text-accent-blue'
    : phase === 'manager_review' ? 'text-warning'
    : phase === 'executing_verdict' ? 'text-approve'
    : 'text-text-dim'
  );

  // Connection status indicator
  let wsConnected = $derived(liveActivity.connected);

  // Running projects for per-project HUD
  let runningProjects = $derived(() => {
    const result: { name: string; mode: string; angle: number; x: number; y: number }[] = [];
    const count = projects.length;
    if (count === 0) return result;
    const orbitR = Math.min(containerW, containerH) * 0.32;
    const cx = containerW / 2;
    const cy = containerH / 2;

    for (let i = 0; i < projects.length; i++) {
      const p = projects[i];
      const runForProject = runs.find(r => (r.status === 'running' || r.status === 'reviewing') && r.project_id === p.id);
      if (!runForProject) continue;

      const angle = (i / count) * Math.PI * 2 - Math.PI / 2;
      result.push({
        name: p.repo.split('/').pop() ?? p.repo,
        mode: runForProject.mode ?? 'employee',
        angle,
        x: cx + Math.cos(angle) * (orbitR + 42),
        y: cy + Math.sin(angle) * (orbitR + 42),
      });
    }
    return result;
  });
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
      class="absolute pointer-events-none px-2 py-1 rounded text-xs glass text-text-dim whitespace-nowrap z-20"
      style="left: {tooltip.x}px; top: {tooltip.y}px; transform: translateX(-50%)"
    >
      {tooltip.text}
    </div>
  {/if}

  <!-- HUD: Top-left corner bracket + status -->
  <div class="absolute top-2 left-2 pointer-events-none z-10">
    <div class="hud-bracket-tl">
      <div class="text-[9px] font-data tracking-widest {phaseColorClass} pl-1.5 pt-0.5 {isActive ? 'animate-pulse-glow' : ''}">
        {phaseLabel}
      </div>
      <div class="text-[8px] font-data text-text-dim pl-1.5 mt-0.5 opacity-60">
        CLAUDE AGENT STATION
      </div>
    </div>
  </div>

  <!-- HUD: Top-right corner bracket + connection -->
  <div class="absolute top-2 right-2 pointer-events-none z-10">
    <div class="hud-bracket-tr text-right">
      <div class="flex items-center justify-end gap-1 pr-1.5 pt-0.5">
        <span class="text-[8px] font-data tracking-wider {wsConnected ? 'text-approve' : 'text-reject'}">
          {wsConnected ? 'STREAM ACTIVE' : 'STREAM OFFLINE'}
        </span>
        <div class="w-1.5 h-1.5 rounded-full {wsConnected ? 'bg-approve animate-pulse-glow' : 'bg-reject'}"></div>
      </div>
      {#if liveActivity.turnCount > 0}
        <div class="text-[8px] font-data text-text-dim pr-1.5 mt-0.5 opacity-60">
          TURNS: {liveActivity.turnCount}
        </div>
      {/if}
    </div>
  </div>

  <!-- HUD: Bottom-left corner bracket -->
  <div class="absolute bottom-2 left-2 pointer-events-none z-10">
    <div class="hud-bracket-bl">
      {#if liveActivity.currentTool}
        <div class="text-[8px] font-data text-accent-cyan pl-1.5 pb-0.5 truncate max-w-[180px]">
          {liveActivity.currentTool.name}
        </div>
      {/if}
      <div class="text-[8px] font-data text-text-dim pl-1.5 pb-0.5 opacity-70">
        {projects.length} PROJECT{projects.length !== 1 ? 'S' : ''} REGISTERED
      </div>
    </div>
  </div>

  <!-- HUD: Bottom-right corner bracket -->
  <div class="absolute bottom-2 right-2 pointer-events-none z-10">
    <div class="hud-bracket-br text-right">
      {#if usage}
        <div class="text-[8px] font-data text-text-dim pr-1.5 pb-0.5 opacity-60">
          USAGE: {Math.round(usage.usage_percent)}%
        </div>
      {/if}
    </div>
  </div>

  <!-- Node labels + per-project interaction badges -->
  {#each projects as project, i}
    {@const count = projects.length}
    {@const angle = (i / count) * Math.PI * 2 - Math.PI / 2}
    {@const labelRadius = Math.min(containerW, containerH) * 0.32 + 30}
    {@const lx = containerW / 2 + Math.cos(angle) * labelRadius}
    {@const ly = containerH / 2 + Math.sin(angle) * labelRadius}
    {@const activeRun = runs.find(r => (r.status === 'running' || r.status === 'reviewing') && r.project_id === project.id)}
    {@const isRunning = !!activeRun}
    {@const modeLabel = activeRun?.mode === 'analyst' ? 'ANALYST' : activeRun?.mode === 'manager' ? 'MANAGER' : 'EMPLOYEE'}
    {@const modeColor = activeRun?.mode === 'analyst' ? 'text-pr' : activeRun?.mode === 'manager' ? 'text-warning' : 'text-accent-blue'}

    <div
      class="absolute pointer-events-none z-10 flex flex-col items-center gap-0.5"
      style="left: {lx}px; top: {ly}px; transform: translate(-50%, -50%)"
    >
      <!-- Project name -->
      <span class="text-[10px] whitespace-nowrap {isRunning ? 'text-text font-medium' : 'text-text-dim'}">
        {project.repo.split('/').pop() ?? project.repo}
      </span>

      <!-- Active role badge -->
      {#if isRunning}
        <span class="text-[8px] font-data tracking-wider {modeColor} animate-pulse-glow whitespace-nowrap">
          {modeLabel}
        </span>
      {/if}
    </div>
  {/each}
</div>

<style>
  /* HUD corner brackets — JARVIS-style */
  .hud-bracket-tl {
    border-left: 1px solid rgba(59, 130, 246, 0.35);
    border-top: 1px solid rgba(59, 130, 246, 0.35);
    padding: 2px 6px 4px 0;
    min-width: 80px;
  }
  .hud-bracket-tr {
    border-right: 1px solid rgba(59, 130, 246, 0.35);
    border-top: 1px solid rgba(59, 130, 246, 0.35);
    padding: 2px 0 4px 6px;
    min-width: 80px;
  }
  .hud-bracket-bl {
    border-left: 1px solid rgba(59, 130, 246, 0.25);
    border-bottom: 1px solid rgba(59, 130, 246, 0.25);
    padding: 4px 6px 2px 0;
    min-width: 80px;
  }
  .hud-bracket-br {
    border-right: 1px solid rgba(59, 130, 246, 0.25);
    border-bottom: 1px solid rgba(59, 130, 246, 0.25);
    padding: 4px 0 2px 6px;
    min-width: 60px;
  }
</style>
