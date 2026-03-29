<script lang="ts">
  import { computeDagLayout, type DagNode } from '../../lib/dag-layout';
  import type { CoordinatorTask } from '../../lib/types';
  import DAGNode from './DAGNode.svelte';
  import DAGEdge from './DAGEdge.svelte';

  let {
    tasks = [],
    selectedTaskId = null,
    onNodeClick,
  }: {
    tasks: CoordinatorTask[];
    selectedTaskId?: string | null;
    onNodeClick?: (taskId: string) => void;
  } = $props();

  let dagNodes = $derived<DagNode[]>(
    tasks.map(t => ({
      id: t.id,
      label: t.title || `Task ${t.id}`,
      status: t.status,
      dependsOn: t.depends_on ? JSON.parse(t.depends_on) : [],
    }))
  );

  let layout = $derived(computeDagLayout(dagNodes));
</script>

{#if layout.nodes.length === 0}
  <div class="flex items-center justify-center h-full text-sm text-tertiary">
    No task graph available
  </div>
{:else}
  <div class="overflow-auto h-full w-full">
    <svg width={layout.width} height={layout.height} class="overflow-visible">
      <!-- Arrow marker definition -->
      <defs>
        <marker id="arrow" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
          <polygon points="0 0, 8 3, 0 6" fill="var(--color-border)" />
        </marker>
      </defs>

      <!-- Edges -->
      {#each layout.edges as edge}
        {@const fromTask = tasks.find(t => t.id === edge.from)}
        {@const toTask = tasks.find(t => t.id === edge.to)}
        <DAGEdge
          fromX={edge.fromX} fromY={edge.fromY}
          toX={edge.toX} toY={edge.toY}
          active={fromTask?.status === 'running' || toTask?.status === 'running'}
        />
      {/each}

      <!-- Nodes -->
      {#each layout.nodes as node}
        <DAGNode
          id={node.id}
          label={node.label}
          status={node.status}
          x={node.x}
          y={node.y}
          selected={node.id === selectedTaskId}
          onclick={() => onNodeClick?.(node.id)}
        />
      {/each}
    </svg>
  </div>
{/if}
