<script lang="ts">
  import { WorkspaceRenderer } from '../lib/workspace-renderer';
  import { agentPresence, togglePanel } from '../lib/agent-presence.svelte';
  import type { SystemStatus, UsageData } from '../lib/types';

  interface Props {
    systemStatus: SystemStatus | null;
    usage: UsageData | null;
  }

  let { systemStatus, usage }: Props = $props();

  let canvas: HTMLCanvasElement;
  let container: HTMLDivElement;
  let renderer: WorkspaceRenderer | null = null;
  let tooltip = $state<{ text: string; x: number; y: number } | null>(null);

  // Initialize renderer
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

  // Feed agent-presence data to renderer
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
        }
      }
    }
    lastLogLen = log.length;
  });

  function handleMouseMove(e: MouseEvent) {
    if (!renderer) return;
    const node = renderer.getNodeAt(e.clientX, e.clientY);
    if (node) {
      const rect = container.getBoundingClientRect();
      tooltip = {
        text: `${node.name} — ${node.isActive ? 'Active' : node.isThinking ? 'Thinking' : 'Idle'}`,
        x: e.clientX - rect.left,
        y: e.clientY - rect.top - 28,
      };
    } else if (renderer.isHubAt(e.clientX, e.clientY)) {
      const rect = container.getBoundingClientRect();
      tooltip = {
        text: `Station — ${agentPresence.phase === 'idle' ? 'Idle' : agentPresence.phase.replace('_', ' ')}`,
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
</script>

<div
  bind:this={container}
  class="relative w-full h-full"
  role="img"
  aria-label="Agent network visualization — Mission Cortex"
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

  <!-- Status overlay: top-left -->
  <div class="absolute top-2 left-2 pointer-events-none z-10">
    <div class="text-[9px] font-data tracking-widest {phaseColorClass}">
      {phaseLabel}
    </div>
    <div class="text-[8px] font-data text-text-dim mt-0.5 opacity-60">
      Mission Cortex
    </div>
  </div>

  <!-- Status overlay: top-right -->
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
  </div>

  <!-- Bottom-right: usage -->
  <div class="absolute bottom-2 right-2 pointer-events-none z-10 text-right">
    {#if usage}
      <div class="text-[8px] font-data text-text-dim opacity-60">
        Usage {Math.round(usage.usage_percent)}%
      </div>
    {/if}
  </div>
</div>
