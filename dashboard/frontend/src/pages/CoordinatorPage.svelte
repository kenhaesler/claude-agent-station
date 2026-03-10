<script lang="ts">
  import { getCoordinatorTasks, getCoordinatorMessages, sendGuidance } from '../lib/api';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import type { CoordinatorTask, CoordinatorMessage } from '../lib/types';

  let tasks = $state<CoordinatorTask[]>([]);
  let messages = $state<CoordinatorMessage[]>([]);
  let loading = $state(true);
  let selectedTask = $state<CoordinatorTask | null>(null);

  // Guidance form
  let guidanceEmployee = $state(0);
  let guidanceType = $state('info');
  let guidanceContent = $state('');
  let guidanceSending = $state(false);

  // Get latest run_id from tasks
  let latestRunId = $derived(tasks.length > 0 ? tasks[0].run_id : null);

  async function load() {
    loading = true;
    try {
      const [t, m] = await Promise.all([
        getCoordinatorTasks(),
        getCoordinatorMessages(),
      ]);
      tasks = t;
      messages = m;
    } catch (e: any) {
      toastError(`Failed to load: ${e.message}`);
    } finally {
      loading = false;
    }
  }

  async function handleSendGuidance() {
    if (!latestRunId || !guidanceContent.trim()) return;
    guidanceSending = true;
    try {
      await sendGuidance({
        run_id: latestRunId,
        employee_index: guidanceEmployee,
        guidance_type: guidanceType,
        content: guidanceContent,
      });
      toastSuccess('Guidance sent');
      guidanceContent = '';
      await load();
    } catch (e: any) {
      toastError(`Failed: ${e.message}`);
    } finally {
      guidanceSending = false;
    }
  }

  function statusColor(status: string): string {
    switch (status) {
      case 'completed': return 'text-green-400 bg-green-500/10 border-green-500/30';
      case 'running': return 'text-yellow-400 bg-yellow-500/10 border-yellow-500/30';
      case 'ready': return 'text-cyan-400 bg-cyan-500/10 border-cyan-500/30';
      case 'failed': return 'text-red-400 bg-red-500/10 border-red-500/30';
      case 'blocked': return 'text-orange-400 bg-orange-500/10 border-orange-500/30';
      default: return 'text-text-dim bg-white/5 border-white/10';
    }
  }

  function statusIcon(status: string): string {
    switch (status) {
      case 'completed': return '●';
      case 'running': return '◉';
      case 'ready': return '○';
      case 'failed': return '✕';
      case 'blocked': return '⊘';
      default: return '·';
    }
  }

  function msgTypeColor(type: string): string {
    switch (type) {
      case 'conflict': return 'text-red-400';
      case 'guidance': return 'text-cyan-400';
      case 'error': return 'text-red-400';
      default: return 'text-text-dim';
    }
  }

  function parseDeps(depsJson: string | null): string[] {
    if (!depsJson) return [];
    try { return JSON.parse(depsJson); } catch { return []; }
  }

  // Group tasks by run_id, sorted newest first
  let tasksByRun = $derived(() => {
    const grouped = tasks.reduce((acc, t) => {
      if (!acc[t.run_id]) acc[t.run_id] = [];
      acc[t.run_id].push(t);
      return acc;
    }, {} as Record<string, CoordinatorTask[]>);
    // Sort by newest first (run_id contains timestamp)
    return Object.entries(grouped).sort(([a], [b]) => b.localeCompare(a));
  });

  function formatRunId(runId: string): string {
    // run-20260310T225533Z -> "Mar 10, 22:55"
    const match = runId.match(/(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z/);
    if (!match) return runId;
    const [, y, mo, d, h, mi] = match;
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return `${months[parseInt(mo)-1]} ${parseInt(d)}, ${h}:${mi}`;
  }

  function runStatus(runTasks: CoordinatorTask[]): { label: string; color: string } {
    if (runTasks.some(t => t.status === 'running')) return { label: 'Active', color: 'text-yellow-400' };
    if (runTasks.every(t => t.status === 'completed')) return { label: 'Completed', color: 'text-green-400' };
    if (runTasks.some(t => t.status === 'failed')) return { label: 'Failed', color: 'text-red-400' };
    return { label: 'Pending', color: 'text-text-dim' };
  }

  $effect(() => {
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  });
</script>

<div class="space-y-6">
  <div class="flex items-center justify-between">
    <h1 class="ai-text text-glow-cyan text-xl font-bold">Coordinator</h1>
    <button onclick={load} class="px-3 py-1.5 text-xs rounded-lg glass-hud border border-hud-line text-text-dim hover:text-text cursor-pointer">
      Refresh
    </button>
  </div>

  {#if loading && tasks.length === 0}
    <div class="glass-hud rounded-xl p-8 text-center text-text-dim">Loading...</div>
  {:else if tasks.length === 0}
    <div class="glass-hud rounded-xl p-8 text-center text-text-dim">
      No coordinated runs yet. When the manager spawns multiple employees, their task DAG, progress, and messages will appear here.
    </div>
  {:else}
    <!-- Task DAG -->
    {#each tasksByRun() as [runId, runTasks]}
      {@const status = runStatus(runTasks)}
      <div class="glass-hud rounded-xl overflow-hidden">
        <div class="px-4 py-3 border-b border-hud-line flex items-center gap-3">
          <span class="text-accent-cyan text-sm font-medium">DAG</span>
          <span class="text-text text-xs">{formatRunId(runId)}</span>
          <span class="text-xs {status.color}">{status.label}</span>
          <span class="ml-auto text-xs text-text-dim">
            {runTasks.filter(t => t.status === 'completed').length}/{runTasks.length} tasks
          </span>
        </div>
        <div class="p-4">
          <!-- Task nodes -->
          <div class="grid gap-3" style="grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));">
            {#each runTasks as task}
              {@const deps = parseDeps(task.depends_on)}
              <button
                class="text-left p-3 rounded-lg border transition-all cursor-pointer hover:scale-[1.02] {statusColor(task.status)}"
                onclick={() => selectedTask = selectedTask?.id === task.id ? null : task}
              >
                <div class="flex items-center gap-2 mb-1">
                  <span class="text-lg">{statusIcon(task.status)}</span>
                  <span class="text-sm font-medium truncate flex-1">{task.title}</span>
                </div>
                <div class="flex items-center gap-3 text-xs opacity-70">
                  <span>{task.status}</span>
                  {#if task.employee_index != null}
                    <span>E{task.employee_index}</span>
                  {/if}
                  {#if deps.length > 0}
                    <span>deps: {deps.length}</span>
                  {/if}
                </div>
                {#if task.error_message}
                  <div class="mt-1 text-xs text-red-400 truncate">{task.error_message}</div>
                {/if}
              </button>
            {/each}
          </div>

          <!-- Dependency edges (text-based) -->
          {#if runTasks.some(t => parseDeps(t.depends_on).length > 0)}
            <div class="mt-4 pt-3 border-t border-hud-line">
              <div class="text-xs text-text-dim mb-2">Dependencies</div>
              <div class="flex flex-wrap gap-2">
                {#each runTasks as task}
                  {#each parseDeps(task.depends_on) as dep}
                    {@const depTask = runTasks.find(t => t.id === dep)}
                    {#if depTask}
                      <span class="text-xs px-2 py-1 rounded bg-white/5 text-text-dim">
                        {depTask.title} → {task.title}
                      </span>
                    {/if}
                  {/each}
                {/each}
              </div>
            </div>
          {/if}
        </div>
      </div>
    {/each}

    <!-- Task Detail Panel -->
    {#if selectedTask}
      <div class="glass-hud rounded-xl overflow-hidden">
        <div class="px-4 py-3 border-b border-hud-line flex items-center gap-3">
          <span class="text-accent-cyan text-sm font-medium">Task Detail</span>
          <button onclick={() => selectedTask = null} class="ml-auto text-text-dim hover:text-text text-sm cursor-pointer">&times;</button>
        </div>
        <div class="p-4 space-y-3 text-sm">
          <div><span class="text-text-dim">ID:</span> <span class="text-text font-mono text-xs">{selectedTask.id}</span></div>
          <div><span class="text-text-dim">Title:</span> <span class="text-text">{selectedTask.title}</span></div>
          <div><span class="text-text-dim">Status:</span> <span class={statusColor(selectedTask.status) + ' px-2 py-0.5 rounded text-xs'}>{selectedTask.status}</span></div>
          {#if selectedTask.description}
            <div><span class="text-text-dim">Description:</span> <span class="text-text">{selectedTask.description}</span></div>
          {/if}
          {#if selectedTask.employee_index != null}
            <div><span class="text-text-dim">Employee:</span> <span class="text-text">#{selectedTask.employee_index}</span></div>
          {/if}
          {#if selectedTask.issue_number}
            <div><span class="text-text-dim">Issue:</span> <span class="text-text">#{selectedTask.issue_number}</span></div>
          {/if}
          {#if selectedTask.started_at}
            <div><span class="text-text-dim">Started:</span> <span class="text-text">{new Date(selectedTask.started_at).toLocaleString()}</span></div>
          {/if}
          {#if selectedTask.finished_at}
            <div><span class="text-text-dim">Finished:</span> <span class="text-text">{new Date(selectedTask.finished_at).toLocaleString()}</span></div>
          {/if}
          {#if selectedTask.touched_files}
            <div>
              <span class="text-text-dim">Files touched:</span>
              <div class="mt-1 flex flex-wrap gap-1">
                {#each JSON.parse(selectedTask.touched_files || '[]') as f}
                  <span class="text-xs font-mono px-1.5 py-0.5 rounded bg-white/5 text-text-dim">{f}</span>
                {/each}
              </div>
            </div>
          {/if}
          {#if selectedTask.error_message}
            <div class="p-2 rounded bg-red-500/10 border border-red-500/20 text-red-400 text-xs">{selectedTask.error_message}</div>
          {/if}
        </div>
      </div>
    {/if}

    <!-- Guidance Panel -->
    {#if latestRunId}
      <div class="glass-hud rounded-xl overflow-hidden">
        <div class="px-4 py-3 border-b border-hud-line">
          <span class="text-accent-cyan text-sm font-medium">Send Guidance</span>
        </div>
        <div class="p-4">
          <div class="flex flex-wrap gap-3 items-end">
            <div>
              <label class="text-xs text-text-dim block mb-1">Employee</label>
              <input type="number" bind:value={guidanceEmployee} min="0" max="10"
                class="w-20 px-2 py-1.5 rounded-lg bg-white/5 border border-hud-line text-sm text-text" />
            </div>
            <div>
              <label class="text-xs text-text-dim block mb-1">Type</label>
              <select bind:value={guidanceType}
                class="px-2 py-1.5 rounded-lg bg-white/5 border border-hud-line text-sm text-text">
                <option value="info">Info</option>
                <option value="warning">Warning</option>
                <option value="redirect">Redirect</option>
                <option value="stop">Stop</option>
              </select>
            </div>
            <div class="flex-1 min-w-[200px]">
              <label class="text-xs text-text-dim block mb-1">Message</label>
              <input type="text" bind:value={guidanceContent} placeholder="Guidance message..."
                class="w-full px-2 py-1.5 rounded-lg bg-white/5 border border-hud-line text-sm text-text" />
            </div>
            <button
              onclick={handleSendGuidance}
              disabled={guidanceSending || !guidanceContent.trim()}
              class="px-4 py-1.5 rounded-lg bg-accent-cyan/20 border border-accent-cyan/30 text-accent-cyan text-sm hover:bg-accent-cyan/30 disabled:opacity-50 cursor-pointer"
            >
              {guidanceSending ? 'Sending...' : 'Send'}
            </button>
          </div>
        </div>
      </div>
    {/if}

    <!-- Message Log -->
    {#if messages.length > 0}
      <div class="glass-hud rounded-xl overflow-hidden">
        <div class="px-4 py-3 border-b border-hud-line">
          <span class="text-accent-cyan text-sm font-medium">Messages</span>
          <span class="text-text-dim text-xs ml-2">{messages.length}</span>
        </div>
        <div class="divide-y divide-hud-line max-h-80 overflow-auto">
          {#each messages as msg}
            <div class="px-4 py-2 flex items-start gap-3 text-xs">
              <span class={msgTypeColor(msg.message_type) + ' shrink-0'}>
                {msg.message_type}
              </span>
              <span class="text-text-dim shrink-0">
                {msg.direction === 'to_employee' ? '→' : '←'} E{msg.employee_index ?? '?'}
              </span>
              <span class="text-text flex-1 truncate">{msg.content}</span>
              {#if msg.created_at}
                <span class="text-text-dim shrink-0">{new Date(msg.created_at).toLocaleTimeString()}</span>
              {/if}
            </div>
          {/each}
        </div>
      </div>
    {/if}
  {/if}
</div>
