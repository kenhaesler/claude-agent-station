<script lang="ts">
  import { agentPresence } from '../lib/agent-presence.svelte';
  import { getCoordinatorTasks, getRunFullContext } from '../lib/api';
  import type { CoordinatorTask, RunFullContext } from '../lib/types';
  import SplitPane from '../components/layout/SplitPane.svelte';
  import AgentCard from '../components/agents/AgentCard.svelte';
  import AgentActivityFeed from '../components/agents/AgentActivityFeed.svelte';
  import GuidanceInput from '../components/agents/GuidanceInput.svelte';
  import DAGCanvas from '../components/dag/DAGCanvas.svelte';
  import SlidePanel from '../components/overlays/SlidePanel.svelte';

  let tasks = $state<CoordinatorTask[]>([]);
  let runContext = $state<RunFullContext | null>(null);
  let selectedTaskId = $state<string | null>(null);
  let panelOpen = $state(false);

  let latestRunId = $derived(agentPresence.latestRunId);
  let activeRuns = $derived(agentPresence.activeRuns);
  let isActive = $derived(activeRuns.length > 0);

  // Fetch DAG tasks when a run is active
  $effect(() => {
    if (!latestRunId) return;
    loadTasks(latestRunId);
    const interval = setInterval(() => { if (latestRunId) loadTasks(latestRunId); }, 5000);
    return () => clearInterval(interval);
  });

  async function loadTasks(runId: string) {
    try {
      const [tasksRes, contextRes] = await Promise.allSettled([
        getCoordinatorTasks(runId),
        getRunFullContext(runId),
      ]);
      if (tasksRes.status === 'fulfilled') tasks = tasksRes.value;
      if (contextRes.status === 'fulfilled') runContext = contextRes.value;
    } catch { /* silent */ }
  }

  function handleNodeClick(taskId: string) {
    selectedTaskId = taskId;
    panelOpen = true;
  }

  let selectedTask = $derived(tasks.find(t => t.id === selectedTaskId));
</script>

<div class="h-[calc(100vh-7rem)] animate-fade-in-up">
  {#if !isActive && tasks.length === 0}
    <!-- Empty state -->
    <div class="flex flex-col items-center justify-center h-full text-center">
      <div class="text-5xl mb-4 opacity-20">◉</div>
      <h2 class="text-lg font-semibold text-text-dim mb-2">No Agents Active</h2>
      <p class="text-sm text-text-muted max-w-md">
        When agents are running, you'll see live activity, task DAGs, and real-time logs here.
        Trigger a run from the Command Center to get started.
      </p>
    </div>
  {:else}
    <SplitPane direction="vertical" initialSplit={55}>
      {#snippet top()}
        <div class="flex gap-4 h-full p-2">
          <!-- Agent cards -->
          <div class="w-64 shrink-0 space-y-2 overflow-y-auto">
            <div class="text-[10px] text-text-muted uppercase tracking-wider px-1 mb-1">Agents</div>
            {#each agentPresence.agents as agent (agent.name)}
              <AgentCard
                name={agent.name}
                role={agent.role}
                color={agent.color}
                status={agent.status}
                currentTool={agent.currentAction ? { name: '', summary: agent.currentAction } : null}
                turns={agentPresence.turnCount}
                tokens={agentPresence.tokensBurned}
              />
            {/each}

            {#if agentPresence.agents.length === 0 && activeRuns.length > 0}
              {#each activeRuns as run}
                <div class="glass rounded-lg p-3 text-xs">
                  <div class="text-text font-medium">{run.run_id}</div>
                  <div class="text-text-muted">{run.mode} / {run.status}</div>
                </div>
              {/each}
            {/if}
          </div>

          <!-- DAG -->
          <div class="flex-1 glass rounded-lg overflow-hidden">
            {#if tasks.length > 0}
              <DAGCanvas {tasks} {selectedTaskId} onNodeClick={handleNodeClick} />
            {:else}
              <div class="flex items-center justify-center h-full text-sm text-text-muted">
                {isActive ? 'Waiting for task graph...' : 'No task graph'}
              </div>
            {/if}
          </div>
        </div>
      {/snippet}

      {#snippet bottom()}
        <div class="flex flex-col h-full">
          <div class="text-[10px] text-text-muted uppercase tracking-wider px-3 py-2 border-b border-border-subtle">
            Activity Log
          </div>
          <div class="flex-1 relative overflow-hidden">
            <AgentActivityFeed />
          </div>
          {#if latestRunId && isActive}
            <GuidanceInput runId={latestRunId} />
          {/if}
        </div>
      {/snippet}
    </SplitPane>
  {/if}
</div>

<!-- Task detail panel -->
<SlidePanel open={panelOpen} onClose={() => panelOpen = false} title="Task Detail">
  {#if selectedTask}
    <div class="space-y-4 text-sm">
      <div>
        <div class="text-text-muted text-xs mb-1">Title</div>
        <div class="text-text font-medium">{selectedTask.title}</div>
      </div>
      <div>
        <div class="text-text-muted text-xs mb-1">Status</div>
        <div class="text-text-dim capitalize">{selectedTask.status}</div>
      </div>
      {#if selectedTask.description}
        <div>
          <div class="text-text-muted text-xs mb-1">Description</div>
          <div class="text-text-dim">{selectedTask.description}</div>
        </div>
      {/if}
      {#if selectedTask.claimed_by}
        <div>
          <div class="text-text-muted text-xs mb-1">Claimed By</div>
          <div class="text-text-dim">{selectedTask.claimed_by}</div>
        </div>
      {/if}
      {#if selectedTask.result_summary}
        <div>
          <div class="text-text-muted text-xs mb-1">Result</div>
          <div class="text-text-dim whitespace-pre-wrap">{selectedTask.result_summary}</div>
        </div>
      {/if}
      {#if selectedTask.error_message}
        <div>
          <div class="text-text-muted text-xs mb-1">Error</div>
          <div class="text-reject">{selectedTask.error_message}</div>
        </div>
      {/if}
    </div>
  {/if}
</SlidePanel>
