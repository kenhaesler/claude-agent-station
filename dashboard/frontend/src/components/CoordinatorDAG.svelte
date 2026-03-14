<script lang="ts">
  import type { CoordinatorTask } from '../lib/types';

  interface Props {
    tasks: CoordinatorTask[];
    class?: string;
  }

  let { tasks, class: className = '' }: Props = $props();

  // Layout constants
  const NODE_W = 120;
  const NODE_H = 40;
  const GAP_X = 40;
  const GAP_Y = 20;
  const PADDING = 20;

  // Build adjacency from depends_on
  interface LayoutNode {
    task: CoordinatorTask;
    col: number;
    row: number;
    x: number;
    y: number;
    deps: string[];
  }

  let layoutNodes = $derived((() => {
    if (tasks.length === 0) return [];

    // Parse dependencies
    const taskMap = new Map(tasks.map(t => [t.id, t]));
    const deps = new Map<string, string[]>();
    for (const t of tasks) {
      try {
        deps.set(t.id, t.depends_on ? JSON.parse(t.depends_on) : []);
      } catch {
        deps.set(t.id, []);
      }
    }

    // Topological sort for column assignment
    const visited = new Set<string>();
    const colMap = new Map<string, number>();

    function getCol(id: string): number {
      if (colMap.has(id)) return colMap.get(id)!;
      if (visited.has(id)) return 0; // cycle guard
      visited.add(id);
      const taskDeps = deps.get(id) ?? [];
      const maxDepCol = taskDeps.length > 0
        ? Math.max(...taskDeps.map(d => getCol(d))) + 1
        : 0;
      colMap.set(id, maxDepCol);
      return maxDepCol;
    }

    for (const t of tasks) getCol(t.id);

    // Group by column for row assignment
    const colGroups = new Map<number, string[]>();
    for (const [id, col] of colMap) {
      const group = colGroups.get(col) ?? [];
      group.push(id);
      colGroups.set(col, group);
    }

    const nodes: LayoutNode[] = [];
    for (const [id, col] of colMap) {
      const group = colGroups.get(col)!;
      const row = group.indexOf(id);
      nodes.push({
        task: taskMap.get(id)!,
        col,
        row,
        x: PADDING + col * (NODE_W + GAP_X),
        y: PADDING + row * (NODE_H + GAP_Y),
        deps: deps.get(id) ?? [],
      });
    }

    return nodes;
  })());

  let maxCol = $derived(Math.max(0, ...layoutNodes.map(n => n.col)));
  let maxRow = $derived(Math.max(0, ...layoutNodes.map(n => n.row)));
  let svgWidth = $derived(PADDING * 2 + (maxCol + 1) * (NODE_W + GAP_X));
  let svgHeight = $derived(PADDING * 2 + (maxRow + 1) * (NODE_H + GAP_Y));

  let nodeMap = $derived(new Map(layoutNodes.map(n => [n.task.id, n])));

  function statusColor(status: string): string {
    switch (status) {
      case 'completed': return 'oklch(0.72 0.17 155)';
      case 'running': return 'oklch(0.62 0.17 260)';
      case 'failed': return 'oklch(0.63 0.2 25)';
      case 'blocked': return 'oklch(0.75 0.15 80)';
      default: return 'oklch(0.50 0.008 260)';
    }
  }
</script>

<div class="overflow-x-auto {className}">
  <svg width={svgWidth} height={svgHeight} class="text-text-dim">
    <!-- Edges -->
    {#each layoutNodes as node}
      {#each node.deps as depId}
        {@const depNode = nodeMap.get(depId)}
        {#if depNode}
          <line
            x1={depNode.x + NODE_W}
            y1={depNode.y + NODE_H / 2}
            x2={node.x}
            y2={node.y + NODE_H / 2}
            stroke="oklch(0.30 0.005 260)"
            stroke-width="1.5"
            marker-end="url(#arrowhead)"
          />
        {/if}
      {/each}
    {/each}

    <!-- Arrow marker -->
    <defs>
      <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
        <path d="M0 0 L8 3 L0 6 Z" fill="oklch(0.30 0.005 260)" />
      </marker>
    </defs>

    <!-- Nodes -->
    {#each layoutNodes as node}
      <rect
        x={node.x}
        y={node.y}
        width={NODE_W}
        height={NODE_H}
        rx="6"
        fill="oklch(0.18 0.005 260)"
        stroke={statusColor(node.task.status)}
        stroke-width="1.5"
      />
      <text
        x={node.x + NODE_W / 2}
        y={node.y + 16}
        text-anchor="middle"
        fill="currentColor"
        font-size="10"
        font-family="inherit"
      >
        {node.task.title.length > 16 ? node.task.title.slice(0, 14) + '..' : node.task.title}
      </text>
      <text
        x={node.x + NODE_W / 2}
        y={node.y + 30}
        text-anchor="middle"
        fill={statusColor(node.task.status)}
        font-size="9"
        font-family="inherit"
      >
        {node.task.status}{node.task.employee_index != null ? ` (E${node.task.employee_index})` : ''}
      </text>
    {/each}
  </svg>
</div>
