<script lang="ts">
  import {
    listProjects,
    listRuns,
    createProject,
    updateProject,
    listGitHubRepos,
    listGitHubBranches,
  } from '../lib/api';
  import { navigate } from '../lib/router.svelte';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import type { Project, Run, AgentMode, AutonomyLevel } from '../lib/types';
  import type { GitHubRepo, GitHubBranch } from '../lib/api';
  import Modal from '../components/overlays/Modal.svelte';
  import VisionChat from '../components/vision/VisionChat.svelte';

  // ── Project list state ────────────────────────────────
  let projects = $state<Project[]>([]);
  let loading = $state(true);

  // Per-project stats keyed by project id. Populated lazily from
  // listRuns(); stays empty if we can't fetch (so the row falls back
  // to em-dashes rather than crashing).
  type ProjectStats = {
    runs7d: number;
    runsToday: number;
    active: number;
    approved: number;
    pr: number;
    last: Run | null;
  };
  let stats = $state<Record<number, ProjectStats>>({});

  // ── Wizard state ─────────────────────────────────────
  let showCreateModal = $state(false);
  let wizardStep = $state<1 | 2>(1);
  let savedProjectId = $state<number | null>(null);
  let newRepo = $state('');
  let newBranch = $state('main');
  let newMode = $state<AgentMode>('full');
  let newPriority = $state('medium');

  let repos = $state<GitHubRepo[]>([]);
  let reposLoading = $state(false);
  let useCustomRepo = $state(false);

  let branches = $state<GitHubBranch[]>([]);
  let branchesLoading = $state(false);

  $effect(() => {
    if (!showCreateModal || useCustomRepo || !newRepo) {
      branches = [];
      return;
    }
    const picked = repos.find(r => r.full_name === newRepo);
    if (!picked) return;
    newBranch = picked.default_branch;
    loadBranches(picked.full_name);
  });

  async function loadBranches(repo: string) {
    branchesLoading = true;
    try {
      const res = await listGitHubBranches(repo);
      branches = res.branches;
    } catch {
      branches = [];
    } finally {
      branchesLoading = false;
    }
  }

  $effect(() => { loadProjects(); });

  async function loadProjects() {
    try {
      projects = await listProjects();
      // Fire stats fetches in parallel; ignore individual failures so
      // one slow/erroring project doesn't blank the whole list.
      await Promise.all(projects.map(p => loadStats(p.id)));
    } catch { /* silent */ }
    loading = false;
  }

  async function loadStats(projectId: number) {
    try {
      // Pull last 50 runs; enough for 7d telemetry on a healthy repo.
      const res = await listRuns({ project_id: projectId, limit: 50 });
      const now = Date.now();
      const sevenDays = 7 * 24 * 60 * 60 * 1000;
      const oneDay = 24 * 60 * 60 * 1000;

      let runs7d = 0;
      let runsToday = 0;
      let active = 0;
      let approved = 0;
      let pr = 0;
      let last: Run | null = null;

      for (const r of res.runs) {
        const ts = r.started_at ? Date.parse(r.started_at) : NaN;
        if (!Number.isNaN(ts)) {
          if (now - ts < sevenDays) runs7d += 1;
          if (now - ts < oneDay) runsToday += 1;
        }
        const status = (r.status ?? '').toLowerCase();
        // Active = any non-terminal pipeline state. Keep `running` to catch
        // anything that bypasses the canonical RunStatus values.
        if (
          status === 'started' ||
          status === 'running' ||
          status === 'reviewing' ||
          status === 'plan_reviewing' ||
          status === 'awaiting_plan_review'
        ) {
          active += 1;
        }
        if (r.verdict === 'APPROVE') approved += 1;
        if (r.verdict === 'PR') pr += 1;
        if (!last || (r.started_at && (!last.started_at || r.started_at > last.started_at))) {
          last = r;
        }
      }

      stats[projectId] = { runs7d, runsToday, active, approved, pr, last };
    } catch {
      // leave row without stats; the UI shows em-dashes.
    }
  }

  async function loadRepos() {
    reposLoading = true;
    try {
      const res = await listGitHubRepos();
      const existing = new Set(projects.map(p => p.repo));
      repos = res.repos.filter(r => !existing.has(r.full_name));
      useCustomRepo = repos.length === 0;
    } catch {
      repos = [];
      useCustomRepo = true;
    } finally {
      reposLoading = false;
    }
  }

  function openCreateModal() {
    showCreateModal = true;
    wizardStep = 1;
    savedProjectId = null;
    newRepo = '';
    newBranch = 'main';
    useCustomRepo = false;
    branches = [];
    loadRepos();
  }

  function closeWizard() {
    showCreateModal = false;
    wizardStep = 1;
    savedProjectId = null;
    newRepo = '';
    newBranch = 'main';
    loadProjects();
  }

  async function handleCreate() {
    if (!newRepo.trim()) return;
    try {
      const created = await createProject({
        repo: newRepo.trim(),
        branch: newBranch.trim() || 'main',
        mode: newMode,
        priority: newPriority,
      });
      toastSuccess('Project created');
      savedProjectId = created.id;
      wizardStep = 2;
    } catch (e: any) { toastError(e.message); }
  }

  // ── Derived totals for the page-head meta line ─────────
  let totalProjects = $derived(projects.length);
  let enabledCount = $derived(projects.filter(p => p.enabled).length);
  let totalRuns7d = $derived(
    Object.values(stats).reduce((sum, s) => sum + s.runs7d, 0),
  );

  // ── Helpers ────────────────────────────────────────────
  function modeClass(m: AgentMode): string {
    if (m === 'plan' || m === 'plan_only') return 'mode plan';
    if (m === 'analyze') return 'mode vision';
    return 'mode full';
  }
  function autClass(a: AutonomyLevel | null | undefined): string {
    if (a === 'auto') return 'aut auto';
    if (a === 'assisted') return 'aut assist';
    return 'aut manual';
  }
  function autLabel(a: AutonomyLevel | null | undefined): string {
    return (a ?? 'manual').toUpperCase();
  }
  function priClass(p: string): string {
    if (p === 'high' || p === 'critical') return 'pri high';
    if (p === 'medium') return 'pri med';
    return '';
  }
  function fmtRel(iso: string | null | undefined): string {
    if (!iso) return '—';
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return '—';
    const diff = Date.now() - t;
    if (diff < 60_000) return 'just now';
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
    return `${Math.floor(diff / 86_400_000)}d ago`;
  }
  function fmtDate(iso: string | null | undefined): string {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }
</script>

<div data-testid="projects-page" class="projects-pro animate-fade-in">

  <div class="page-head">
    <h1>Projects</h1>
    <div class="meta">
      {totalProjects} project{totalProjects === 1 ? '' : 's'}
      · {enabledCount} enabled
      · {totalRuns7d} runs / 7d
    </div>
    <div>
      <button class="opbtn primary" onclick={openCreateModal}>+ Add Project</button>
    </div>
  </div>

  {#if loading}
    <div class="empty-list">Loading projects…</div>
  {:else if projects.length === 0}
    <div class="empty-list">
      No projects yet. Add a GitHub repository to get started.
    </div>
  {:else}
    <div class="list">
      {#each projects as project (project.id)}
        {@const s = stats[project.id]}
        <a
          class="proj {project.enabled ? 'enabled' : 'disabled'}"
          href={`/projects/${project.id}`}
          onclick={(e) => {
            // Let the browser handle modifier-clicks (open in new tab/window)
            // and non-primary mouse buttons.
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
            e.preventDefault();
            navigate(`/projects/${project.id}`);
          }}
        >
          <div class="icon">▣</div>
          <div class="body">
            <div class="repo">
              <b>{project.repo}</b>
              <span class="branch">{project.branch}</span>
              <span class={modeClass(project.mode)}>{project.mode.toUpperCase()}</span>
              <span class={autClass(project.autonomy_level)}>{autLabel(project.autonomy_level)}</span>
            </div>
            <div class="row">
              <span>Priority <b class={priClass(project.priority)}>{project.priority.toUpperCase()}</b></span>
              <span>·</span>
              <span>Enabled <b style:color={project.enabled ? 'var(--go)' : 'var(--ash)'}>
                {project.enabled ? 'YES' : 'NO'}
              </b></span>
              <span>·</span>
              <span>Security review <b>{project.security_review_enabled ? 'ON' : 'OFF'}</b></span>
              <span>·</span>
              <span>Budget <b style:color={project.max_budget_usd == null ? 'var(--ash)' : 'var(--ink)'}>
                {project.max_budget_usd == null ? '—' : `$${project.max_budget_usd}`}
              </b></span>
              <span>·</span>
              <span>Created <b>{fmtDate(project.created_at)}</b></span>
            </div>
          </div>
          <div class="stats">
            <div class="stat">
              <span class="k">Runs · 7d</span>
              <span class="v">{s ? s.runs7d : '—'}</span>
              <span class="sub">{s ? `${s.runsToday} today` : ''}</span>
            </div>
            <div class="stat">
              <span class="k">Active</span>
              <span class={'v ' + (s && s.active > 0 ? 'go' : 'nu')}>
                {s ? (s.active > 0 ? s.active : '—') : '—'}
              </span>
              <span class="sub">{s && s.active > 0 ? 'live' : 'idle'}</span>
            </div>
            <div class="stat">
              <span class="k">Verdicts</span>
              <span class={'v ' + (s && (s.approved + s.pr) > 0 ? '' : 'nu')}>
                {s ? (s.approved + s.pr > 0 ? s.approved + s.pr : '—') : '—'}
              </span>
              <span class="sub">{s ? `${s.approved} OK / ${s.pr} PR` : '0 OK / 0 PR'}</span>
            </div>
            <div class="stat">
              <span class="k">Last</span>
              <span class="v" style="font-size: 12px; font-weight: 500">
                {s && s.last ? fmtRel(s.last.started_at) : '—'}
              </span>
              <span class="sub">
                {#if s && s.last}
                  {(s.last.status ?? '').toString()}
                {/if}
              </span>
            </div>
          </div>
        </a>
      {/each}
    </div>
  {/if}
</div>

<Modal show={showCreateModal} onClose={closeWizard} title={wizardStep === 1 ? 'Add Project' : 'Project Vision'}>
  {#if wizardStep === 1}
    <div class="space-y-3">
      {#if reposLoading}
        <div class="text-xs text-tertiary py-2">Loading repos from GitHub…</div>
      {:else if !useCustomRepo && repos.length > 0}
        <select bind:value={newRepo} class="input" data-testid="repo-select">
          <option value="" disabled>Pick a repo…</option>
          {#each repos as r (r.full_name)}
            <option value={r.full_name}>
              {r.full_name}{r.private ? ' (private)' : ''}
            </option>
          {/each}
        </select>
        <button
          type="button"
          onclick={() => { useCustomRepo = true; newRepo = ''; }}
          class="text-xs text-tertiary hover:text-secondary underline"
        >Or enter a repo manually</button>
      {:else}
        <input
          bind:value={newRepo}
          placeholder="owner/repo"
          class="input"
          data-testid="repo-input"
        />
        {#if repos.length > 0}
          <button
            type="button"
            onclick={() => { useCustomRepo = false; newRepo = ''; }}
            class="text-xs text-tertiary hover:text-secondary underline"
          >Pick from your GitHub repos</button>
        {:else}
          <div class="text-[11px] text-tertiary">
            No repos found via GitHub auth. Connect a GitHub App or PAT in
            <a href="/settings?tab=auth" class="text-accent-orange underline">Settings → Auth</a>
            to populate this list automatically.
          </div>
        {/if}
      {/if}

      <div>
        <label for="branch-input" class="text-[11px] uppercase tracking-widest text-tertiary block mb-1">
          Branch
        </label>
        {#if branchesLoading}
          <div class="text-xs text-tertiary py-2">Loading branches…</div>
        {:else if branches.length > 0}
          <select id="branch-input" bind:value={newBranch} class="input" data-testid="branch-select">
            {#each branches as b (b.name)}
              <option value={b.name}>
                {b.name}{b.protected ? ' (protected)' : ''}
              </option>
            {/each}
          </select>
        {:else}
          <input
            id="branch-input"
            bind:value={newBranch}
            placeholder="main"
            class="input"
            data-testid="branch-input"
          />
        {/if}
      </div>

      <div class="flex gap-3">
        <select bind:value={newMode} class="input">
          <option value="full">Full</option>
          <option value="analyze">Analyze</option>
          <option value="plan">Plan</option>
          <option value="plan_only">Plan Only</option>
        </select>
        <select bind:value={newPriority} class="input">
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
      </div>

      <div class="flex justify-end gap-2 mt-4">
        <button type="button" onclick={closeWizard} class="btn btn-ghost btn-sm">Cancel</button>
        <button type="button" onclick={handleCreate} disabled={!newRepo.trim() || !newBranch.trim()} class="btn btn-primary btn-sm">Next →</button>
      </div>
    </div>
  {:else if wizardStep === 2 && savedProjectId !== null}
    <p class="text-xs text-tertiary mb-3">
      Step 2 of 2 — Define the project's vision so Claude knows the end goal.
    </p>
    <VisionChat
      projectId={savedProjectId}
      onApproved={closeWizard}
      onCancelled={closeWizard}
    />
    <div class="flex justify-start mt-3">
      <button type="button" onclick={closeWizard} class="btn btn-ghost btn-sm text-xs">
        Skip for now
      </button>
    </div>
  {/if}
</Modal>


<style>
  /* Edge-to-edge container — flush against the strip/ticker. */
  .projects-pro {
    display: flex;
    flex-direction: column;
    min-height: calc(100vh - 40px);
    background: var(--paper);
    color: var(--ink);
    font-family: var(--pro-sans);
    background-image: radial-gradient(circle at 1px 1px, var(--dot) 1px, transparent 0);
    background-size: 24px 24px;
  }

  /* Page head ----------------------------------------------- */
  .projects-pro :global(.page-head) {
    display: grid;
    grid-template-columns: auto 1fr auto;
    align-items: center;
    gap: 18px;
    padding: 10px 16px;
    border-bottom: 1px solid var(--rule);
    background: var(--paper);
    flex-shrink: 0;
  }
  .projects-pro :global(.page-head h1) {
    margin: 0;
    font-family: var(--pro-sans);
    font-size: 14px; font-weight: 700;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--ink);
  }
  .projects-pro :global(.page-head .meta) {
    font-family: var(--pro-mono);
    font-size: 11px;
    color: var(--graphite);
  }
  .projects-pro :global(.opbtn) {
    font-family: var(--pro-sans);
    font-weight: 700; font-size: 10px;
    letter-spacing: 0.14em; text-transform: uppercase;
    background: transparent; color: var(--ink);
    border: 1px solid var(--rule-2);
    padding: 5px 11px; cursor: pointer; height: 26px;
    border-radius: 0;
  }
  .projects-pro :global(.opbtn:hover) { background: var(--paper-2); }
  .projects-pro :global(.opbtn.primary) {
    background: var(--ink);
    color: var(--paper);
    border-color: var(--ink);
  }
  .projects-pro :global(.opbtn.primary:hover) { filter: brightness(1.1); background: var(--ink); }

  /* List ---------------------------------------------------- */
  .projects-pro :global(.list) {
    flex: 1;
    padding: 16px;
    display: grid;
    grid-template-columns: 1fr;
    gap: 12px;
  }
  .projects-pro :global(.proj) {
    background: var(--paper-2);
    border: 1px solid var(--rule);
    display: grid;
    grid-template-columns: 36px 1fr auto;
    gap: 16px;
    align-items: center;
    padding: 14px 18px;
    cursor: pointer;
    text-decoration: none;
    color: var(--ink);
    transition: background-color 120ms ease, border-color 120ms ease;
  }
  .projects-pro :global(.proj:hover) {
    background: var(--paper-3);
    border-color: var(--rule-2);
  }
  .projects-pro :global(.proj.enabled) { border-left: 3px solid var(--go); }
  .projects-pro :global(.proj.disabled) {
    border-left: 3px solid var(--ash);
    opacity: 0.74;
  }

  .projects-pro :global(.proj .icon) {
    width: 36px; height: 36px;
    background: var(--paper);
    border: 1px solid var(--rule);
    display: grid; place-items: center;
    font-family: var(--pro-mono); font-size: 14px;
    color: var(--graphite);
  }

  .projects-pro :global(.proj .body .repo) {
    font-family: var(--pro-mono); font-size: 14px; color: var(--ink);
    display: flex; align-items: center; gap: 10px;
    flex-wrap: wrap;
  }
  .projects-pro :global(.proj .body .repo b) { font-weight: 600; }
  .projects-pro :global(.proj .body .repo .branch) {
    font-family: var(--pro-mono);
    font-size: 11px;
    color: var(--graphite);
    border: 1px solid var(--rule);
    padding: 1px 6px;
  }
  .projects-pro :global(.proj .body .row) {
    margin-top: 6px;
    display: flex; gap: 14px; flex-wrap: wrap;
    font-family: var(--pro-mono); font-size: 11px;
    color: var(--graphite);
  }
  .projects-pro :global(.proj .body .row b) { color: var(--ink); font-weight: 500; }
  .projects-pro :global(.proj .body .row .pri.high) { color: var(--abort); }
  .projects-pro :global(.proj .body .row .pri.med) { color: var(--caution); }

  .projects-pro :global(.proj .stats) {
    display: grid;
    grid-template-columns: repeat(4, auto);
    gap: 0;
  }
  .projects-pro :global(.proj .stat) {
    padding: 0 18px;
    border-right: 1px solid var(--rule);
    display: flex; flex-direction: column; align-items: flex-end; gap: 2px;
    min-width: 88px;
  }
  .projects-pro :global(.proj .stat:last-child) {
    border-right: none;
    padding-right: 0;
  }
  .projects-pro :global(.proj .stat .k) {
    font-family: var(--pro-sans);
    font-size: 9px; font-weight: 700;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--graphite);
  }
  .projects-pro :global(.proj .stat .v) {
    font-family: var(--pro-mono);
    font-size: 16px; font-weight: 600;
    color: var(--ink);
    line-height: 1;
    font-variant-numeric: tabular-nums;
  }
  .projects-pro :global(.proj .stat .v.go) { color: var(--go); }
  .projects-pro :global(.proj .stat .v.caution) { color: var(--caution); }
  .projects-pro :global(.proj .stat .v.abort) { color: var(--abort); }
  .projects-pro :global(.proj .stat .v.nu) { color: var(--ash); }
  .projects-pro :global(.proj .stat .sub) {
    font-family: var(--pro-mono);
    font-size: 9px;
    color: var(--ash);
  }

  /* Empty / loading ----------------------------------------- */
  .projects-pro :global(.empty-list) {
    font-family: var(--pro-mono);
    font-size: 13px;
    color: var(--ash);
    text-align: center;
    padding: 60px 14px;
    border: 1px dashed var(--rule);
    margin: 16px;
  }

  /* Stack stats below body on narrow viewports -------------- */
  @media (max-width: 900px) {
    .projects-pro :global(.proj) {
      grid-template-columns: 36px 1fr;
    }
    .projects-pro :global(.proj .stats) {
      grid-column: 1 / -1;
      grid-template-columns: repeat(4, 1fr);
      border-top: 1px solid var(--rule);
      padding-top: 10px;
      margin-top: 4px;
    }
    .projects-pro :global(.proj .stat) {
      min-width: 0;
      padding: 0 8px;
      align-items: flex-start;
    }
  }
</style>
