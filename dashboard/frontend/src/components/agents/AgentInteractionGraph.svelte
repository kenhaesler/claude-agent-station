<script lang="ts">
  import type { AgentIdentity, ConversationEntry } from '../../lib/agent-presence.svelte';
  import type { CoordinatorMessage } from '../../lib/types';

  let {
    agents = [],
    conversationLog = [],
    messages = [],
    selectedAgent = null,
    onAgentClick,
  }: {
    agents?: AgentIdentity[];
    conversationLog?: ConversationEntry[];
    messages?: CoordinatorMessage[];
    selectedAgent?: string | null;
    onAgentClick?: (name: string) => void;
  } = $props();

  // Layout: Lead at top center, teammates in semi-circle below
  const WIDTH = 600;
  const HEIGHT = 220;
  const NODE_R = 28;

  interface NodePos { x: number; y: number; agent: AgentIdentity }

  let nodes = $derived.by(() => {
    if (agents.length === 0) return [];
    if (agents.length === 1) {
      return [{ x: WIDTH / 2, y: HEIGHT / 2, agent: agents[0] }];
    }

    const positions: NodePos[] = [];
    // Lead at top center
    const lead = agents.find(a => a.role === 'manager' || a.name === 'Lead') ?? agents[0];
    positions.push({ x: WIDTH / 2, y: 50, agent: lead });

    // Others in semi-circle below
    const others = agents.filter(a => a !== lead);
    const arcStart = Math.PI * 0.15;
    const arcEnd = Math.PI * 0.85;
    others.forEach((a, i) => {
      const t = others.length === 1 ? 0.5 : i / (others.length - 1);
      const angle = arcStart + t * (arcEnd - arcStart);
      const rx = (WIDTH - 120) / 2;
      const ry = 80;
      positions.push({
        x: WIDTH / 2 + rx * Math.cos(Math.PI - angle),
        y: 140 + ry * Math.sin(angle) * 0.3,
        agent: a,
      });
    });

    return positions;
  });

  // Edges: connect agents that have communicated
  interface Edge { from: NodePos; to: NodePos; active: boolean; color: string }

  let edges = $derived.by(() => {
    if (nodes.length < 2) return [];
    const edgeList: Edge[] = [];
    const lead = nodes[0];

    // Connect lead to every teammate
    for (let i = 1; i < nodes.length; i++) {
      edgeList.push({
        from: lead,
        to: nodes[i],
        active: nodes[i].agent.status === 'active' || nodes[i].agent.status === 'thinking',
        color: nodes[i].agent.color,
      });
    }

    return edgeList;
  });

  // Active pulses from recent events
  let pulses = $state<{ id: number; fromIdx: number; toIdx: number; color: string; progress: number }[]>([]);
  let pulseId = 0;

  // Watch for new coordinator messages to trigger pulses
  $effect(() => {
    const latest = messages[messages.length - 1];
    if (!latest || nodes.length < 2) return;

    const fromIdx = latest.direction === 'to_employee' ? 0 : Math.min((latest.employee_index ?? 0) + 1, nodes.length - 1);
    const toIdx = latest.direction === 'to_employee' ? Math.min((latest.employee_index ?? 0) + 1, nodes.length - 1) : 0;

    const newPulse = { id: ++pulseId, fromIdx, toIdx, color: nodes[fromIdx]?.agent.color ?? '#3b82f6', progress: 0 };
    pulses = [...pulses.slice(-5), newPulse];

    // Animate pulse
    const start = performance.now();
    const duration = 800;
    function animate(now: number) {
      const p = Math.min((now - start) / duration, 1);
      pulses = pulses.map(pulse =>
        pulse.id === newPulse.id ? { ...pulse, progress: p } : pulse
      );
      if (p < 1) requestAnimationFrame(animate);
      else {
        pulses = pulses.filter(pulse => pulse.id !== newPulse.id);
      }
    }
    requestAnimationFrame(animate);
  });

  function nodePath(from: NodePos, to: NodePos): string {
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    const cx = from.x + dx * 0.5;
    const cy = from.y + dy * 0.3;
    return `M ${from.x} ${from.y} Q ${cx} ${cy} ${to.x} ${to.y}`;
  }

  function pulsePosition(from: NodePos, to: NodePos, t: number): { x: number; y: number } {
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    const cx = from.x + dx * 0.5;
    const cy = from.y + dy * 0.3;
    // Quadratic bezier
    const u = 1 - t;
    return {
      x: u * u * from.x + 2 * u * t * cx + t * t * to.x,
      y: u * u * from.y + 2 * u * t * cy + t * t * to.y,
    };
  }

  // Activity count per agent (for node sizing)
  function activityCount(agentName: string): number {
    return conversationLog.filter(e => e.agentName === agentName && e.type === 'tool_use').length;
  }
</script>

{#if agents.length > 0}
  <svg viewBox="0 0 {WIDTH} {HEIGHT}" class="w-full" style="max-height: {HEIGHT}px">
    <!-- Edges -->
    {#each edges as edge}
      <path
        d={nodePath(edge.from, edge.to)}
        fill="none"
        stroke={edge.active ? edge.color : 'var(--color-border-subtle)'}
        stroke-width={edge.active ? 1.5 : 0.5}
        stroke-opacity={edge.active ? 0.4 : 0.15}
        stroke-dasharray={edge.active ? 'none' : '4 4'}
      />
    {/each}

    <!-- Animated pulses -->
    {#each pulses as pulse (pulse.id)}
      {#if nodes[pulse.fromIdx] && nodes[pulse.toIdx]}
        {@const pos = pulsePosition(nodes[pulse.fromIdx], nodes[pulse.toIdx], pulse.progress)}
        <circle
          cx={pos.x}
          cy={pos.y}
          r={4 + (1 - Math.abs(pulse.progress - 0.5) * 2) * 3}
          fill={pulse.color}
          opacity={1 - pulse.progress * 0.6}
        />
        <circle
          cx={pos.x}
          cy={pos.y}
          r={8 + (1 - Math.abs(pulse.progress - 0.5) * 2) * 4}
          fill={pulse.color}
          opacity={(1 - pulse.progress) * 0.15}
        />
      {/if}
    {/each}

    <!-- Nodes -->
    {#each nodes as node}
      {@const activity = activityCount(node.agent.name)}
      {@const r = NODE_R + Math.min(activity * 0.5, 8)}
      {@const isSelected = selectedAgent === node.agent.name}

      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <g
        class="cursor-pointer"
        onclick={() => onAgentClick?.(node.agent.name)}
        role="button"
        tabindex="0"
      >
        <!-- Glow ring for active agents -->
        {#if node.agent.status === 'active' || node.agent.status === 'thinking'}
          <circle
            cx={node.x}
            cy={node.y}
            r={r + 6}
            fill="none"
            stroke={node.agent.color}
            stroke-width="1"
            opacity="0.25"
          >
            <animate attributeName="r" values="{r + 4};{r + 8};{r + 4}" dur="2s" repeatCount="indefinite" />
            <animate attributeName="opacity" values="0.25;0.1;0.25" dur="2s" repeatCount="indefinite" />
          </circle>
        {/if}

        <!-- Selection ring -->
        {#if isSelected}
          <circle
            cx={node.x}
            cy={node.y}
            r={r + 3}
            fill="none"
            stroke="var(--color-cyan)"
            stroke-width="1.5"
            opacity="0.6"
          />
        {/if}

        <!-- Node circle -->
        <circle
          cx={node.x}
          cy={node.y}
          {r}
          fill="var(--color-surface-1)"
          stroke={node.agent.color}
          stroke-width={node.agent.status === 'active' ? 2 : 1}
        />

        <!-- Status dot -->
        <circle
          cx={node.x + r * 0.6}
          cy={node.y - r * 0.6}
          r="4"
          fill={node.agent.status === 'active' ? '#10b981' : node.agent.status === 'thinking' ? '#8b5cf6' : node.agent.status === 'error' ? '#ef4444' : '#6b728040'}
          stroke="var(--color-surface-1)"
          stroke-width="1.5"
        />

        <!-- Name label -->
        <text
          x={node.x}
          y={node.y + 2}
          text-anchor="middle"
          fill={node.agent.color}
          font-size="10"
          font-weight="600"
          class="font-mono pointer-events-none select-none"
        >
          {node.agent.name.length > 12 ? node.agent.name.slice(0, 10) + '..' : node.agent.name}
        </text>
      </g>
    {/each}
  </svg>
{:else}
  <div class="flex items-center justify-center h-[120px] text-sm text-tertiary font-mono">
    No agents active
  </div>
{/if}
