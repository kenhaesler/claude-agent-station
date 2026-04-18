<script lang="ts">
  import { agentPresence } from '../lib/agent-presence.svelte';
  import { getCoordinatorTasks, getCoordinatorMessages, triggerRun, getActiveEmployees } from '../lib/api';
  import { addToast } from '../lib/toast.svelte';
  import { formatTokens, formatDuration, timeAgo } from '../lib/format';
  import type { CoordinatorTask, CoordinatorMessage, ActiveEmployee } from '../lib/types';

  import TeamLeadCard from '../components/agent-teams/TeamLeadCard.svelte';
  import TeammateGrid from '../components/agent-teams/TeammateGrid.svelte';
  import SharedTaskPanel from '../components/agent-teams/SharedTaskPanel.svelte';
  import ActivityFeed from '../components/agent-teams/ActivityFeed.svelte';

  let tasks = $state<CoordinatorTask[]>([]);
  let messages = $state<CoordinatorMessage[]>([]);
  let employees = $state<ActiveEmployee[]>([]);

  let latestRunId = $derived(agentPresence.latestRunId);
  let agents = $derived(agentPresence.agents);
  let isActive = $derived(agents.length > 0 || employees.length > 0);

  // Fetch coordinator data
  $effect(() => {
    loadEmployees();
    if (!latestRunId) return;
    loadData(latestRunId);
    const interval = setInterval(() => {
      loadEmployees();
      if (latestRunId) loadData(latestRunId);
    }, 5000);
    return () => clearInterval(interval);
  });

  async function loadEmployees() {
    try { employees = await getActiveEmployees(); } catch { /* silent */ }
  }

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

  // Map tasks to teammate card data — render every teammate, not just the
  // first five; hiding the tail misrepresents team size.
  let teammateData = $derived.by(() => {
    return tasks.map(t => {
      const statusType: 'working' | 'reviewing' | 'idle' | 'blocked' =
        t.status === 'running' ? 'working' :
        t.status === 'pending' || t.status === 'ready' ? 'idle' :
        t.status === 'blocked' ? 'blocked' :
        t.status === 'completed' ? 'working' : 'idle';

      const taskMessages = messages.filter(m => m.task_id === t.id).slice(-1);
      const latestMsg = taskMessages.length > 0 ? (() => {
        const raw = taskMessages[0].content;
        let displayMsg: string;
        if (typeof raw === 'string') {
          try {
            const parsed = JSON.parse(raw);
            displayMsg = parsed.tool ? `Using ${parsed.tool}` : raw.slice(0, 80);
          } catch { displayMsg = raw.slice(0, 80); }
        } else {
          displayMsg = (raw as any)?.tool ? `Using ${(raw as any).tool}` : JSON.stringify(raw).slice(0, 80);
        }
        return {
          sender: taskMessages[0].direction === 'to_employee' ? 'Lead' : (t.claimed_by ?? 'Teammate'),
          message: displayMsg,
          time: timeAgo(taskMessages[0].created_at),
          type: (taskMessages[0].direction === 'to_employee' ? 'lead' : 'peer') as 'peer' | 'lead',
        };
      })() : undefined;

      return {
        name: t.claimed_by ?? `Task ${t.id?.split('-').pop()?.slice(0, 4) ?? '?'}`,
        // Teammates run as Opus per CLAUDE.md; the lead (Sonnet) is the
        // TeamLeadCard above. If a real model name arrives on the task it
        // will override this default.
        model: (t as { model?: string | null }).model ?? 'claude-opus-4-6',
        task: t.title ?? 'Untitled task',
        status: t.status === 'running' ? (t.result_summary || 'Working...') :
                t.status === 'completed' ? 'Completed' :
                t.status === 'blocked' ? `Blocked` :
                t.status === 'pending' ? 'Pending' : t.status ?? '',
        statusType,
        detail: (() => {
          if (typeof t.touched_files === 'string') {
            try { const p = JSON.parse(t.touched_files); return p.turns ? `${p.turns} tool calls` : ''; }
            catch { return ''; }
          }
          return t.touched_files?.length ? `${t.touched_files.length} files changed` : '';
        })(),
        latestMessage: latestMsg,
        connections: [] as { direction: 'in' | 'out' | 'both'; target: string; type: 'peer' | 'lead' }[],
      };
    });
  });

  // Map tasks to shared task panel data
  let taskPanelData = $derived(
    tasks.map(t => ({
      name: t.title ?? 'Untitled',
      status: (t.status === 'running' ? 'progress' :
              t.status === 'blocked' ? 'blocked' :
              t.status === 'completed' ? 'progress' :
              'pending') as 'progress' | 'plan-review' | 'blocked' | 'pending',
      owner: `\u2192 ${t.claimed_by ?? 'Unassigned'}`,
      dependency: t.depends_on?.length ? `Depends on: ${t.depends_on.join(', ')}` : undefined,
    }))
  );

  // Map messages to activity feed events
  let activityEvents = $derived(
    messages.slice(-10).reverse().map(m => ({
      type: (m.direction === 'to_employee' ? 'lead' : 'peer') as 'peer' | 'lead' | 'system',
      sender: m.direction === 'to_employee' ? 'Lead' : (m.task_id ?? 'Teammate'),
      target: m.direction === 'to_employee' ? (m.task_id ?? 'Teammate') : 'Lead',
      message: (() => {
        const raw = m.content;
        if (typeof raw === 'string') {
          try { const p = JSON.parse(raw); return p.tool ? `Using ${p.tool}` : raw.slice(0, 100); }
          catch { return raw.slice(0, 100); }
        }
        return (raw as any)?.tool ? `Using ${(raw as any).tool}` : JSON.stringify(raw).slice(0, 100);
      })(),
      time: timeAgo(m.created_at),
    }))
  );

  let completedCount = $derived(tasks.filter(t => t.status === 'completed').length);
  let totalTokens = $derived(employees.reduce((s, e) => s + (e.tokens_total ?? 0), 0));
</script>

{#if !isActive && tasks.length === 0}
  <!-- Idle state -->
  <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; height: calc(100vh - 160px); text-align: center; padding: 0 20px;">
    <div style="width: 80px; height: 80px; border-radius: 50%; background: rgba(255,251,247,0.60); border: 1px dashed rgba(240,220,200,0.40); display: flex; align-items: center; justify-content: center; margin-bottom: 24px;">
      <span style="font-size: 28px; opacity: 0.25;">&#9670;</span>
    </div>
    <h2 style="font-size: 22px; font-weight: 800; color: #3D2A1A; margin-bottom: 8px;">The Team is Off-Duty</h2>
    <p style="font-size: 15px; color: #8C7A66; max-width: 420px; margin-bottom: 24px; line-height: 1.6;">
      When an agent team is running, this becomes your live mission control — see teammates, their tasks, and direct peer-to-peer communication.
    </p>
    <button onclick={handleTrigger} class="btn btn-primary">▶ Trigger a Run</button>
  </div>

{:else}
  <!-- Active team canvas -->
  <div style="position: fixed; top: 54px; left: 0; right: 0; bottom: 0; z-index: 1; display: grid; grid-template-columns: 1fr 280px;">

    <!-- Left: Lead + Teammate Grid -->
    <div style="position: relative; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 16px;">
      <TeamLeadCard
        teamName={latestRunId ?? ''}
        teammateCount={tasks.length}
        activity={agents.length > 0 ? `Coordinating ${agents.length} session${agents.length !== 1 ? 's' : ''}` : 'Monitoring tasks'}
        tasksCompleted={completedCount}
        tasksTotal={tasks.length}
        tokens={formatTokens(totalTokens)}
        duration={employees[0]?.started_at ? timeAgo(employees[0].started_at).replace(' ago', '') : '—'}
      />

      <TeammateGrid teammates={teammateData} />
    </div>

    <!-- Right: Task Panel + Activity -->
    <div style="display: flex; flex-direction: column; background: rgba(255,251,247,0.40); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px); border-left: 1px solid rgba(240,220,200,0.20); overflow-y: auto;">
      <SharedTaskPanel tasks={taskPanelData} />
      <ActivityFeed events={activityEvents} />
    </div>
  </div>
{/if}
