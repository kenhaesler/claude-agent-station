<script lang="ts">
  import { agentPresence } from '../lib/agent-presence.svelte';
  import { getCoordinatorTasks, getCoordinatorMessages, triggerRun } from '../lib/api';
  import { addToast } from '../lib/toast.svelte';
  import { navigate } from '../lib/router.svelte';
  import type { CoordinatorTask, CoordinatorMessage } from '../lib/types';
  import AgentInteractionGraph from '../components/agents/AgentInteractionGraph.svelte';
  import AgentConversationPanel from '../components/agents/AgentConversationPanel.svelte';
  import AgentLiveCard from '../components/agents/AgentLiveCard.svelte';
  import TeamActivityTimeline from '../components/agents/TeamActivityTimeline.svelte';
  import SharedTaskList from '../components/agents/SharedTaskList.svelte';
  import GuidanceInput from '../components/agents/GuidanceInput.svelte';

  let tasks = $state<CoordinatorTask[]>([]);
  let messages = $state<CoordinatorMessage[]>([]);
  let focusedAgent = $state<string | null>(null);

  let latestRunId = $derived(agentPresence.latestRunId);
  let agents = $derived(agentPresence.agents);
  let isActive = $derived(agents.length > 0);

  // Auto-focus first agent
  $effect(() => {
    if (agents.length > 0 && (!focusedAgent || !agents.find(a => a.name === focusedAgent))) {
      focusedAgent = agents[0].name;
    }
    if (agents.length === 0) focusedAgent = null;
  });

  // Fetch coordinator data
  $effect(() => {
    if (!latestRunId) return;
    loadData(latestRunId);
    const interval = setInterval(() => { if (latestRunId) loadData(latestRunId); }, 5000);
    return () => clearInterval(interval);
  });

  async function loadData(runId: string) {
    try {
      const [t, m] = await Promise.allSettled([
        getCoordinatorTasks(runId),
        getCoordinatorMessages(runId),
      ]);
      if (t.status === 'fulfilled') tasks = t.value;
      if (m.status === 'fulfilled') messages = m.value;
    } catch { /* silent */ }
  }

  async function handleTrigger() {
    try {
      await triggerRun();
      addToast('success', 'Run triggered');
    } catch (e: any) {
      addToast('error', e.message);
    }
  }

  function selectAgent(name: string) { focusedAgent = name; }

  let focused = $derived(agents.find(a => a.name === focusedAgent));
  let sidebarAgents = $derived(agents.filter(a => a.name !== focusedAgent));

  // Reactive: re-filters whenever conversationLog or focusedAgent changes
  let focusedEntries = $derived(
    agentPresence.conversationLog.filter(e => e.agentName === focusedAgent)
  );

  function agentEntries(name: string) {
    return agentPresence.conversationLog.filter(e => e.agentName === name);
  }

  function phaseLabel(phase: string): string {
    const labels: Record<string, string> = {
      coordinating: 'Coordinating', employee: 'Team Working',
      plan_review: 'Plan Review', manager_review: 'Manager Reviewing',
      executing_verdict: 'Executing Verdict',
    };
    return labels[phase] ?? 'Idle';
  }
</script>

<div class="h-[calc(100vh-7rem)] flex flex-col animate-fade-in">
  {#if !isActive && tasks.length === 0}
    <!-- ==================== IDLE ==================== -->
    <div class="flex flex-col items-center justify-center h-full text-center px-4">
      <div class="mb-6">
        <svg width="80" height="80" viewBox="0 0 80 80">
          <circle cx="40" cy="40" r="30" fill="none" stroke="var(--color-text-muted)" stroke-width="1" stroke-dasharray="6 4" opacity="0.3" />
          <circle cx="40" cy="40" r="3" fill="var(--color-text-muted)" opacity="0.3" />
        </svg>
      </div>
      <h2 class="text-xl font-heading font-semibold text-primary mb-3">The Team is Off-Duty</h2>
      <p class="text-sm text-tertiary max-w-md mb-6 leading-relaxed">
        When agents are running, this becomes your live view into the team —
        see who's working, how they communicate, and steer their work.
      </p>
      <button onclick={handleTrigger} class="btn btn-primary">
        <span>▶</span> Trigger a Run
      </button>
    </div>

  {:else if agents.length === 1 && focused}
    <!-- ==================== SINGLE AGENT ==================== -->
    <div class="flex items-center justify-between px-4 py-2 border-b border-border shrink-0">
      <div class="flex items-center gap-3">
        <span class="text-sm font-heading font-semibold text-primary">{phaseLabel(agentPresence.phase)}</span>
        {#if latestRunId}
          <span class="text-xs font-mono text-tertiary">{latestRunId.slice(0, 16)}</span>
        {/if}
      </div>
    </div>
    <div class="flex-1 overflow-hidden relative">
      <AgentConversationPanel
        agent={focused}
        entries={focusedEntries}
        runId={latestRunId ?? ''}
        employeeIndex={focused.employeeIndex ?? 0}
        turnCount={agentPresence.turnCount}
        tokensBurned={agentPresence.tokensBurned}
        currentTool={agentPresence.currentTool}
      />
    </div>
    {#if latestRunId}
      <GuidanceInput runId={latestRunId} employeeIndex={focused.employeeIndex ?? 0} />
    {/if}

  {:else}
    <!-- ==================== TEAM VIEW ==================== -->
    <div class="flex items-center justify-between px-4 py-2 border-b border-border shrink-0">
      <div class="flex items-center gap-3">
        <span class="text-sm font-heading font-semibold text-primary">{phaseLabel(agentPresence.phase)}</span>
        {#if latestRunId}
          <span class="text-xs font-mono text-tertiary">{latestRunId.slice(0, 16)}</span>
        {/if}
      </div>
      <span class="text-xs font-mono text-tertiary">{agents.length} agents</span>
    </div>

    <!-- Interaction Graph -->
    <div class="border-b border-border shrink-0 bg-surface-0/30">
      <AgentInteractionGraph
        {agents}
        conversationLog={agentPresence.conversationLog}
        {messages}
        selectedAgent={focusedAgent}
        onAgentClick={selectAgent}
      />
    </div>

    <!-- Main: Focus + Sidebar | Timeline + Tasks -->
    <div class="flex-1 flex overflow-hidden min-h-0">
      <!-- Left: Focus + Cards (60%) -->
      <div class="flex flex-col overflow-hidden" style="width: 60%;">
        {#if focused}
          <div class="flex-1 overflow-hidden relative">
            <AgentConversationPanel
              agent={focused}
              entries={focusedEntries}
              runId={latestRunId ?? ''}
              employeeIndex={focused.employeeIndex ?? 0}
              turnCount={agentPresence.turnCount}
              tokensBurned={agentPresence.tokensBurned}
              currentTool={focused.name === agents[0]?.name ? agentPresence.currentTool : null}
            />
          </div>
          {#if latestRunId}
            <GuidanceInput runId={latestRunId} employeeIndex={focused.employeeIndex ?? 0} />
          {/if}
        {/if}

        <!-- Sidebar agent cards -->
        {#if sidebarAgents.length > 0}
          <div class="border-t border-border shrink-0 bg-surface-0/30 p-2">
            <div class="flex gap-2 overflow-x-auto">
              {#each sidebarAgents as agent}
                <div class="min-w-[200px] max-w-[260px] shrink-0">
                  <AgentLiveCard
                    {agent}
                    entries={agentEntries(agent.name)}
                    onclick={() => selectAgent(agent.name)}
                  />
                </div>
              {/each}
            </div>
          </div>
        {/if}
      </div>

      <!-- Right: Timeline + Tasks (40%) -->
      <div class="flex flex-col border-l border-border overflow-hidden" style="width: 40%;">
        <div class="flex-1 overflow-hidden min-h-0" style="flex: 6;">
          <TeamActivityTimeline
            conversationLog={agentPresence.conversationLog}
            {messages}
          />
        </div>
        <div class="border-t border-border overflow-hidden" style="flex: 4;">
          <SharedTaskList {tasks} />
        </div>
      </div>
    </div>
  {/if}
</div>
