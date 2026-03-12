<script lang="ts">
  import { getCoordinatorTasks, getCoordinatorMessages, getCoordinatorTaskDetails, sendGuidance } from '../lib/api';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import type { CoordinatorTask, CoordinatorTaskDetail, CoordinatorMessage } from '../lib/types';
  import { onMount, tick } from 'svelte';

  let tasks = $state<CoordinatorTask[]>([]);
  let messages = $state<CoordinatorMessage[]>([]);
  let loading = $state(true);
  let selectedTaskId = $state<string | null>(null);
  let taskDetail = $state<CoordinatorTaskDetail | null>(null);
  let detailLoading = $state(false);
  let showLogExcerpt = $state(false);

  // Guidance form
  let guidanceEmployee = $state(0);
  let guidanceType = $state('info');
  let guidanceContent = $state('');
  let guidanceSending = $state(false);

  // DAG node positions for SVG edges
  let nodeElements = $state<Record<string, HTMLElement | null>>({});

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

  async function selectTask(task: CoordinatorTask) {
    if (selectedTaskId === task.id) {
      selectedTaskId = null;
      taskDetail = null;
      showLogExcerpt = false;
      return;
    }
    selectedTaskId = task.id;
    taskDetail = null;
    detailLoading = true;
    showLogExcerpt = false;
    try {
      taskDetail = await getCoordinatorTaskDetails(task.id);
    } catch {
      // Fallback: show basic task data if detail endpoint fails
      taskDetail = { ...task, employee_report: null, log_excerpt: null } as CoordinatorTaskDetail;
    } finally {
      detailLoading = false;
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

  function statusBgColor(status: string): string {
    switch (status) {
      case 'completed': return '#22c55e';
      case 'running': return '#eab308';
      case 'ready': return '#06b6d4';
      case 'failed': return '#ef4444';
      case 'blocked': return '#f97316';
      default: return '#6b7280';
    }
  }

  function statusIcon(status: string): string {
    switch (status) {
      case 'completed': return '✓';
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

  function parseJsonArray(json: string | null): string[] {
    if (!json) return [];
    try { return JSON.parse(json); } catch { return []; }
  }

  // Group tasks by run_id, sorted newest first
  let tasksByRun = $derived(() => {
    const grouped = tasks.reduce((acc, t) => {
      if (!acc[t.run_id]) acc[t.run_id] = [];
      acc[t.run_id].push(t);
      return acc;
    }, {} as Record<string, CoordinatorTask[]>);
    return Object.entries(grouped).sort(([a], [b]) => b.localeCompare(a));
  });

  function formatRunId(runId: string): string {
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

  function formatDuration(start: string | null, end: string | null): string {
    if (!start) return '--';
    const s = new Date(start).getTime();
    const e = end ? new Date(end).getTime() : Date.now();
    const ms = e - s;
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
  }

  // Topological sort for DAG layout (tasks arranged in dependency layers)
  function topoLayers(runTasks: CoordinatorTask[]): CoordinatorTask[][] {
    const taskMap = new Map(runTasks.map(t => [t.id, t]));
    const layers: CoordinatorTask[][] = [];
    const placed = new Set<string>();

    // Place tasks layer by layer: a task goes into the first layer where all deps are placed
    let remaining = [...runTasks];
    let maxIterations = remaining.length + 1;
    while (remaining.length > 0 && maxIterations-- > 0) {
      const layer: CoordinatorTask[] = [];
      const nextRemaining: CoordinatorTask[] = [];
      for (const task of remaining) {
        const deps = parseDeps(task.depends_on);
        if (deps.every(d => placed.has(d))) {
          layer.push(task);
        } else {
          nextRemaining.push(task);
        }
      }
      if (layer.length === 0) {
        // Circular dependency or orphan — dump remaining
        layers.push(nextRemaining);
        break;
      }
      layers.push(layer);
      layer.forEach(t => placed.add(t.id));
      remaining = nextRemaining;
    }
    return layers;
  }

  // SVG edge computation
  interface DagEdge {
    fromId: string;
    toId: string;
    fromStatus: string;
    toStatus: string;
  }

  function computeEdges(runTasks: CoordinatorTask[]): DagEdge[] {
    const edges: DagEdge[] = [];
    const taskMap = new Map(runTasks.map(t => [t.id, t]));
    for (const task of runTasks) {
      for (const depId of parseDeps(task.depends_on)) {
        const dep = taskMap.get(depId);
        if (dep) {
          edges.push({
            fromId: depId,
            toId: task.id,
            fromStatus: dep.status,
            toStatus: task.status,
          });
        }
      }
    }
    return edges;
  }

  // SVG path calculation between DOM nodes
  function getEdgePath(
    fromEl: HTMLElement | null | undefined,
    toEl: HTMLElement | null | undefined,
    container: HTMLElement | null
  ): string | null {
    if (!fromEl || !toEl || !container) return null;
    const containerRect = container.getBoundingClientRect();
    const fromRect = fromEl.getBoundingClientRect();
    const toRect = toEl.getBoundingClientRect();

    const x1 = fromRect.left + fromRect.width / 2 - containerRect.left;
    const y1 = fromRect.top + fromRect.height - containerRect.top;
    const x2 = toRect.left + toRect.width / 2 - containerRect.left;
    const y2 = toRect.top - containerRect.top;

    // Bezier curve for smooth edge
    const midY = (y1 + y2) / 2;
    return `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`;
  }

  // Re-compute SVG edges after DOM updates
  let svgEdgePaths = $state<{ path: string; color: string }[]>([]);
  let dagContainers = $state<Record<string, HTMLElement | null>>({});

  async function recomputeEdges(runId: string, runTasks: CoordinatorTask[]) {
    await tick();
    const container = dagContainers[runId];
    if (!container) return;
    const edges = computeEdges(runTasks);
    const paths: { path: string; color: string }[] = [];
    for (const edge of edges) {
      const fromEl = nodeElements[edge.fromId];
      const toEl = nodeElements[edge.toId];
      const p = getEdgePath(fromEl, toEl, container);
      if (p) {
        const color = edge.fromStatus === 'completed' ? '#22c55e80' : '#6b728080';
        paths.push({ path: p, color });
      }
    }
    svgEdgePaths = paths;
  }

  // Trigger edge recompute when tasks change
  $effect(() => {
    const runs = tasksByRun();
    if (runs.length > 0) {
      const [runId, runTasks] = runs[0];
      recomputeEdges(runId, runTasks);
    }
  });

  $effect(() => {
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  });
</script>

<div class="space-y-6">
  <div class="flex items-center justify-between">
    <h1 class="text-xl font-bold">Coordinator</h1>
    <button onclick={load} class="px-3 py-1.5 text-xs rounded-lg bg-surface border border-border text-text-dim hover:text-text cursor-pointer">
      Refresh
    </button>
  </div>

  {#if loading && tasks.length === 0}
    <div class="bg-surface border border-border-subtle rounded-xl p-8 text-center text-text-dim">Loading...</div>
  {:else if tasks.length === 0}
    <div class="bg-surface border border-border-subtle rounded-xl p-8 text-center text-text-dim">
      No coordinated runs yet. When the manager spawns multiple employees, their task DAG, progress, and messages will appear here.
    </div>
  {:else}
    <!-- Task DAGs grouped by run -->
    {#each tasksByRun() as [runId, runTasks]}
      {@const status = runStatus(runTasks)}
      {@const layers = topoLayers(runTasks)}
      {@const edges = computeEdges(runTasks)}
      <div class="bg-surface border border-border-subtle rounded-xl overflow-hidden">
        <div class="px-4 py-3 border-b border-border flex items-center gap-3">
          <span class="text-accent-blue text-sm font-medium">DAG</span>
          <span class="text-text text-xs">{formatRunId(runId)}</span>
          <span class="text-xs {status.color}">{status.label}</span>
          <span class="ml-auto text-xs text-text-dim">
            {runTasks.filter(t => t.status === 'completed').length}/{runTasks.length} tasks
          </span>
        </div>

        <!-- DAG Visual Container -->
        <div class="p-4 relative" bind:this={dagContainers[runId]}>
          <!-- SVG overlay for edges -->
          {#if svgEdgePaths.length > 0}
            <svg class="absolute inset-0 w-full h-full pointer-events-none" style="z-index: 0;">
              <defs>
                <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
                  <polygon points="0 0, 8 3, 0 6" fill="#6b7280" opacity="0.6" />
                </marker>
              </defs>
              {#each svgEdgePaths as edge}
                <path
                  d={edge.path}
                  stroke={edge.color}
                  stroke-width="2"
                  fill="none"
                  marker-end="url(#arrowhead)"
                  class="transition-all duration-300"
                />
              {/each}
            </svg>
          {/if}

          <!-- Task nodes arranged in layers -->
          <div class="relative" style="z-index: 1;">
            {#each layers as layer, layerIdx}
              {#if layerIdx > 0}
                <div class="flex justify-center my-2">
                  <div class="w-px h-4 bg-border opacity-30"></div>
                </div>
              {/if}
              <div class="flex flex-wrap justify-center gap-3">
                {#each layer as task}
                  {@const deps = parseDeps(task.depends_on)}
                  {@const isSelected = selectedTaskId === task.id}
                  <button
                    bind:this={nodeElements[task.id]}
                    class="dag-node text-left p-3 rounded-lg border-2 transition-all duration-200
                      cursor-pointer select-none min-w-[240px] max-w-[320px] flex-1
                      hover:scale-[1.03] hover:shadow-lg hover:shadow-black/20
                      active:scale-[0.98]
                      {isSelected
                        ? 'ring-2 ring-accent-blue ring-offset-2 ring-offset-transparent border-accent-blue/50 bg-accent-blue/10'
                        : statusColor(task.status)}"
                    onclick={() => selectTask(task)}
                  >
                    <div class="flex items-center gap-2 mb-1.5">
                      <span
                        class="w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold border"
                        style="background: {statusBgColor(task.status)}20; border-color: {statusBgColor(task.status)}60; color: {statusBgColor(task.status)};"
                      >
                        {statusIcon(task.status)}
                      </span>
                      <span class="text-sm font-medium truncate flex-1 text-text">{task.title}</span>
                      <span class="text-text-dim text-xs shrink-0 transition-transform duration-200" class:rotate-180={isSelected}>
                        ▼
                      </span>
                    </div>
                    <div class="flex items-center gap-3 text-xs opacity-70">
                      <span class="capitalize">{task.status}</span>
                      {#if task.employee_index != null}
                        <span class="px-1.5 py-0.5 rounded bg-white/10 text-text-dim">E{task.employee_index}</span>
                      {/if}
                      {#if deps.length > 0}
                        <span class="text-text-dim">{deps.length} dep{deps.length > 1 ? 's' : ''}</span>
                      {/if}
                      {#if task.started_at}
                        <span class="text-text-dim ml-auto">{formatDuration(task.started_at, task.finished_at)}</span>
                      {/if}
                    </div>
                    {#if task.result_summary}
                      <div class="mt-1.5 text-xs text-text-dim truncate">{task.result_summary}</div>
                    {/if}
                    {#if task.error_message}
                      <div class="mt-1.5 text-xs text-red-400 truncate">{task.error_message}</div>
                    {/if}
                  </button>
                {/each}
              </div>
            {/each}
          </div>

          <!-- Dependency Legend -->
          {#if edges.length > 0}
            <div class="mt-4 pt-3 border-t border-border flex items-center gap-4 text-xs text-text-dim">
              <span>Dependencies:</span>
              <span class="flex items-center gap-1">
                <span class="w-4 h-0.5 bg-green-500/50 inline-block rounded"></span>
                Completed
              </span>
              <span class="flex items-center gap-1">
                <span class="w-4 h-0.5 bg-gray-500/50 inline-block rounded"></span>
                Pending
              </span>
              <span class="ml-auto">{edges.length} edge{edges.length !== 1 ? 's' : ''}</span>
            </div>
          {/if}
        </div>
      </div>
    {/each}

    <!-- Task Detail Side Panel -->
    {#if selectedTaskId}
      <div class="bg-surface border border-border-subtle rounded-xl overflow-hidden">
        <div class="px-4 py-3 border-b border-border flex items-center gap-3 bg-accent-blue/5">
          <span class="text-accent-blue text-sm font-bold">Task Details</span>
          {#if detailLoading}
            <span class="text-xs text-text-dim animate-pulse">Loading...</span>
          {/if}
          <button onclick={() => { selectedTaskId = null; taskDetail = null; showLogExcerpt = false; }} class="ml-auto text-text-dim hover:text-text text-lg cursor-pointer leading-none">&times;</button>
        </div>

        {#if taskDetail}
          <div class="p-4 space-y-4 text-sm">
            <!-- Basic Info -->
            <div class="grid grid-cols-2 gap-3">
              <div>
                <span class="text-text-dim text-xs block mb-0.5">Title</span>
                <span class="text-text font-medium">{taskDetail.title}</span>
              </div>
              <div>
                <span class="text-text-dim text-xs block mb-0.5">Status</span>
                <span class="{statusColor(taskDetail.status)} px-2 py-0.5 rounded text-xs inline-block">{taskDetail.status}</span>
              </div>
              <div>
                <span class="text-text-dim text-xs block mb-0.5">Task ID</span>
                <span class="text-text font-mono text-xs">{taskDetail.id}</span>
              </div>
              {#if taskDetail.employee_index != null}
                <div>
                  <span class="text-text-dim text-xs block mb-0.5">Employee</span>
                  <span class="text-text">#{taskDetail.employee_index}</span>
                </div>
              {/if}
              {#if taskDetail.issue_number}
                <div>
                  <span class="text-text-dim text-xs block mb-0.5">Issue</span>
                  <a href="https://github.com/{taskDetail.project_repo}/issues/{taskDetail.issue_number}" target="_blank" rel="noopener" class="text-accent-blue hover:underline">
                    #{taskDetail.issue_number}
                  </a>
                </div>
              {/if}
              {#if taskDetail.branch}
                <div>
                  <span class="text-text-dim text-xs block mb-0.5">Branch</span>
                  <span class="text-text font-mono text-xs">{taskDetail.branch}</span>
                </div>
              {/if}
              {#if taskDetail.exit_code != null}
                <div>
                  <span class="text-text-dim text-xs block mb-0.5">Exit Code</span>
                  <span class="text-text font-mono" class:text-green-400={taskDetail.exit_code === 0} class:text-red-400={taskDetail.exit_code !== 0}>{taskDetail.exit_code}</span>
                </div>
              {/if}
            </div>

            <!-- Timestamps -->
            {#if taskDetail.started_at || taskDetail.finished_at}
              <div class="grid grid-cols-2 gap-3 pt-3 border-t border-border">
                {#if taskDetail.started_at}
                  <div>
                    <span class="text-text-dim text-xs block mb-0.5">Started</span>
                    <span class="text-text text-xs">{new Date(taskDetail.started_at).toLocaleString()}</span>
                  </div>
                {/if}
                {#if taskDetail.finished_at}
                  <div>
                    <span class="text-text-dim text-xs block mb-0.5">Finished</span>
                    <span class="text-text text-xs">{new Date(taskDetail.finished_at).toLocaleString()}</span>
                  </div>
                {/if}
                {#if taskDetail.started_at}
                  <div>
                    <span class="text-text-dim text-xs block mb-0.5">Duration</span>
                    <span class="text-text text-xs">{formatDuration(taskDetail.started_at, taskDetail.finished_at)}</span>
                  </div>
                {/if}
              </div>
            {/if}

            <!-- Description -->
            {#if taskDetail.description}
              <div class="pt-3 border-t border-border">
                <span class="text-text-dim text-xs block mb-1">Description</span>
                <p class="text-text text-sm">{taskDetail.description}</p>
              </div>
            {/if}

            <!-- Result Summary -->
            {#if taskDetail.result_summary}
              <div class="pt-3 border-t border-border">
                <span class="text-text-dim text-xs block mb-1">Result Summary</span>
                <p class="text-text text-sm bg-green-500/5 border border-green-500/20 rounded p-2">{taskDetail.result_summary}</p>
              </div>
            {/if}

            <!-- Error Message -->
            {#if taskDetail.error_message}
              <div class="pt-3 border-t border-border">
                <span class="text-text-dim text-xs block mb-1">Error</span>
                <div class="p-2 rounded bg-red-500/10 border border-red-500/20 text-red-400 text-xs font-mono whitespace-pre-wrap">{taskDetail.error_message}</div>
              </div>
            {/if}

            <!-- Files Touched -->
            {#if taskDetail.touched_files}
              {@const files = parseJsonArray(taskDetail.touched_files)}
              {#if files.length > 0}
                <div class="pt-3 border-t border-border">
                  <span class="text-text-dim text-xs block mb-1">Files Touched ({files.length})</span>
                  <div class="flex flex-wrap gap-1">
                    {#each files as f}
                      <span class="text-xs font-mono px-1.5 py-0.5 rounded bg-white/5 text-text-dim">{f}</span>
                    {/each}
                  </div>
                </div>
              {/if}
            {/if}

            <!-- Employee Report -->
            {#if taskDetail.employee_report}
              {@const report = taskDetail.employee_report}
              <div class="pt-3 border-t border-border">
                <span class="text-text-dim text-xs block mb-2">Employee Report</span>
                <div class="space-y-2 bg-white/3 rounded-lg p-3 border border-border">
                  {#if report.status}
                    <div class="flex items-center gap-2">
                      <span class="text-text-dim text-xs">Status:</span>
                      <span class="text-xs px-2 py-0.5 rounded {report.status === 'success' ? 'bg-green-500/20 text-green-400' : report.status === 'partial' ? 'bg-yellow-500/20 text-yellow-400' : report.status === 'failure' ? 'bg-red-500/20 text-red-400' : 'bg-white/10 text-text-dim'}">
                        {report.status}
                      </span>
                    </div>
                  {/if}
                  {#if report.issue_title}
                    <div class="text-xs"><span class="text-text-dim">Issue:</span> <span class="text-text">{report.issue_title}</span></div>
                  {/if}
                  {#if report.branch}
                    <div class="text-xs"><span class="text-text-dim">Branch:</span> <span class="text-text font-mono">{report.branch}</span></div>
                  {/if}
                  {#if report.tests_run != null}
                    <div class="text-xs">
                      <span class="text-text-dim">Tests:</span>
                      <span class:text-green-400={report.tests_passed} class:text-red-400={!report.tests_passed}>
                        {report.tests_passed ? 'Passed' : 'Failed'}
                      </span>
                      {#if report.test_output_summary}
                        <span class="text-text-dim ml-1">({report.test_output_summary})</span>
                      {/if}
                    </div>
                  {/if}
                  {#if Array.isArray(report.requirements)}
                    <div class="mt-2">
                      <span class="text-text-dim text-xs block mb-1">Requirements:</span>
                      <div class="space-y-1">
                        {#each report.requirements as req}
                          <div class="flex items-start gap-1.5 text-xs">
                            <span class:text-green-400={req.completed} class:text-red-400={!req.completed}>{req.completed ? '✓' : '✕'}</span>
                            <span class="text-text">{req.description}</span>
                          </div>
                        {/each}
                      </div>
                    </div>
                  {/if}
                  {#if report.files_changed && Array.isArray(report.files_changed)}
                    <div class="mt-2">
                      <span class="text-text-dim text-xs block mb-1">Files Changed:</span>
                      <div class="flex flex-wrap gap-1">
                        {#each report.files_changed as f}
                          <span class="text-xs font-mono px-1.5 py-0.5 rounded bg-white/5 text-text-dim">{f}</span>
                        {/each}
                      </div>
                    </div>
                  {/if}
                  {#if report.notes}
                    <div class="text-xs mt-2"><span class="text-text-dim">Notes:</span> <span class="text-text">{report.notes}</span></div>
                  {/if}
                </div>
              </div>
            {/if}

            <!-- Log Excerpt -->
            {#if taskDetail.log_excerpt}
              <div class="pt-3 border-t border-border">
                <button
                  class="text-xs text-accent-blue hover:underline cursor-pointer flex items-center gap-1"
                  onclick={() => showLogExcerpt = !showLogExcerpt}
                >
                  <span class="transition-transform duration-200" class:rotate-90={showLogExcerpt}>&#9654;</span>
                  {showLogExcerpt ? 'Hide' : 'Show'} Log Output (last 100 lines)
                </button>
                {#if showLogExcerpt}
                  <pre class="mt-2 p-3 rounded bg-black/30 border border-border text-xs text-text-dim font-mono overflow-auto max-h-80 whitespace-pre-wrap">{taskDetail.log_excerpt}</pre>
                {/if}
              </div>
            {/if}

            <!-- Dependencies -->
            {#if parseDeps(taskDetail.depends_on).length > 0}
              <div class="pt-3 border-t border-border">
                <span class="text-text-dim text-xs block mb-1">Depends On</span>
                <div class="flex flex-wrap gap-1">
                  {#each parseDeps(taskDetail.depends_on) as depId}
                    {@const depTask = tasks.find(t => t.id === depId)}
                    {#if depTask}
                      <button
                        class="text-xs px-2 py-1 rounded border cursor-pointer hover:bg-white/10 transition-colors {statusColor(depTask.status)}"
                        onclick={() => selectTask(depTask)}
                      >
                        {depTask.title}
                      </button>
                    {:else}
                      <span class="text-xs font-mono px-1.5 py-0.5 rounded bg-white/5 text-text-dim">{depId}</span>
                    {/if}
                  {/each}
                </div>
              </div>
            {/if}
          </div>
        {:else if detailLoading}
          <div class="p-8 text-center text-text-dim animate-pulse">Loading task details...</div>
        {/if}
      </div>
    {/if}

    <!-- Guidance Panel -->
    {#if latestRunId}
      <div class="bg-surface border border-border-subtle rounded-xl overflow-hidden">
        <div class="px-4 py-3 border-b border-border">
          <span class="text-accent-blue text-sm font-medium">Send Guidance</span>
        </div>
        <div class="p-4">
          <div class="flex flex-wrap gap-3 items-end">
            <div>
              <label class="text-xs text-text-dim block mb-1">Employee</label>
              <input type="number" bind:value={guidanceEmployee} min="0" max="10"
                class="w-20 px-2 py-1.5 rounded-lg bg-white/5 border border-border text-sm text-text" />
            </div>
            <div>
              <label class="text-xs text-text-dim block mb-1">Type</label>
              <select bind:value={guidanceType}
                class="px-2 py-1.5 rounded-lg bg-white/5 border border-border text-sm text-text">
                <option value="info">Info</option>
                <option value="warning">Warning</option>
                <option value="redirect">Redirect</option>
                <option value="stop">Stop</option>
              </select>
            </div>
            <div class="flex-1 min-w-[200px]">
              <label class="text-xs text-text-dim block mb-1">Message</label>
              <input type="text" bind:value={guidanceContent} placeholder="Guidance message..."
                class="w-full px-2 py-1.5 rounded-lg bg-white/5 border border-border text-sm text-text" />
            </div>
            <button
              onclick={handleSendGuidance}
              disabled={guidanceSending || !guidanceContent.trim()}
              class="px-4 py-1.5 rounded-lg bg-accent-blue/20 border border-accent-blue/30 text-accent-blue text-sm hover:bg-accent-blue/30 disabled:opacity-50 cursor-pointer"
            >
              {guidanceSending ? 'Sending...' : 'Send'}
            </button>
          </div>
        </div>
      </div>
    {/if}

    <!-- Message Log -->
    {#if messages.length > 0}
      <div class="bg-surface border border-border-subtle rounded-xl overflow-hidden">
        <div class="px-4 py-3 border-b border-border">
          <span class="text-accent-blue text-sm font-medium">Messages</span>
          <span class="text-text-dim text-xs ml-2">{messages.length}</span>
        </div>
        <div class="divide-y divide-border max-h-80 overflow-auto">
          {#each messages as msg}
            <div class="px-4 py-2 flex items-start gap-3 text-xs">
              <span class="{msgTypeColor(msg.message_type)} shrink-0">
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
