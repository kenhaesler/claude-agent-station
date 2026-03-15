<script lang="ts">
  import { WorkspaceRenderer } from '../lib/workspace-renderer';
  import { agentPresence, togglePanel } from '../lib/agent-presence.svelte';
  import { navigate } from '../lib/router.svelte';
  import type { SystemStatus, UsageData } from '../lib/types';

  interface Props {
    systemStatus?: SystemStatus | null;
    usage?: UsageData | null;
    /** Render quality: 'full' for all layers, 'ambient' for lightweight backdrop */
    renderQuality?: 'full' | 'ambient';
    /** Whether canvas handles pointer events (click/hover) */
    interactive?: boolean;
    /** Canvas opacity (0-1) */
    opacity?: number;
  }

  let { systemStatus = null, usage = null, renderQuality = 'full', interactive = true, opacity = 1 }: Props = $props();

  let canvas: HTMLCanvasElement;
  let container: HTMLDivElement;
  let renderer: WorkspaceRenderer | null = null;
  let tooltip = $state<{ text: string; x: number; y: number } | null>(null);

  // HUD tool cycling state
  let hudToolCycleIndex = $state(0);
  let hudToolCycleTimer: ReturnType<typeof setInterval> | null = null;

  // Initialize renderer
  $effect(() => {
    if (!canvas || !container) return;

    renderer = new WorkspaceRenderer(canvas);
    const rect = container.getBoundingClientRect();
    renderer.resize(rect.width, rect.height);
    renderer.start();

    // Sound event hook architecture — custom event for Web Audio integration
    const unsubSound = renderer.onSoundEvent((event) => {
      // Dispatch custom DOM event for Web Audio API consumers
      container.dispatchEvent(new CustomEvent('workspace-sound', {
        detail: event,
        bubbles: true,
      }));
    });

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width > 0 && height > 0) {
          renderer?.resize(width, height);
        }
      }
    });
    ro.observe(container);

    // HUD tool cycling timer (every 3s)
    hudToolCycleTimer = setInterval(() => {
      hudToolCycleIndex++;
    }, 3000);

    return () => {
      renderer?.stop();
      unsubSound();
      renderer = null;
      ro.disconnect();
      if (hudToolCycleTimer) { clearInterval(hudToolCycleTimer); hudToolCycleTimer = null; }
    };
  });

  // Sync render quality
  $effect(() => {
    renderer?.setRenderQuality(renderQuality);
  });

  // Feed agent-presence data to renderer (with employee data)
  $effect(() => {
    renderer?.setData({
      agents: agentPresence.agents.map(a => ({
        id: a.name,
        role: a.role,
        name: a.name,
        color: a.color,
        isActive: a.status === 'active' || a.status === 'thinking',
        isThinking: a.status === 'thinking',
        currentAction: a.currentAction,
        turnCount: agentPresence.turnCount,
        tokenCount: agentPresence.tokensBurned,
        employees: agentPresence.activeRuns
          .filter(r => r.mode !== 'manager' && r.mode !== 'analyst')
          .map((r, i) => ({
            index: i,
            runId: r.run_id,
            status: r.status === 'running' ? 'working' as const : 'waiting' as const,
            tool: null,
            issueNumber: r.issue_number,
            inWorktree: false, // TODO: populate from backend when available
            tokensUsed: 0,
            turnsUsed: r.turns ?? 0,
          })),
      })),
      phase: agentPresence.phase,
      serviceActive: systemStatus?.service.active ?? false,
      usagePercent: usage?.usage_percent ?? 0,
      activityIntensity: agentPresence.activityIntensity,
    });
  });

  $effect(() => {
    renderer?.setActivity({
      intensity: agentPresence.activityIntensity,
      currentTool: agentPresence.currentTool?.summary ?? null,
      activeAgent: agentPresence.agents.find(a => a.status === 'active')?.name ?? null,
    });
  });

  // Trigger visual events from conversation log changes
  let lastLogLen = 0;
  $effect(() => {
    const log = agentPresence.conversationLog;
    if (log.length > lastLogLen && renderer) {
      for (let i = lastLogLen; i < log.length; i++) {
        const entry = log[i];
        if (entry.type === 'tool_use') {
          renderer.triggerEvent('tool_use', { agentId: entry.agentName, toolName: entry.toolName });
        } else if (entry.type === 'thinking') {
          renderer.triggerEvent('thinking_start', { agentId: entry.agentName });
        } else if (entry.type === 'guidance') {
          renderer.triggerEvent('guidance_sent', { agentId: entry.agentName });
        } else if (entry.type === 'phase' && entry.content.startsWith('Verdict')) {
          renderer.triggerEvent('verdict', { verdict: entry.content });
        } else if (entry.type === 'phase' && entry.content.includes('started')) {
          renderer.triggerEvent('run_start');
        } else if (entry.type === 'phase' && entry.content.includes('completed')) {
          renderer.triggerEvent('run_complete');
        } else if (entry.type === 'system' && entry.content.includes('REAPER')) {
          // Parse reaper count from message
          const match = entry.content.match(/(\d+)/);
          const count = match ? parseInt(match[1]) : 1;
          renderer.triggerEvent('reaper_sweep', { reaperCount: count });
        }
      }
    }
    lastLogLen = log.length;
  });

  function handleMouseMove(e: MouseEvent) {
    if (!renderer) return;
    // Update parallax
    const rect = container.getBoundingClientRect();
    renderer.setMousePosition(e.clientX - rect.left, e.clientY - rect.top);

    // Check employee orbitals first (more specific)
    const empTooltip = renderer.getEmployeeTooltip(e.clientX, e.clientY);
    if (empTooltip) {
      tooltip = {
        text: empTooltip,
        x: e.clientX - rect.left,
        y: e.clientY - rect.top - 28,
      };
      return;
    }

    const node = renderer.getNodeAt(e.clientX, e.clientY);
    if (node) {
      tooltip = {
        text: `${node.name} -- ${node.isActive ? 'Active' : node.isThinking ? 'Thinking' : 'Idle'}`,
        x: e.clientX - rect.left,
        y: e.clientY - rect.top - 28,
      };
    } else if (renderer.isHubAt(e.clientX, e.clientY)) {
      tooltip = {
        text: `Station -- ${agentPresence.phase === 'idle' ? 'Idle' : agentPresence.phase.replace('_', ' ')}`,
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

    // Check employee orbital click -> navigate to run detail
    const emp = renderer.getEmployeeAt(e.clientX, e.clientY);
    if (emp) {
      // Navigate to run detail page
      navigate(`/stream/${emp.runId}`);
      return;
    }

    const node = renderer.getNodeAt(e.clientX, e.clientY);
    if (node) {
      togglePanel(node.name);
    } else if (renderer.isHubAt(e.clientX, e.clientY)) {
      togglePanel();
    }
  }

  let phaseLabel = $derived(
    agentPresence.phase === 'coordinating' ? 'Coordinating'
    : agentPresence.phase === 'employee' ? 'Working'
    : agentPresence.phase === 'manager_review' ? 'Reviewing'
    : agentPresence.phase === 'executing_verdict' ? 'Verdict'
    : 'Standby'
  );

  let phaseColorClass = $derived(
    agentPresence.phase === 'coordinating' ? 'text-accent-purple'
    : agentPresence.phase === 'employee' ? 'text-info'
    : agentPresence.phase === 'manager_review' ? 'text-warning'
    : agentPresence.phase === 'executing_verdict' ? 'text-approve'
    : 'text-text-dim'
  );

  // Active employee count
  let activeEmployeeCount = $derived(
    agentPresence.activeRuns.filter(r => r.mode !== 'manager' && r.mode !== 'analyst' && r.status === 'running').length
  );

  // Employee heartbeat dots for top-right HUD
  let employeeStatuses = $derived(
    agentPresence.activeRuns
      .filter(r => r.mode !== 'manager' && r.mode !== 'analyst')
      .map((r, i) => ({
        index: i,
        status: r.status === 'running' ? 'working' : 'waiting',
        color: r.status === 'running' ? 'bg-info' : 'bg-warning',
      }))
  );

  // HUD bottom-left: cycle through active employee tools
  let cyclingToolText = $derived(() => {
    const employees = agentPresence.activeRuns.filter(r => r.mode !== 'manager' && r.mode !== 'analyst');
    if (employees.length === 0) return null;
    const idx = hudToolCycleIndex % employees.length;
    const emp = employees[idx];
    const tool = agentPresence.currentTool;
    if (tool) {
      return `E${idx}: ${tool.name} -- ${tool.summary.slice(0, 30)}`;
    }
    return `E${idx}: idle`;
  });

  // HUD bottom-right: per-employee token/turn breakdown
  let employeeTokenBreakdown = $derived(
    agentPresence.activeRuns
      .filter(r => r.mode !== 'manager' && r.mode !== 'analyst')
      .map((r, i) => ({
        index: i,
        turns: r.turns ?? 0,
      }))
  );
</script>

<div
  bind:this={container}
  class="relative w-full h-full"
  style="opacity: {opacity}"
  role="img"
  aria-label="Agent network visualization -- Mission Cortex"
>
  <canvas
    bind:this={canvas}
    class="w-full h-full {interactive ? 'cursor-pointer' : 'pointer-events-none'}"
    onmousemove={interactive ? handleMouseMove : undefined}
    onmouseleave={interactive ? handleMouseLeave : undefined}
    onclick={interactive ? handleClick : undefined}
  ></canvas>

  {#if tooltip}
    <div
      class="absolute pointer-events-none px-2 py-1 rounded text-xs glass text-text-dim whitespace-nowrap z-20"
      style="left: {tooltip.x}px; top: {tooltip.y}px; transform: translateX(-50%)"
    >
      {tooltip.text}
    </div>
  {/if}

  <!-- HUD: top-left — phase + active employee count -->
  <div class="absolute top-2 left-2 pointer-events-none z-10">
    <div class="text-[9px] font-data tracking-widest {phaseColorClass}">
      {phaseLabel}{#if activeEmployeeCount > 0} &middot; {activeEmployeeCount} EMPLOYEE{activeEmployeeCount !== 1 ? 'S' : ''}{/if}
    </div>
    <div class="text-[8px] font-data text-text-dim mt-0.5 opacity-60">
      Mission Cortex
    </div>
  </div>

  <!-- HUD: top-right — connection status + employee heartbeat dots -->
  <div class="absolute top-2 right-2 pointer-events-none z-10 text-right">
    <div class="flex items-center justify-end gap-1">
      <span class="text-[8px] font-data tracking-wider {agentPresence.wsConnected ? 'text-approve' : 'text-reject'}">
        {agentPresence.wsConnected ? 'Live' : 'Offline'}
      </span>
      <div class="w-1.5 h-1.5 rounded-full {agentPresence.wsConnected ? 'bg-approve' : 'bg-reject'}"></div>
    </div>
    {#if agentPresence.turnCount > 0}
      <div class="text-[8px] font-data text-text-dim mt-0.5 opacity-60">
        {agentPresence.turnCount} turns
      </div>
    {/if}
    <!-- Employee heartbeat indicators -->
    {#if employeeStatuses.length > 0}
      <div class="flex items-center justify-end gap-0.5 mt-1">
        {#each employeeStatuses as emp}
          <div
            class="w-1.5 h-1.5 rounded-full {emp.color}"
            class:animate-pulse={emp.status === 'working'}
            title="Employee {emp.index}: {emp.status}"
          ></div>
        {/each}
      </div>
    {/if}
  </div>

  <!-- HUD: bottom-left — cycling through active employee tools -->
  <div class="absolute bottom-2 left-2 pointer-events-none z-10">
    {#if cyclingToolText()}
      <div class="text-[8px] font-data text-text-dim opacity-60 transition-opacity duration-300">
        {cyclingToolText()}
      </div>
    {/if}
  </div>

  <!-- HUD: bottom-right — usage + per-employee token breakdown -->
  <div class="absolute bottom-2 right-2 pointer-events-none z-10 text-right">
    {#if usage}
      <div class="text-[8px] font-data text-text-dim opacity-60">
        Usage {Math.round(usage.usage_percent)}%
      </div>
    {/if}
    {#if employeeTokenBreakdown.length > 0}
      <div class="mt-0.5">
        {#each employeeTokenBreakdown as emp}
          <div class="text-[7px] font-data text-text-dim opacity-40">
            E{emp.index}: {emp.turns}t
          </div>
        {/each}
      </div>
    {/if}
  </div>
</div>
