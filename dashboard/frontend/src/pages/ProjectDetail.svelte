<script lang="ts">
  import { getProject, updateProject, deleteProject, listRuns } from '../lib/api';
  import { navigate } from '../lib/router.svelte';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import { formatCompact, formatDuration } from '../lib/chart-utils';
  import type { Project, Run, AutonomyLevel } from '../lib/types';
  import Toggle from '../components/forms/Toggle.svelte';
  import VisionTab from '../components/vision/VisionTab.svelte';

  let { projectId = '' }: { projectId: string } = $props();

  let activeTab = $state<'overview' | 'vision' | 'runs'>('overview');

  let project = $state<Project | null>(null);
  let runs = $state<Run[]>([]);
  let loading = $state(true);

  $effect(() => {
    if (!projectId) return;
    load();
  });

  async function load() {
    loading = true;
    try {
      const id = parseInt(projectId);
      const [pRes, rRes] = await Promise.allSettled([
        getProject(id),
        listRuns({ project_id: id, limit: 20 }),
      ]);
      if (pRes.status === 'fulfilled') project = pRes.value;
      if (rRes.status === 'fulfilled') runs = rRes.value.runs;
    } catch { /* silent */ }
    loading = false;
  }

  async function save(field: string, value: any) {
    if (!project) return;
    try {
      await updateProject(project.id, { [field]: value });
      toastSuccess('Updated');
    } catch (e: any) { toastError(e.message); }
  }

  async function handleDelete() {
    if (!project || !confirm(`Delete project ${project.repo}?`)) return;
    try {
      await deleteProject(project.id);
      toastSuccess('Deleted');
      navigate('/projects');
    } catch (e: any) { toastError(e.message); }
  }
</script>

<div class="space-y-6 animate-fade-in-up max-w-3xl">
  {#if loading}
    <div class="text-sm text-tertiary">Loading...</div>
  {:else if !project}
    <div class="text-sm text-tertiary">Project not found</div>
  {:else}
    <div class="flex items-center justify-between">
      <h1 class="text-lg font-semibold text-primary">{project.repo}</h1>
      <button onclick={handleDelete} class="text-xs text-reject hover:text-reject/80 transition-colors">Delete</button>
    </div>

    <!-- Tab strip -->
    <div class="flex gap-1" style="border-bottom: 1px solid var(--color-border);">
      {#each ['overview', 'vision', 'runs'] as t}
        <button
          class="px-4 py-2.5 text-xs font-medium capitalize transition-colors cursor-pointer"
          style="{activeTab === t ? 'color: var(--color-primary); border-bottom: 2px solid var(--color-violet);' : 'color: var(--color-tertiary); border-bottom: 2px solid transparent;'}"
          onclick={() => activeTab = t as 'overview' | 'vision' | 'runs'}
        >{t}</button>
      {/each}
    </div>

    {#if activeTab === 'overview'}
      <!-- Work scope — what the agent is asked to do -->
      <div class="glass rounded-lg p-4 space-y-3">
        <div>
          <h2 class="text-xs font-semibold text-secondary uppercase tracking-wider">Work scope</h2>
          <p class="mt-1 text-[11px] leading-snug text-tertiary">
            What the agent is asked to do on this project. Shapes the prompt teammates receive and the criteria the manager applies.
          </p>
        </div>
        <div>
          <label class="text-xs text-tertiary mb-1 block">Mode</label>
          <select
            value={project.mode}
            onchange={(e) => { project!.mode = (e.target as HTMLSelectElement).value as any; save('mode', project!.mode); }}
            class="w-full px-3 py-2 rounded-lg bg-void text-primary text-sm border border-border focus:border-border-focus outline-none"
          >
            <option value="full">Full — plan and implement</option>
            <option value="analyze">Analyze — read-only investigation</option>
            <option value="plan">Plan — plan-quality output, source untouched</option>
            <option value="plan_only">Plan Only — write plan, stop, wait for approval</option>
          </select>
          <p class="mt-1 text-[11px] leading-snug text-tertiary">
            {#if project.mode === 'full'}
              Teammates write code, run tests, and push a feature branch for the manager to review.
            {:else if project.mode === 'analyze'}
              Teammates inspect code and write findings to a report file. No source changes, no branches, no commits.
            {:else if project.mode === 'plan'}
              Teammates produce inline-rich plans; the manager rejects any source modification.
            {:else if project.mode === 'plan_only'}
              Teammates write a plan and stop. The manager approves, requests revisions, or rejects before any code is written. An approved plan_only run schedules a follow-up <em>full</em> run that implements it.
            {/if}
          </p>
        </div>
      </div>

      <!-- Execution policy — how freely the agent can act -->
      <div class="glass rounded-lg p-4 space-y-3">
        <div>
          <h2 class="text-xs font-semibold text-secondary uppercase tracking-wider">Execution policy</h2>
          <p class="mt-1 text-[11px] leading-snug text-tertiary">
            How freely the agent can act on the work scope above. Independent of mode — applies to every tool call regardless of whether the run is implementing, planning, or analyzing.
          </p>
        </div>
        <div>
          <label class="text-xs text-tertiary mb-1 block">Autonomy level</label>
          <select
            value={project.autonomy_level ?? 'assisted'}
            onchange={(e) => { project!.autonomy_level = (e.target as HTMLSelectElement).value as AutonomyLevel; save('autonomy_level', project!.autonomy_level); }}
            class="w-full px-3 py-2 rounded-lg bg-void text-primary text-sm border border-border focus:border-border-focus outline-none"
          >
            <option value="manual">Manual — operator approves every edit and risky command</option>
            <option value="assisted">Assisted — edits auto-allowed, destructive commands ask</option>
            <option value="auto">Auto — only the always-deny list blocks the agent</option>
          </select>
          <p class="mt-1 text-[11px] leading-snug text-tertiary">
            {#if project.autonomy_level === 'manual'}
              Every <code>Edit</code> / <code>Write</code> and every destructive bash (<code>rm -rf</code>, force push, etc.) defers to the operator via the permission tray. Use for new or sensitive repos.
            {:else if project.autonomy_level === 'auto'}
              Mirrors Claude Code Auto Mode. Edits and most bash run without prompting; only the hard-coded always-deny list (push to <code>main</code>, fork bombs, <code>sudo</code>, etc.) is blocked.
            {:else}
              Edits auto-allowed; destructive bash patterns still defer to the operator. The default for established repos.
            {/if}
            Every decision is recorded to the autonomy audit, regardless of level.
          </p>
        </div>
      </div>

      <!-- Project basics -->
      <div class="glass rounded-lg p-4 space-y-4">
        <h2 class="text-xs font-semibold text-secondary uppercase tracking-wider">Project basics</h2>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label class="text-xs text-tertiary mb-1 block">Priority</label>
            <select
              value={project.priority}
              onchange={(e) => { project!.priority = (e.target as HTMLSelectElement).value as any; save('priority', project!.priority); }}
              class="w-full px-3 py-2 rounded-lg bg-void text-primary text-sm border border-border focus:border-border-focus outline-none"
            >
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </div>
          <div>
            <label class="text-xs text-tertiary mb-1 block">Branch</label>
            <input
              value={project.branch}
              onchange={(e) => { project!.branch = (e.target as HTMLInputElement).value; save('branch', project!.branch); }}
              class="w-full px-3 py-2 rounded-lg bg-void text-primary text-sm border border-border focus:border-border-focus outline-none"
            />
          </div>
          <div class="flex items-end">
            <Toggle checked={project.enabled} label="Enabled"
              onchange={(v) => { project!.enabled = v; save('enabled', v); }} />
          </div>
        </div>
      </div>
    {:else if activeTab === 'vision'}
      <VisionTab {project} />
    {:else if activeTab === 'runs'}
      <!-- Recent runs -->
      <div class="glass rounded-lg p-4">
        <h2 class="text-xs font-semibold text-secondary uppercase tracking-wider mb-3">Recent Runs</h2>
        {#if runs.length > 0}
          <div class="space-y-1">
            {#each runs as run}
              <button
                class="w-full flex items-center justify-between px-3 py-2 rounded hover:bg-surface-2/50 text-xs transition-colors"
                onclick={() => navigate(`/runs/${run.run_id}`)}
              >
                <span class="text-primary font-mono">{run.run_id}</span>
                <div class="flex items-center gap-3 text-tertiary">
                  {#if run.verdict}
                    <span class="{run.verdict === 'APPROVE' ? 'text-approve' : run.verdict === 'REJECT' ? 'text-reject' : 'text-pr'}">{run.verdict}</span>
                  {/if}
                  {#if run.tokens_total}<span class="font-mono">{formatCompact(run.tokens_total)}</span>{/if}
                </div>
              </button>
            {/each}
          </div>
        {:else}
          <div class="text-sm text-tertiary text-center py-4">No runs</div>
        {/if}
      </div>
    {/if}
  {/if}
</div>
