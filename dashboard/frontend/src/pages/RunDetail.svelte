<script lang="ts">
  import { getRunFullContext, getRunDiff, getCoordinatorMessages } from '../lib/api';
  import { formatTokens, formatDuration, timeAgo } from '../lib/format';
  import { getAgentName, getAgentColor } from '../lib/agent-presence.svelte';
  import type { RunFullContext, DiffResult, CoordinatorMessage } from '../lib/types';
  import { navigate } from '../lib/router.svelte';
  import LogViewer from '../components/data-display/LogViewer.svelte';

  let { runId }: { runId: string } = $props();

  let ctx = $state<RunFullContext | null>(null);
  let diff = $state<DiffResult | null>(null);
  let allMessages = $state<CoordinatorMessage[]>([]);
  let loading = $state(true);
  let activeTab = $state<'overview' | 'dag' | 'team' | 'conversation' | 'diff' | 'logs' | 'intelligence'>('overview');
  let error = $state<string | null>(null);

  async function loadRun() {
    loading = true;
    error = null;
    try {
      ctx = await getRunFullContext(runId);
      // Load diff and messages in background
      getRunDiff(runId).then(d => diff = d).catch(() => {});
      getCoordinatorMessages(runId).then(m => allMessages = m).catch(() => {});
    } catch (e: any) {
      error = e.message ?? 'Failed to load run';
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    runId;
    loadRun();
  });

  let run = $derived(ctx?.run);
  let hasDag = $derived((ctx?.coordinator_tasks?.length ?? 0) > 0);
  let hasTeam = $derived(ctx?.team_summary !== null);
  let hasIntelligence = $derived((ctx?.intelligence_decisions?.length ?? 0) > 0);
  let hasConversation = $derived(allMessages.length > 0 || (ctx?.coordinator_messages?.length ?? 0) > 0);
  let conversationMessages = $derived(allMessages.length > 0 ? allMessages : (ctx?.coordinator_messages ?? []));

  let hasDiff = $derived((diff?.files?.length ?? 0) > 0 || !!run?.branch);
  let hasLogs = $derived(!!run?.log_file);

  let tabs = $derived.by(() => {
    const t: { id: string; label: string }[] = [{ id: 'overview', label: 'Overview' }];
    if (hasDag) t.push({ id: 'dag', label: 'DAG' });
    if (hasTeam) t.push({ id: 'team', label: 'Team' });
    if (hasConversation) t.push({ id: 'conversation', label: 'Conversation' });
    if (hasDiff) t.push({ id: 'diff', label: 'Diff' });
    if (hasLogs) t.push({ id: 'logs', label: 'Logs' });
    if (hasIntelligence) t.push({ id: 'intelligence', label: 'Intelligence' });
    return t;
  });

  function getInitials(name: string): string {
    return name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
  }

  function getMsgBorderColor(type: string): string {
    const map: Record<string, string> = { guidance: 'var(--color-cyan)', conflict: 'var(--color-amber)', progress: 'var(--color-emerald)', error: 'var(--color-rose)' };
    return map[type] ?? 'var(--color-tertiary)';
  }

  function getVerdictBadge(verdict: string | null): string {
    const map: Record<string, string> = { 'APPROVE': 'badge-approve', 'PR': 'badge-pr', 'REJECT': 'badge-reject' };
    return verdict ? map[verdict] ?? '' : '';
  }

  function parseReport(report: string | null): Record<string, unknown> | null {
    if (!report) return null;
    try { return JSON.parse(report); } catch { return null; }
  }
</script>

{#if loading}
  <div class="space-y-4 animate-fade-in">
    <div class="skeleton h-8 w-64"></div>
    <div class="skeleton h-48 w-full"></div>
  </div>
{:else if error}
  <div class="card p-12 text-center">
    <p class="text-rose text-sm mb-4">{error}</p>
    <button onclick={() => navigate('/runs')} class="btn btn-secondary">Back to Runs</button>
  </div>
{:else if run}
  <div class="space-y-6 animate-fade-in">
    <!-- ===== HEADER ===== -->
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
      <div>
        <div class="flex items-center gap-3 mb-2">
          <span class="status-dot {run.status === 'started' ? 'running' : run.verdict === 'REJECT' ? 'error' : run.verdict ? 'online' : 'offline'}"></span>
          <h1 class="font-heading text-xl">{run.run_id?.slice(0, 20)}</h1>
          {#if run.verdict}
            <span class="badge {getVerdictBadge(run.verdict)}">{run.verdict}</span>
          {:else if run.status === 'started'}
            <span class="badge badge-running">LIVE</span>
          {/if}
        </div>
        <div class="flex items-center gap-4 text-xs text-tertiary font-mono">
          {#if ctx?.project_repo}
            <span>{ctx.project_repo}</span>
          {/if}
          {#if run.issue_number}
            <span>#{run.issue_number}</span>
          {/if}
          {#if run.mode}
            <span class="badge badge-{run.mode}">{run.mode}</span>
          {/if}
          {#if run.model}
            <span>{run.model}</span>
          {/if}
        </div>
      </div>

      <!-- Stats chips -->
      <div class="flex items-center gap-3">
        {#if run.tokens_total}
          <div class="card px-3 py-1.5">
            <span class="text-[10px] text-tertiary font-mono">TOKENS</span>
            <span class="block font-mono text-sm text-primary font-medium">{formatTokens(run.tokens_total)}</span>
          </div>
        {/if}
        {#if run.duration_ms}
          <div class="card px-3 py-1.5">
            <span class="text-[10px] text-tertiary font-mono">DURATION</span>
            <span class="block font-mono text-sm text-primary font-medium">{formatDuration(run.duration_ms)}</span>
          </div>
        {/if}
        {#if run.turns}
          <div class="card px-3 py-1.5">
            <span class="text-[10px] text-tertiary font-mono">TURNS</span>
            <span class="block font-mono text-sm text-primary font-medium">{run.turns}</span>
          </div>
        {/if}
      </div>
    </div>

    <!-- ===== TABS ===== -->
    <div class="flex items-center gap-1 border-b border-border">
      {#each tabs as tab}
        <button
          onclick={() => activeTab = tab.id as typeof activeTab}
          class="px-4 py-2.5 text-sm font-medium transition-colors relative
                 {activeTab === tab.id ? 'text-cyan' : 'text-secondary hover:text-primary'}"
        >
          {tab.label}
          {#if activeTab === tab.id}
            <span class="absolute bottom-0 left-2 right-2 h-0.5 bg-cyan rounded-full"></span>
          {/if}
        </button>
      {/each}
    </div>

    <!-- ===== TAB CONTENT ===== -->
    {#if activeTab === 'overview'}
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <!-- Employee Report -->
        <div class="card p-5 space-y-3">
          <h3 class="text-xs font-mono uppercase tracking-widest text-tertiary">Employee Report</h3>
          {#if parseReport(run.employee_report)}
            {@const report = parseReport(run.employee_report)!}
            <div class="space-y-3 text-sm">
              {#each Object.entries(report) as [key, val]}
                <div>
                  <span class="text-[10px] font-mono uppercase text-tertiary">{key.replace(/_/g, ' ')}</span>
                  <div class="text-secondary mt-0.5">
                    {#if typeof val === 'string'}
                      <p class="whitespace-pre-wrap">{val}</p>
                    {:else if Array.isArray(val)}
                      <ul class="list-disc list-inside space-y-0.5">
                        {#each val as item}<li>{typeof item === 'string' ? item : JSON.stringify(item)}</li>{/each}
                      </ul>
                    {:else}
                      <pre class="text-xs font-mono bg-surface-1 rounded p-2 overflow-x-auto">{JSON.stringify(val, null, 2)}</pre>
                    {/if}
                  </div>
                </div>
              {/each}
            </div>
          {:else if run.employee_report}
            <p class="text-sm text-secondary whitespace-pre-wrap">{run.employee_report}</p>
          {:else}
            <p class="text-sm text-tertiary">
              {#if run.status === 'started' || run.status === 'running'}
                Report will be available after the employee finishes
              {:else if run.status === 'reviewing'}
                Employee finished — report pending review
              {:else}
                Employee did not produce a report
              {/if}
            </p>
          {/if}
        </div>

        <!-- Verdict & Context -->
        <div class="space-y-4">
          {#if run.verdict_detail}
            <div class="card p-5 space-y-2">
              <h3 class="text-xs font-mono uppercase tracking-widest text-tertiary">Verdict Detail</h3>
              <p class="text-sm text-secondary whitespace-pre-wrap">{run.verdict_detail}</p>
            </div>
          {/if}

          {#if ctx?.queue_item}
            <div class="card p-5 space-y-2">
              <h3 class="text-xs font-mono uppercase tracking-widest text-tertiary">Queue Item</h3>
              <div class="text-xs space-y-1 text-secondary font-mono">
                <div>State: <span class="badge badge-{ctx.queue_item.state}">{ctx.queue_item.state}</span></div>
                <div>Priority: {ctx.queue_item.priority}</div>
                {#if ctx.queue_item.issue_title}
                  <div>Issue: {ctx.queue_item.issue_title}</div>
                {/if}
              </div>
            </div>
          {/if}

          {#if ctx?.plan}
            <div class="card p-5 space-y-2">
              <h3 class="text-xs font-mono uppercase tracking-widest text-tertiary">Plan</h3>
              <div class="text-sm text-primary font-medium">{ctx.plan.title}</div>
              <div class="text-xs text-tertiary font-mono">Status: {ctx.plan.status}</div>
            </div>
          {/if}

          <!-- Token Breakdown -->
          {#if run.tokens_input || run.tokens_output}
            <div class="card p-5 space-y-2">
              <h3 class="text-xs font-mono uppercase tracking-widest text-tertiary">Token Breakdown</h3>
              <div class="flex items-center gap-2 h-4 rounded-full overflow-hidden bg-surface-2">
                {#if run.tokens_input && run.tokens_total}
                  <div class="h-full bg-cyan/40 rounded-l-full" style="width: {(run.tokens_input / run.tokens_total) * 100}%"></div>
                {/if}
                {#if run.tokens_output && run.tokens_total}
                  <div class="h-full bg-violet/40 rounded-r-full" style="width: {(run.tokens_output / run.tokens_total) * 100}%"></div>
                {/if}
              </div>
              <div class="flex justify-between text-[10px] font-mono text-tertiary">
                <span>Input: {formatTokens(run.tokens_input ?? 0)}</span>
                <span>Output: {formatTokens(run.tokens_output ?? 0)}</span>
              </div>
            </div>
          {/if}
        </div>
      </div>

    {:else if activeTab === 'dag' && ctx?.coordinator_tasks}
      <div class="card p-5">
        <h3 class="text-xs font-mono uppercase tracking-widest text-tertiary mb-4">Task DAG</h3>
        <div class="space-y-2">
          {#each ctx.coordinator_tasks as task}
            <div class="flex items-center gap-3 px-3 py-2 rounded-lg bg-surface-1 border border-border">
              <span class="status-dot {task.status === 'completed' ? 'online' : task.status === 'running' ? 'running' : task.status === 'failed' ? 'error' : 'offline'}"></span>
              <div class="flex-1 min-w-0">
                <div class="text-sm text-primary truncate">{task.title ?? task.id}</div>
                {#if task.description}
                  <div class="text-xs text-tertiary truncate">{task.description}</div>
                {/if}
              </div>
              <span class="badge badge-{task.status === 'completed' ? 'completed' : task.status === 'running' ? 'running' : task.status === 'failed' ? 'failed' : 'pending'}">
                {task.status}
              </span>
            </div>
          {/each}
        </div>
      </div>

    {:else if activeTab === 'team' && ctx?.team_summary}
      <div class="space-y-4">
        <!-- Team Summary Bar -->
        <div class="card p-4 flex items-center gap-6">
          <div>
            <span class="text-xs text-tertiary font-mono">Team</span>
            <div class="text-sm font-heading font-semibold text-primary">{ctx.team_summary.team_name}</div>
          </div>
          <div class="flex-1 h-2 rounded-full bg-surface-2 overflow-hidden">
            <div class="h-full bg-emerald/60 rounded-full transition-all duration-500" style="width: {ctx.team_summary.tasks_total > 0 ? (ctx.team_summary.tasks_completed / ctx.team_summary.tasks_total) * 100 : 0}%"></div>
          </div>
          <div class="flex items-center gap-4 text-xs font-mono">
            <span class="text-emerald">{ctx.team_summary.tasks_completed} done</span>
            <span class="text-violet">{ctx.team_summary.tasks_in_progress} active</span>
            {#if ctx.team_summary.conflicts > 0}
              <span class="text-amber">{ctx.team_summary.conflicts} conflicts</span>
            {/if}
          </div>
        </div>

        <!-- Team Members Grid -->
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {#each ctx.team_summary.teammates as member}
            {@const memberColor = getAgentColor(member.name.toLowerCase())}
            <div class="card p-4 space-y-3">
              <div class="flex items-center gap-3">
                <div
                  class="w-10 h-10 rounded-full flex items-center justify-center text-xs font-bold"
                  style="background: {memberColor}15; color: {memberColor}; border: 2px solid {memberColor};"
                >
                  {getInitials(member.name)}
                </div>
                <div class="flex-1 min-w-0">
                  <div class="text-sm font-medium text-primary">{member.name}</div>
                  <div class="flex items-center gap-1.5">
                    <span class="status-dot {member.status === 'completed' ? 'online' : member.status === 'stuck' ? 'error' : 'running'}"></span>
                    <span class="text-xs text-tertiary capitalize">{member.status}</span>
                  </div>
                </div>
              </div>
              <div class="flex items-center gap-3 text-[10px] font-mono text-tertiary">
                {#if member.turns_used}<span>{member.turns_used} turns</span>{/if}
                {#if member.tokens_used}<span>{formatTokens(member.tokens_used)} tokens</span>{/if}
              </div>
              {#if member.files_touched && member.files_touched.length > 0}
                <div class="space-y-1">
                  <span class="text-[10px] font-mono text-tertiary">Files touched:</span>
                  {#each member.files_touched.slice(0, 3) as file}
                    <div class="text-[10px] font-mono text-secondary truncate">{file}</div>
                  {/each}
                  {#if member.files_touched.length > 3}
                    <div class="text-[10px] font-mono text-ghost">+{member.files_touched.length - 3} more</div>
                  {/if}
                </div>
              {/if}
            </div>
          {/each}
        </div>
      </div>

    {:else if activeTab === 'conversation'}
      <div class="card p-5 space-y-4">
        <h3 class="text-xs font-mono uppercase tracking-widest text-tertiary">Agent Conversation</h3>
        {#if conversationMessages.length === 0}
          <p class="text-sm text-tertiary text-center py-8">No conversation data for this run</p>
        {:else}
          <div class="space-y-3 max-h-[600px] overflow-y-auto">
            {#each conversationMessages as msg}
              {@const agentName = getAgentName(msg.employee_index, msg.direction === 'system' ? 'coordinator' : null)}
              {@const color = getAgentColor(agentName.toLowerCase())}
              <div class="flex items-start gap-3 py-2 pl-3 border-l-2 rounded-r-lg hover:bg-surface-1/30 transition-colors"
                style="border-color: {getMsgBorderColor(msg.message_type)};">
                <!-- Avatar -->
                <div
                  class="w-8 h-8 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0"
                  style="background: {color}20; color: {color};"
                >
                  {getInitials(agentName)}
                </div>
                <!-- Content -->
                <div class="flex-1 min-w-0">
                  <div class="flex items-center gap-2 mb-0.5">
                    <span class="text-xs font-semibold" style="color: {color};">{agentName}</span>
                    <span class="px-1.5 py-0.5 rounded text-[9px] font-mono bg-surface-2 text-tertiary">{msg.message_type}</span>
                    <span class="px-1.5 py-0.5 rounded text-[9px] font-mono bg-surface-2 text-ghost">{msg.direction.replace('_', ' ')}</span>
                    <span class="text-[10px] text-ghost font-mono ml-auto shrink-0">{timeAgo(msg.created_at)}</span>
                  </div>
                  <p class="text-sm text-secondary whitespace-pre-wrap break-words">{msg.content ?? ''}</p>
                </div>
              </div>
            {/each}
          </div>
        {/if}
      </div>

    {:else if activeTab === 'diff'}
      <div class="card p-5">
        <h3 class="text-xs font-mono uppercase tracking-widest text-tertiary mb-4">Code Changes</h3>
        {#if diff?.files && diff.files.length > 0}
          <div class="space-y-4">
            <div class="text-xs font-mono text-secondary">
              {diff.files.length} file{diff.files.length > 1 ? 's' : ''} changed,
              <span class="text-emerald">+{diff.total_additions}</span>
              <span class="text-rose">-{diff.total_deletions}</span>
            </div>
            {#each diff.files as file}
              <div class="border border-border rounded-lg overflow-hidden">
                <div class="px-3 py-2 bg-surface-1 border-b border-border flex items-center justify-between">
                  <span class="text-xs font-mono text-primary">{file.path}</span>
                  <span class="text-[10px] font-mono text-tertiary">
                    <span class="text-emerald">+{file.additions}</span>
                    <span class="text-rose ml-1">-{file.deletions}</span>
                  </span>
                </div>
                <div class="overflow-x-auto">
                  {#each file.hunks as hunk}
                    {#each hunk.lines as line}
                      <div class="px-3 py-0 text-xs font-mono whitespace-pre leading-5
                                  {line.type === 'add' ? 'bg-emerald/5 text-emerald' :
                                   line.type === 'delete' ? 'bg-rose/5 text-rose' :
                                   'text-secondary'}">
                        <span class="inline-block w-4 text-ghost mr-2 select-none">
                          {line.type === 'add' ? '+' : line.type === 'delete' ? '-' : ' '}
                        </span>{line.content}
                      </div>
                    {/each}
                  {/each}
                </div>
              </div>
            {/each}
          </div>
        {:else}
          <p class="text-sm text-tertiary text-center py-8">
            {#if run.status === 'started' || run.status === 'running'}
              Diff will be available after the run completes
            {:else if !run.branch}
              No branch recorded for this run
            {:else}
              No code changes detected
            {/if}
          </p>
        {/if}
      </div>

    {:else if activeTab === 'logs'}
      <div class="card p-5">
        <h3 class="text-xs font-mono uppercase tracking-widest text-tertiary mb-4">Logs</h3>
        <LogViewer runId={run.run_id} logFile={run.log_file ?? null} />
      </div>

    {:else if activeTab === 'intelligence' && ctx?.intelligence_decisions}
      <div class="card p-5">
        <h3 class="text-xs font-mono uppercase tracking-widest text-tertiary mb-4">Intelligence Decisions</h3>
        <div class="space-y-2">
          {#each ctx.intelligence_decisions as event}
            <div class="px-3 py-2 rounded-lg bg-surface-1 border border-border">
              <div class="flex items-center justify-between mb-1">
                <span class="text-xs font-mono text-cyan">{event.event_type}</span>
                <span class="text-[10px] font-mono text-ghost">{timeAgo(event.created_at)}</span>
              </div>
              <pre class="text-[11px] font-mono text-tertiary overflow-x-auto">{JSON.stringify(event.event_data, null, 2)}</pre>
            </div>
          {/each}
        </div>
      </div>
    {/if}

    <!-- Messages (always visible below tabs if present) -->
    {#if ctx?.coordinator_messages && ctx.coordinator_messages.length > 0}
      <div class="card p-5">
        <h3 class="text-xs font-mono uppercase tracking-widest text-tertiary mb-4">Messages</h3>
        <div class="space-y-2">
          {#each ctx.coordinator_messages as msg}
            <div class="flex items-start gap-3 px-3 py-2 rounded-lg bg-surface-1">
              <span class="badge badge-{msg.message_type === 'guidance' ? 'pr' : msg.message_type === 'conflict' ? 'reject' : msg.message_type === 'error' ? 'reject' : 'pending'}">
                {msg.message_type}
              </span>
              <div class="flex-1">
                <span class="text-xs text-secondary">{msg.content}</span>
                <span class="text-[10px] text-ghost ml-2">{timeAgo(msg.created_at)}</span>
              </div>
            </div>
          {/each}
        </div>
      </div>
    {/if}
  </div>
{/if}
