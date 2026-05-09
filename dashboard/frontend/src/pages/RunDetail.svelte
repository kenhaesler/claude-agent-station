<script lang="ts">
  import { getRunFullContext, getRunDiff, getCoordinatorMessages } from '../lib/api';
  import { formatTokens, formatDuration, timeAgo, formatRunMode, formatSkipReason } from '../lib/format';
  import { getAgentName, getAgentColor } from '../lib/agent-presence.svelte';
  import type { RunFullContext, DiffResult, CoordinatorMessage } from '../lib/types';
  import { navigate } from '../lib/router.svelte';
  import LogViewer from '../components/data-display/LogViewer.svelte';
  import AutonomyBadge from '../components/badges/AutonomyBadge.svelte';

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
  <div class="space-y-6 animate-fade-in run-detail-pro">
    <!-- ===== HEADER (Pro) ===== -->
    <div class="rd-head">
      <div class="rd-head-left">
        <div class="rd-title-row">
          <span class="rd-status-dot {run.status === 'started' || run.status === 'running' ? 'go' : run.verdict === 'REJECT' ? 'abort' : run.verdict ? 'go' : ''} {run.status === 'started' || run.status === 'running' ? 'live' : ''}"></span>
          <h1 class="rd-runid">{run.run_id}</h1>
          {#if run.verdict}
            <span class="rd-pill verdict-{run.verdict.toLowerCase()}">{run.verdict}</span>
          {:else if run.status === 'started' || run.status === 'running'}
            <span class="rd-pill run">RUN</span>
          {/if}
          <AutonomyBadge level={run.autonomy_level} />
          {#if run.max_budget_usd != null}
            <span class="rd-meta-chip" title="Per-run budget cap">≤ ${run.max_budget_usd.toFixed(2)}</span>
          {/if}
        </div>
        <div class="rd-meta-row">
          {#if ctx?.project_repo}
            <span><b>{ctx.project_repo}</b></span>
          {/if}
          {#if run.issue_number}
            <span class="sep">·</span>
            <span>#{run.issue_number}</span>
          {/if}
          {#if run.mode}
            {@const m = formatRunMode(run.mode)}
            <span class="sep">·</span>
            <span class="rd-pill mode mode-{run.mode}">{m.icon} {m.label}</span>
          {/if}
          {#if run.skip_reason}
            <span class="sep">·</span>
            <span class="rd-skip">{formatSkipReason(run.skip_reason)}</span>
          {/if}
          {#if run.model}
            <span class="sep">·</span>
            <span>{run.model}</span>
          {/if}
        </div>
      </div>

      <!-- Stat chips (Pro) -->
      <div class="rd-stats">
        {#if run.tokens_total}
          <div class="rd-stat">
            <span class="k">Tokens</span>
            <span class="v">{formatTokens(run.tokens_total)}</span>
          </div>
        {/if}
        {#if run.duration_ms}
          <div class="rd-stat">
            <span class="k">Duration</span>
            <span class="v">{formatDuration(run.duration_ms)}</span>
          </div>
        {/if}
        {#if run.turns}
          <div class="rd-stat">
            <span class="k">Turns</span>
            <span class="v">{run.turns}</span>
          </div>
        {/if}
      </div>
    </div>

    <!-- ===== TABS (Pro) ===== -->
    <div class="rd-tabs">
      {#each tabs as tab}
        <button
          onclick={() => activeTab = tab.id as typeof activeTab}
          class="rd-tab"
          class:active={activeTab === tab.id}
        >
          {tab.label}
        </button>
      {/each}
    </div>

    <!-- ===== TAB CONTENT ===== -->
    {#if activeTab === 'overview'}
      {#if run.mode === 'vision-bootstrap'}
        <!-- Vision-bootstrap runs don't produce employee_report / verdict;
             they propose new issues directly on GitHub. Render a dedicated
             summary card so operators see what the run actually did
             instead of "Employee did not produce a report". -->
        <div class="card p-5 space-y-3 mb-6" data-testid="vision-bootstrap-summary">
          <div class="flex items-center justify-between">
            <h3 class="text-xs font-mono uppercase tracking-widest text-tertiary">Vision bootstrap</h3>
            <span class="text-[10px] font-mono text-tertiary">
              {#if run.status === 'completed'}
                {run.vision_bootstrap_count ?? 0} issue{(run.vision_bootstrap_count ?? 0) === 1 ? '' : 's'} proposed
              {:else if run.status === 'failed' || run.status === 'error'}
                Failed
              {:else}
                {run.status}
              {/if}
            </span>
          </div>

          {#if run.status === 'failed' || run.status === 'error'}
            <p class="text-sm text-secondary">
              The vision analyst could not complete this run. Check the logs tab for the underlying error.
            </p>
          {:else if (run.vision_bootstrap_count ?? 0) === 0 && run.status === 'completed'}
            <p class="text-sm text-secondary">
              The analyst found no gaps to propose. The current repo state already covers the vision's near-term horizons.
            </p>
          {:else if run.vision_bootstrap_proposals && run.vision_bootstrap_proposals.length > 0}
            <p class="text-sm text-secondary leading-snug">
              These issues carry the <code class="font-mono text-xs">vision-suggested</code> label and are skipped by the orchestrator until you accept one — remove the label to allow autonomous implementation, or close to reject.
            </p>
            <ul class="space-y-2">
              {#each run.vision_bootstrap_proposals as p}
                <li class="flex items-start gap-2 text-sm">
                  <span class="font-mono text-tertiary text-xs pt-0.5">#{p.number}</span>
                  <a
                    href={p.url}
                    target="_blank"
                    rel="noopener"
                    class="text-primary hover:underline flex-1"
                  >
                    {p.title}
                  </a>
                  <a
                    href={p.url}
                    target="_blank"
                    rel="noopener"
                    class="text-tertiary hover:text-primary text-xs pt-0.5"
                    aria-label="Open on GitHub"
                  >
                    ↗
                  </a>
                </li>
              {/each}
            </ul>
          {:else if run.status === 'started' || run.status === 'running'}
            <p class="text-sm text-tertiary">
              Analyst is running — proposed issues will appear here once the run finishes.
            </p>
          {/if}
        </div>
      {/if}

      {#if run.mode !== 'vision-bootstrap'}
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

          {#if ctx?.queue_items && ctx.queue_items.length > 0}
            <div class="card p-5 space-y-2">
              <h3 class="text-xs font-mono uppercase tracking-widest text-tertiary">
                Queue Items ({ctx.queue_items.length})
              </h3>
              <div class="space-y-2">
                {#each ctx.queue_items as qi}
                  <div class="text-xs space-y-1 text-secondary font-mono border-t border-border first:border-t-0 pt-2 first:pt-0">
                    <div class="flex items-center gap-2">
                      <span>#{qi.issue_number ?? '–'}</span>
                      <span class="badge badge-{qi.state}">{qi.state}</span>
                      <span class="text-tertiary">{qi.mode ?? '–'}</span>
                    </div>
                    {#if qi.issue_title}
                      <div class="text-secondary">{qi.issue_title}</div>
                    {/if}
                  </div>
                {/each}
              </div>
            </div>
          {:else if ctx?.queue_item}
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
                  <div class="h-full rounded-l-full" style="width: {(run.tokens_input / run.tokens_total) * 100}%; background: rgba(99,102,180,0.4)"></div>
                {/if}
                {#if run.tokens_output && run.tokens_total}
                  <div class="h-full rounded-r-full" style="width: {(run.tokens_output / run.tokens_total) * 100}%; background: rgba(176,96,48,0.4)"></div>
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
      {/if}

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
            <span style="color: #B06030;">{ctx.team_summary.tasks_in_progress} active</span>
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
            {#if event.event_type === 'vision_misalignment'}
              <div class="card p-3" style="border-left: 3px solid #B06030;">
                <div class="flex items-center gap-2 text-xs text-[#B06030] font-semibold mb-1">
                  ⚠ Vision misalignment — issue #{event.event_data?.issue_number}
                </div>
                <div class="text-xs text-secondary mb-1">
                  Violated: <code class="text-accent-orange">{event.event_data?.violated_section}</code>
                </div>
                <blockquote class="text-xs text-tertiary italic border-l-2 border-tertiary/40 pl-2 my-1">
                  "{event.event_data?.quote}"
                </blockquote>
                <div class="text-xs text-tertiary">Plan excerpt: {event.event_data?.plan_excerpt}</div>
                <div class="text-[10px] font-mono text-ghost mt-1">{timeAgo(event.created_at)}</div>
              </div>
            {:else}
              <div class="px-3 py-2 rounded-lg bg-surface-1 border border-border">
                <div class="flex items-center justify-between mb-1">
                  <span class="text-xs font-mono text-cyan">{event.event_type}</span>
                  <span class="text-[10px] font-mono text-ghost">{timeAgo(event.created_at)}</span>
                </div>
                <pre class="text-[11px] font-mono text-tertiary overflow-x-auto">{JSON.stringify(event.event_data, null, 2)}</pre>
              </div>
            {/if}
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

<style>
  /* Pro restyle for the page header + tabs only.
     Tab content keeps existing app.css tokens — its `card`, `badge`,
     `text-primary`, etc. classes still resolve correctly. */
  .run-detail-pro :global(.rd-head) {
    display: flex; flex-direction: column;
    gap: 16px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--rule);
  }
  @media (min-width: 640px) {
    .run-detail-pro :global(.rd-head) {
      flex-direction: row;
      align-items: flex-end;
      justify-content: space-between;
    }
  }

  .run-detail-pro :global(.rd-title-row) {
    display: flex; flex-wrap: wrap; align-items: center; gap: 10px;
    margin-bottom: 6px;
  }
  .run-detail-pro :global(.rd-runid) {
    margin: 0;
    font-family: var(--pro-mono);
    font-size: 22px;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: var(--ink);
    word-break: break-all;
  }
  .run-detail-pro :global(.rd-status-dot) {
    display: inline-block; width: 8px; height: 8px;
    background: var(--ash);
    flex-shrink: 0;
  }
  .run-detail-pro :global(.rd-status-dot.go)    { background: var(--go); }
  .run-detail-pro :global(.rd-status-dot.abort) { background: var(--abort); }
  .run-detail-pro :global(.rd-status-dot.live)  { animation: rdlive 1.6s steps(2) infinite; }
  @keyframes rdlive { 50% { opacity: .35; } }

  .run-detail-pro :global(.rd-pill) {
    font-family: var(--pro-sans);
    font-weight: 700; font-size: 9px;
    letter-spacing: 0.14em; text-transform: uppercase;
    padding: 3px 6px;
    border: 1px solid currentColor;
    color: var(--graphite);
    line-height: 1;
    display: inline-block;
    white-space: nowrap;
  }
  .run-detail-pro :global(.rd-pill.run)              { color: var(--go); }
  .run-detail-pro :global(.rd-pill.verdict-approve)  { color: var(--go); }
  .run-detail-pro :global(.rd-pill.verdict-pr)       { color: var(--caution); }
  .run-detail-pro :global(.rd-pill.verdict-reject)   { color: var(--abort); }
  .run-detail-pro :global(.rd-pill.verdict-skip)     { color: var(--graphite); }
  .run-detail-pro :global(.rd-pill.mode)             { color: var(--ink); border-color: var(--rule-2); }
  .run-detail-pro :global(.rd-pill.mode-plan_only)   { color: var(--data); border-color: color-mix(in oklab, var(--data) 60%, transparent); }
  .run-detail-pro :global(.rd-pill.mode-vision-bootstrap) { color: var(--graphite); }

  .run-detail-pro :global(.rd-meta-chip) {
    font-family: var(--pro-mono);
    font-size: 11px;
    color: var(--graphite);
    border: 1px solid var(--rule);
    padding: 1px 6px;
  }

  .run-detail-pro :global(.rd-meta-row) {
    display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
    font-family: var(--pro-mono);
    font-size: 11px;
    color: var(--graphite);
  }
  .run-detail-pro :global(.rd-meta-row b)   { color: var(--ink); font-weight: 500; }
  .run-detail-pro :global(.rd-meta-row .sep){ color: var(--ash); }
  .run-detail-pro :global(.rd-skip) { font-style: italic; color: var(--caution); }

  .run-detail-pro :global(.rd-stats) {
    display: flex; align-items: center; gap: 0;
    border: 1px solid var(--rule);
    background: var(--paper-2);
  }
  .run-detail-pro :global(.rd-stat) {
    padding: 6px 14px;
    border-right: 1px solid var(--rule);
    display: flex; flex-direction: column; gap: 2px;
    min-width: 80px;
  }
  .run-detail-pro :global(.rd-stat:last-child) { border-right: none; }
  .run-detail-pro :global(.rd-stat .k) {
    font-family: var(--pro-sans);
    font-size: 9px; font-weight: 700;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--graphite);
  }
  .run-detail-pro :global(.rd-stat .v) {
    font-family: var(--pro-mono);
    font-size: 14px; font-weight: 600;
    color: var(--ink); line-height: 1;
    font-variant-numeric: tabular-nums;
  }

  /* Tabs */
  .run-detail-pro :global(.rd-tabs) {
    display: flex; gap: 0;
    border-bottom: 1px solid var(--rule);
  }
  .run-detail-pro :global(.rd-tab) {
    font-family: var(--pro-sans);
    font-size: 11px; font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase;
    color: var(--graphite);
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 10px 14px;
    cursor: pointer;
    white-space: nowrap;
  }
  .run-detail-pro :global(.rd-tab:hover) { color: var(--ink); }
  .run-detail-pro :global(.rd-tab.active) {
    color: var(--ink);
    border-bottom-color: var(--ink);
  }
</style>
