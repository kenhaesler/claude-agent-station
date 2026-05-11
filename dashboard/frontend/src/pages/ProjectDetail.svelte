<script lang="ts">
  import { getProject, updateProject, deleteProject, listRuns, triggerRun } from '../lib/api';
  import { navigate } from '../lib/router.svelte';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import { formatDuration as fmtMs, timeAgo } from '../lib/format';
  import { flap } from '../lib/design/flap';
  import type { Project, Run, AutonomyLevel, AgentMode } from '../lib/types';
  import VisionTab from '../components/vision/VisionTab.svelte';

  let { projectId = '' }: { projectId: string } = $props();

  type TabId = 'overview' | 'settings' | 'runs' | 'vision';
  let activeTab = $state<TabId>('overview');

  let project = $state<Project | null>(null);
  let runs = $state<Run[]>([]);
  let runsTotal = $state(0);
  let loading = $state(true);
  let triggering = $state(false);

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
        listRuns({ project_id: id, limit: 30 }),
      ]);
      if (pRes.status === 'fulfilled') project = pRes.value;
      if (rRes.status === 'fulfilled') {
        runs = rRes.value.runs;
        runsTotal = rRes.value.total;
      }
    } catch { /* silent */ }
    loading = false;
  }

  async function save<K extends keyof Project>(field: K, value: Project[K]) {
    if (!project) return;
    try {
      await updateProject(project.id, { [field]: value } as Partial<Project>);
      toastSuccess('Updated');
    } catch (e: any) { toastError(e.message); }
  }

  async function handleDelete() {
    if (!project || !confirm(`Remove project ${project.repo}?`)) return;
    try {
      await deleteProject(project.id);
      toastSuccess('Removed');
      navigate('/projects');
    } catch (e: any) { toastError(e.message); }
  }

  async function handleTrigger() {
    if (triggering) return;
    triggering = true;
    try {
      await triggerRun();
      toastSuccess('Run triggered');
      // Re-load runs shortly so the new run appears in the list
      setTimeout(load, 1500);
    } catch (e: any) { toastError(e.message); }
    finally { triggering = false; }
  }

  async function toggleEnabled() {
    if (!project) return;
    const next = !project.enabled;
    project.enabled = next;
    save('enabled', next);
  }

  // ── Run row mapping (mirrors CommandCenter) ──────────────
  type StatusBucket = { label: string; cls: string; tick: boolean };

  function statusFor(r: Run): StatusBucket {
    const s = (r.status ?? '').toLowerCase();
    if (s === 'running' || s === 'started') return { label: 'RUN', cls: 'run', tick: true };
    if (s === 'reviewing') return { label: 'REVIEW', cls: 'run', tick: true };
    if (s === 'plan_reviewing' || s === 'awaiting_plan_review') return { label: 'PLAN-RV', cls: 'planok', tick: false };
    if (s === 'plan_approved' || r.verdict === 'APPROVE' || r.verdict === 'PR') return { label: 'PLAN OK', cls: 'planok', tick: false };
    if (s === 'plan_rejected' || r.verdict === 'REJECT') return { label: 'PLAN ✗', cls: 'planx', tick: false };
    if (s === 'interrupted') return { label: 'STOP', cls: 'stop', tick: false };
    if (s === 'failed' || s === 'error') return { label: 'FAIL', cls: 'planx', tick: false };
    if (s === 'completed' || s === 'finished' || s === 'success') return { label: 'DONE', cls: 'done', tick: false };
    if (!r.status && !r.finished_at) return { label: 'IDLE', cls: 'idle', tick: false };
    return { label: (r.status ?? 'IDLE').toUpperCase().slice(0, 6), cls: 'idle', tick: false };
  }

  function modeFor(r: Run | { mode: AgentMode | string | null }): { label: string; cls: string } {
    const m = (r.mode ?? '').toString().toLowerCase();
    if (m === 'plan_only' || m === 'plan') return { label: 'PLAN', cls: 'plan' };
    if (m === 'analyze') return { label: 'ANLZ', cls: 'plan' };
    if (m === 'vision-bootstrap') return { label: 'VIS', cls: 'vision' };
    if (m === 'agent_teams') return { label: 'TEAMS', cls: 'full' };
    if (m === 'employee') return { label: 'EMP', cls: 'full' };
    if (m === 'manager') return { label: 'MGR', cls: 'full' };
    if (m === 'full') return { label: 'FULL', cls: 'full' };
    return { label: (m || '—').toUpperCase().slice(0, 4), cls: 'full' };
  }

  function autFor(level: AutonomyLevel | null | undefined): { label: string; cls: string } {
    const a = (level ?? '').toLowerCase();
    if (a === 'assisted') return { label: 'ASSIST', cls: 'assist' };
    if (a === 'auto') return { label: 'AUTO', cls: 'auto' };
    if (a === 'manual') return { label: 'MANUAL', cls: 'manual' };
    return { label: '—', cls: 'manual' };
  }

  function fmtTok(n: number | null | undefined): string {
    if (n == null) return '—';
    if (n < 1000) return String(n);
    return (n / 1000).toFixed(1) + 'K';
  }
  function fmtTurns(n: number | null | undefined): string {
    return n == null ? '—' : String(n);
  }
  function fmtDur(ms: number | null | undefined, status: string | null | undefined): string {
    if (ms == null) return (status ?? '').toLowerCase() === 'running' ? 'live' : '—';
    return fmtMs(ms);
  }
  function shortId(id: string): string {
    return id.replace(/^run-(vb-)?/, '…');
  }
  function headlineFor(r: Run): string {
    const m = (r.mode ?? '').toString().toLowerCase();
    if (m === 'vision-bootstrap') return 'vision bootstrap';
    if (m === 'plan_only' || m === 'plan') return 'plan-only run';
    if (r.issue_number) return `issue #${r.issue_number}`;
    return 'untitled · no issue';
  }

  // ── Stats derived from project + runs ────────────────────
  function within7d(r: Run): boolean {
    const t = r.started_at ?? r.finished_at;
    if (!t) return false;
    const ms = Date.parse(t);
    if (Number.isNaN(ms)) return false;
    return Date.now() - ms < 7 * 24 * 3600 * 1000;
  }
  function withinDay(r: Run): boolean {
    const t = r.started_at ?? r.finished_at;
    if (!t) return false;
    const ms = Date.parse(t);
    if (Number.isNaN(ms)) return false;
    return Date.now() - ms < 24 * 3600 * 1000;
  }

  let runs7d = $derived(runs.filter(within7d));
  let tokens7d = $derived(runs7d.reduce((acc, r) => acc + (r.tokens_total ?? 0), 0));
  let runsToday = $derived(runs.filter(withinDay).length);
  let activeCount = $derived(runs.filter(r => {
    const s = (r.status ?? '').toLowerCase();
    return s === 'running' || s === 'started' || s === 'reviewing';
  }).length);
  let lastRunRow = $derived(runs.length > 0 ? runs[0] : null);
  let lastRunAge = $derived(lastRunRow?.started_at ? timeAgo(lastRunRow.started_at) : '—');
  let lastRunStatus = $derived(lastRunRow ? statusFor(lastRunRow).label.toLowerCase() : 'none');
  let approveCount = $derived(runs.filter(r => r.verdict === 'APPROVE' || r.verdict === 'PR').length);
  let rejectCount = $derived(runs.filter(r => r.verdict === 'REJECT').length);
  let verdictsLabel = $derived(approveCount + rejectCount === 0 ? '—' : `${approveCount}/${approveCount + rejectCount}`);

  const TABS: TabId[] = ['overview', 'settings', 'runs', 'vision'];

  function activate(t: TabId) { activeTab = t; }

  function handleKey(e: KeyboardEvent) {
    const tgt = e.target as HTMLElement | null;
    if (tgt && (tgt.tagName === 'INPUT' || tgt.tagName === 'TEXTAREA' || tgt.tagName === 'SELECT')) return;
    // Number keys 1..6 are app-level navigation in App.svelte; we don't override.
    // Use [/] to cycle tabs locally on this page.
    if (e.key === ']') { activate(TABS[(TABS.indexOf(activeTab) + 1) % TABS.length]); }
    else if (e.key === '[') { activate(TABS[(TABS.indexOf(activeTab) + TABS.length - 1) % TABS.length]); }
  }

  function fmtDateTime(iso: string | null | undefined): string {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} · ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
</script>

<svelte:window onkeydown={handleKey} />

<div class="pd-pro animate-fade-in">
  {#if loading}
    <div class="pd-empty">Loading…</div>
  {:else if !project}
    <div class="pd-empty">Project not found</div>
  {:else}
    <!-- Crumb -->
    <div class="crumb">
      <a href="/projects">← Projects</a>
      <span class="sep">/</span>
      <b>{project.repo}</b>
    </div>

    <!-- Page head -->
    <div class="page-head">
      <h1>
        <span>{project.repo}</span>
        <span class="branch">{project.branch}</span>
        <span class="mode {modeFor({ mode: project.mode }).cls}">{modeFor({ mode: project.mode }).label}</span>
        <span class="aut {autFor(project.autonomy_level).cls}">{autFor(project.autonomy_level).label}</span>
        {#if activeCount > 0}
          <span class="status run"><span class="run-tick"></span><span class="lab">RUN</span></span>
        {:else if !project.enabled}
          <span class="status stop"><span class="lab">DISABLED</span></span>
        {:else}
          <span class="status idle"><span class="lab">IDLE</span></span>
        {/if}
      </h1>
      <div class="actions">
        <button class="opbtn" disabled={triggering || !project.enabled} onclick={handleTrigger}>
          ↻ {triggering ? 'Triggering…' : 'Trigger Run'}
        </button>
        <button class="opbtn" onclick={toggleEnabled}>
          {project.enabled ? '⏸ Disable' : '▶ Enable'}
        </button>
        <button class="opbtn danger" onclick={handleDelete}>Remove</button>
      </div>
    </div>

    <!-- Quick stats -->
    <div class="qstats">
      <div class="qstat">
        <span class="k">Runs · 7d</span>
        <span class="v"><span use:flap={{ text: String(runs7d.length), baseDelay: 0 }}></span></span>
        <span class="sub">{runsToday} today</span>
      </div>
      <div class="qstat">
        <span class="k">Tokens · 7d</span>
        <span class="v"><span use:flap={{ text: fmtTok(tokens7d), baseDelay: 30 }}></span></span>
        <span class="sub">cumulative</span>
      </div>
      <div class="qstat">
        <span class="k">Active</span>
        <span class="v {activeCount > 0 ? 'go' : 'nu'}">
          <span use:flap={{ text: String(activeCount), baseDelay: 60 }}></span>
        </span>
        <span class="sub">{activeCount === 1 ? 'run in flight' : 'runs in flight'}</span>
      </div>
      <div class="qstat">
        <span class="k">Total Runs</span>
        <span class="v"><span use:flap={{ text: String(runsTotal), baseDelay: 90 }}></span></span>
        <span class="sub">all time</span>
      </div>
      <div class="qstat">
        <span class="k">Verdicts</span>
        <span class="v {approveCount + rejectCount === 0 ? 'nu' : ''}">
          <span use:flap={{ text: verdictsLabel, baseDelay: 120 }}></span>
        </span>
        <span class="sub">{approveCount + rejectCount === 0 ? 'none yet' : 'approved / decided'}</span>
      </div>
      <div class="qstat">
        <span class="k">Last Run</span>
        <span class="v" style="font-size: 13px; font-weight: 500"><span use:flap={{ text: lastRunAge, baseDelay: 150 }}></span></span>
        <span class="sub">{lastRunStatus}</span>
      </div>
    </div>

    <!-- Tabs -->
    <div class="tabs" role="tablist">
      {#each TABS as t}
        <button
          class="tab"
          class:active={activeTab === t}
          role="tab"
          aria-selected={activeTab === t}
          onclick={() => activate(t)}
        >{t}{#if t === 'runs'} <span class="count">{runsTotal}</span>{/if}</button>
      {/each}
    </div>

    <div class="tab-body">
      {#if activeTab === 'overview'}
        <section class="tab-pane">
          <div class="cards">
            <div class="card-block">
              <h3>Recent Runs</h3>
              {#if runs.length === 0}
                <div class="empty">No runs yet</div>
              {:else}
                {#each runs.slice(0, 5) as r, i (r.run_id)}
                  {@const stat = statusFor(r)}
                  {@const md = modeFor(r)}
                  {@const aut = autFor(r.autonomy_level)}
                  {@const baseDelay = i * 60}
                  <a class="run-row" href={`/runs/${r.run_id}`}>
                    <span class="ix"><span use:flap={{ text: String(i + 1).padStart(2, '0'), baseDelay }}></span></span>
                    <span class="id"><span use:flap={{ text: shortId(r.run_id), baseDelay: baseDelay + 24 }}></span></span>
                    <span><span class="mode {md.cls}"><span use:flap={{ text: md.label, baseDelay: baseDelay + 48 }}></span></span></span>
                    <span><span class="aut {aut.cls}"><span use:flap={{ text: aut.label, baseDelay: baseDelay + 70 }}></span></span></span>
                    <span class="title"><span use:flap={{ text: headlineFor(r), baseDelay: baseDelay + 80 }}></span></span>
                    <span><span class="status {stat.cls}">{#if stat.tick}<span class="run-tick"></span>{/if}<span class="lab"><span use:flap={{ text: stat.label, baseDelay: baseDelay + 140 }}></span></span></span></span>
                    <span class="num {r.turns == null ? 'nu' : ''}"><span use:flap={{ text: fmtTurns(r.turns), baseDelay: baseDelay + 180 }}></span></span>
                    <span class="num {r.tokens_total == null ? 'nu' : ''}"><span use:flap={{ text: fmtTok(r.tokens_total), baseDelay: baseDelay + 210 }}></span></span>
                    <span class="num {r.duration_ms == null && (r.status ?? '').toLowerCase() !== 'running' ? 'nu' : ''}"><span use:flap={{ text: fmtDur(r.duration_ms, r.status), baseDelay: baseDelay + 240 }}></span></span>
                    <span class="num"><span use:flap={{ text: r.started_at ? timeAgo(r.started_at) : '—', baseDelay: baseDelay + 270 }}></span></span>
                  </a>
                {/each}
              {/if}
            </div>

            <div class="card-block">
              <h3>Project Snapshot</h3>
              <div class="key-row"><span class="key-label">Repo</span><span class="val">{project.repo}</span></div>
              <div class="key-row"><span class="key-label">Branch</span><span class="val">{project.branch}</span></div>
              {#if project.promotion_target}
                <div class="key-row"><span class="key-label">Promotion target</span><span class="val">{project.promotion_target}</span></div>
              {/if}
              <div class="key-row"><span class="key-label">Mode</span><span class="val">{project.mode}</span></div>
              <div class="key-row"><span class="key-label">Autonomy</span><span class="val">{project.autonomy_level}</span></div>
              <div class="key-row">
                <span class="key-label">Priority</span>
                <span class="val" style={project.priority === 'high' || project.priority === 'critical' ? 'color: var(--abort)' : ''}>{project.priority}</span>
              </div>
              <div class="key-row">
                <span class="key-label">Enabled</span>
                <span class="val" style={project.enabled ? 'color: var(--go)' : 'color: var(--ash)'}>
                  {project.enabled ? 'yes' : 'no'}
                </span>
              </div>
              <div class="key-row">
                <span class="key-label">Security review</span>
                <span class="val">{#if project.security_review_enabled}<span style="color: var(--go)">on</span>{:else}<span class="nu">off</span>{/if}</span>
              </div>
              <div class="key-row">
                <span class="key-label">Budget cap</span>
                <span class="val">{project.max_budget_usd != null ? `$${project.max_budget_usd}` : '—'}</span>
              </div>
              <div class="key-row"><span class="key-label">Created</span><span class="val">{fmtDateTime(project.created_at)}</span></div>
              <div class="key-row"><span class="key-label">Updated</span><span class="val">{fmtDateTime(project.updated_at)}</span></div>
            </div>
          </div>
        </section>

      {:else if activeTab === 'settings'}
        <section class="tab-pane">
          <div class="cards">
            <div class="card-block">
              <h3>General</h3>
              <div class="key-row">
                <span class="key-label">Repo</span>
                <span class="val"><input value={project.repo} readonly style="color: var(--graphite); background: var(--paper-3)" /></span>
              </div>
              <div class="key-row">
                <span class="key-label">Branch</span>
                <span class="val">
                  <input
                    value={project.branch}
                    onchange={(e) => { project!.branch = (e.currentTarget as HTMLInputElement).value; save('branch', project!.branch); }}
                  />
                </span>
              </div>
              <div class="key-row">
                <span class="key-label">Promotion target</span>
                <span class="val">
                  <input
                    data-testid="project-promotion-target"
                    placeholder="(defaults to branch)"
                    value={project.promotion_target ?? ''}
                    onchange={(e) => {
                      const v = (e.currentTarget as HTMLInputElement).value.trim();
                      project!.promotion_target = v || null;
                      save('promotion_target', project!.promotion_target);
                    }}
                  />
                  <small style="color: var(--graphite); display: block; margin-top: 4px;">
                    Branch the integration meta-PR opens against. Leave empty to fall back to <code>{project.branch}</code>.
                  </small>
                </span>
              </div>
              <div class="key-row">
                <span class="key-label">Priority</span>
                <span class="val">
                  <select
                    value={project.priority}
                    onchange={(e) => { project!.priority = (e.currentTarget as HTMLSelectElement).value as Project['priority']; save('priority', project!.priority); }}
                  >
                    <option value="critical">critical</option>
                    <option value="high">high</option>
                    <option value="medium">medium</option>
                    <option value="low">low</option>
                  </select>
                </span>
              </div>
              <div class="key-row">
                <span class="key-label">Default mode</span>
                <span class="val">
                  <select
                    value={project.mode}
                    onchange={(e) => { project!.mode = (e.currentTarget as HTMLSelectElement).value as AgentMode; save('mode', project!.mode); }}
                  >
                    <option value="full">full</option>
                    <option value="analyze">analyze</option>
                    <option value="plan">plan</option>
                    <option value="plan_only">plan_only</option>
                  </select>
                </span>
              </div>
              <div class="key-row">
                <span class="key-label">Enabled</span>
                <span class="val">
                  <label class="toggle">
                    <input type="checkbox" checked={project.enabled}
                      onchange={(e) => { const v = (e.currentTarget as HTMLInputElement).checked; project!.enabled = v; save('enabled', v); }} />
                    {project.enabled ? 'on — schedule active' : 'off — paused'}
                  </label>
                </span>
              </div>
            </div>

            <div class="card-block">
              <h3>Autonomy</h3>
              <div class="key-row">
                <span class="key-label">Level</span>
                <span class="val">
                  <select
                    value={project.autonomy_level ?? 'assisted'}
                    onchange={(e) => { const v = (e.currentTarget as HTMLSelectElement).value as AutonomyLevel; project!.autonomy_level = v; save('autonomy_level', v); }}
                  >
                    <option value="manual">manual</option>
                    <option value="assisted">assisted</option>
                    <option value="auto">auto</option>
                  </select>
                </span>
              </div>
              <div class="key-row">
                <span class="key-label">Max budget · USD</span>
                <span class="val">
                  <input
                    type="number"
                    placeholder="—"
                    value={project.max_budget_usd ?? ''}
                    onchange={(e) => {
                      const raw = (e.currentTarget as HTMLInputElement).value;
                      const v = raw === '' ? null : Number(raw);
                      project!.max_budget_usd = v;
                      save('max_budget_usd', v);
                    }} />
                </span>
              </div>
              <div class="key-row">
                <span class="key-label">Security review</span>
                <span class="val">
                  <label class="toggle">
                    <input type="checkbox" checked={project.security_review_enabled}
                      onchange={(e) => { const v = (e.currentTarget as HTMLInputElement).checked; project!.security_review_enabled = v; save('security_review_enabled', v); }} />
                    require manager security pass before merge
                  </label>
                </span>
              </div>
            </div>
          </div>

          <div class="cards" style="margin-top: 1px">
            <div class="card-block">
              <h3>Custom Instructions</h3>
              <textarea
                placeholder="Project-specific instructions appended to every prompt. Empty = defaults."
                value={project.custom_instructions ?? ''}
                onchange={(e) => { const v = (e.currentTarget as HTMLTextAreaElement).value; project!.custom_instructions = v || null; save('custom_instructions', project!.custom_instructions); }}
              ></textarea>
            </div>
            <div class="card-block">
              <h3>Setup Script</h3>
              <textarea
                placeholder="bash run before each agent session in the workspace. Empty = defaults."
                value={project.setup_script ?? ''}
                onchange={(e) => { const v = (e.currentTarget as HTMLTextAreaElement).value; project!.setup_script = v || null; save('setup_script', project!.setup_script); }}
              ></textarea>
            </div>
          </div>
        </section>

      {:else if activeTab === 'runs'}
        <section class="tab-pane">
          {#if runs.length === 0}
            <div class="empty">No runs yet for this project.</div>
          {:else}
            {#each runs as r, i (r.run_id)}
              {@const stat = statusFor(r)}
              {@const md = modeFor(r)}
              {@const aut = autFor(r.autonomy_level)}
              <a class="run-row" href={`/runs/${r.run_id}`}>
                <span class="ix"><span use:flap={{ text: String(i + 1).padStart(2, '0'), baseDelay: 0 }}></span></span>
                <span class="id"><span use:flap={{ text: shortId(r.run_id), baseDelay: 0 }}></span></span>
                <span><span class="mode {md.cls}"><span use:flap={{ text: md.label, baseDelay: 0 }}></span></span></span>
                <span><span class="aut {aut.cls}"><span use:flap={{ text: aut.label, baseDelay: 0 }}></span></span></span>
                <span class="title"><span use:flap={{ text: headlineFor(r), baseDelay: 0 }}></span></span>
                <span><span class="status {stat.cls}">{#if stat.tick}<span class="run-tick"></span>{/if}<span class="lab"><span use:flap={{ text: stat.label, baseDelay: 0 }}></span></span></span></span>
                <span class="num {r.turns == null ? 'nu' : ''}"><span use:flap={{ text: fmtTurns(r.turns), baseDelay: 0 }}></span></span>
                <span class="num {r.tokens_total == null ? 'nu' : ''}"><span use:flap={{ text: fmtTok(r.tokens_total), baseDelay: 0 }}></span></span>
                <span class="num {r.duration_ms == null && (r.status ?? '').toLowerCase() !== 'running' ? 'nu' : ''}"><span use:flap={{ text: fmtDur(r.duration_ms, r.status), baseDelay: 0 }}></span></span>
                <span class="num"><span use:flap={{ text: r.started_at ? timeAgo(r.started_at) : '—', baseDelay: 0 }}></span></span>
              </a>
            {/each}
          {/if}
        </section>

      {:else if activeTab === 'vision'}
        <section class="tab-pane vision-pane">
          <VisionTab {project} />
        </section>
      {/if}
    </div>
  {/if}
</div>

<style>
  /* Dense Pro detail page — edge-to-edge. Mirrors design-drafts/project-detail.html */

  .pd-pro {
    display: flex;
    flex-direction: column;
    width: 100%;
    color: var(--ink);
    background: var(--paper);
    font-family: var(--pro-sans);
    font-size: 13px;
  }

  .pd-empty {
    font-family: var(--pro-mono);
    font-size: 12px;
    color: var(--ash);
    padding: 24px 16px;
  }

  /* Crumb */
  .pd-pro :global(.crumb) {
    display: flex; align-items: center; gap: 12px;
    padding: 6px 16px; border-bottom: 1px solid var(--rule);
    font-family: var(--pro-mono); font-size: 11px; color: var(--graphite);
    flex-shrink: 0;
  }
  .pd-pro :global(.crumb a) { color: var(--graphite); text-decoration: none; }
  .pd-pro :global(.crumb a:hover) { color: var(--ink); }
  .pd-pro :global(.crumb b) { color: var(--ink); font-weight: 500; }
  .pd-pro :global(.crumb .sep) { color: var(--ash); }

  /* Page head */
  .pd-pro :global(.page-head) {
    display: grid; grid-template-columns: 1fr auto;
    align-items: end; gap: 18px;
    padding: 16px 16px 12px; border-bottom: 1px solid var(--rule);
    flex-shrink: 0;
  }
  .pd-pro :global(.page-head h1) {
    margin: 0;
    font-family: var(--pro-mono);
    font-size: 22px; font-weight: 600;
    letter-spacing: -0.01em; color: var(--ink);
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  }
  .pd-pro :global(.page-head h1 .branch) {
    font-family: var(--pro-mono); font-size: 11px;
    color: var(--graphite);
    border: 1px solid var(--rule);
    padding: 1px 6px;
  }
  .pd-pro :global(.page-head .actions) { display: flex; gap: 8px; }

  .pd-pro :global(.opbtn) {
    font-family: var(--pro-sans); font-weight: 700; font-size: 10px;
    letter-spacing: 0.14em; text-transform: uppercase;
    background: transparent; color: var(--ink);
    border: 1px solid var(--rule-2);
    padding: 5px 11px; cursor: pointer; height: 26px;
  }
  .pd-pro :global(.opbtn:hover:not(:disabled)) { background: var(--paper-2); }
  .pd-pro :global(.opbtn:disabled) { opacity: 0.5; cursor: not-allowed; }
  .pd-pro :global(.opbtn.danger) {
    color: var(--abort);
    border-color: color-mix(in oklab, var(--abort) 50%, transparent);
  }
  .pd-pro :global(.opbtn.danger:hover) {
    background: color-mix(in oklab, var(--abort) 12%, var(--paper));
  }

  /* Quick stats strip */
  .pd-pro :global(.qstats) {
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    border-bottom: 1px solid var(--rule);
  }
  .pd-pro :global(.qstat) {
    padding: 10px 14px;
    border-right: 1px solid var(--rule);
    display: flex; flex-direction: column; gap: 2px;
  }
  .pd-pro :global(.qstat:last-child) { border-right: none; }
  .pd-pro :global(.qstat .k) {
    font-family: var(--pro-sans); font-size: 9px; font-weight: 700;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--graphite);
  }
  .pd-pro :global(.qstat .v) {
    font-family: var(--pro-mono); font-size: 18px; font-weight: 600;
    color: var(--ink); line-height: 1; font-variant-numeric: tabular-nums;
  }
  .pd-pro :global(.qstat .v.go) { color: var(--go); }
  .pd-pro :global(.qstat .v.nu) { color: var(--ash); }
  .pd-pro :global(.qstat .sub) {
    font-family: var(--pro-mono); font-size: 9px; color: var(--ash);
  }

  /* Tabs */
  .pd-pro :global(.tabs) {
    display: flex; gap: 0; align-items: center;
    border-bottom: 1px solid var(--rule);
    background: var(--paper); padding: 0 16px;
    flex-shrink: 0;
  }
  .pd-pro :global(.tab) {
    font-family: var(--pro-sans); font-size: 11px; font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase;
    color: var(--graphite); background: transparent;
    border: none; border-bottom: 2px solid transparent;
    padding: 10px 14px; cursor: pointer; white-space: nowrap;
  }
  .pd-pro :global(.tab.active) { color: var(--ink); border-bottom-color: var(--ink); }
  .pd-pro :global(.tab:hover) { color: var(--ink); }
  .pd-pro :global(.tab .count) {
    font-family: var(--pro-mono); font-size: 10px;
    color: var(--ash); margin-left: 4px; font-weight: 500;
  }
  .pd-pro :global(.tab.active .count) { color: var(--graphite); }

  .pd-pro :global(.tab-body) { flex: 1; min-height: 0; }
  .pd-pro :global(.tab-pane) { padding: 16px; }

  /* Cards */
  .pd-pro :global(.cards) {
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 16px;
  }
  .pd-pro :global(.card-block) {
    background: var(--paper-2);
    border: 1px solid var(--rule);
    padding: 14px 16px;
  }
  .pd-pro :global(.card-block h3) {
    margin: 0 0 8px;
    font-family: var(--pro-sans); font-size: 10px; font-weight: 700;
    letter-spacing: 0.2em; text-transform: uppercase;
    color: var(--ash);
    border-bottom: 1px solid var(--rule);
    padding-bottom: 6px;
  }
  .pd-pro :global(.card-block .key-row) {
    display: grid; grid-template-columns: 130px 1fr;
    gap: 10px; padding: 5px 0;
    border-bottom: 1px dashed var(--rule);
    font-family: var(--pro-mono); font-size: 12px;
    align-items: center;
  }
  .pd-pro :global(.card-block .key-row:last-child) { border-bottom: none; }
  .pd-pro :global(.card-block .key-row .key-label) {
    color: var(--ash);
    font-family: var(--pro-sans); font-size: 9px; font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase;
    align-self: center;
  }
  .pd-pro :global(.card-block .key-row .val) { color: var(--ink); display: block; min-width: 0; }
  .pd-pro :global(.card-block .key-row .nu) { color: var(--ash); }

  .pd-pro :global(.card-block input),
  .pd-pro :global(.card-block select),
  .pd-pro :global(.card-block textarea) {
    font-family: var(--pro-mono); font-size: 12px;
    background: var(--paper); color: var(--ink);
    border: 1px solid var(--rule-2);
    padding: 4px 8px;
    width: 100%; max-width: 320px;
    border-radius: 0;
  }
  .pd-pro :global(.card-block textarea) {
    width: 100%; max-width: 100%; min-height: 90px; resize: vertical;
  }
  .pd-pro :global(.card-block input:focus),
  .pd-pro :global(.card-block select:focus),
  .pd-pro :global(.card-block textarea:focus) {
    outline: none; border-color: var(--ink);
  }
  .pd-pro :global(.toggle) {
    display: inline-flex; align-items: center; gap: 8px;
    font-family: var(--pro-mono); font-size: 12px;
  }
  .pd-pro :global(.toggle input) { width: auto; }

  /* Run rows */
  .pd-pro :global(.run-row) {
    display: grid;
    grid-template-columns: 24px 110px 44px 60px 1fr 78px 50px 62px 72px 60px;
    gap: 10px; align-items: center;
    padding: 0 14px; border: 1px solid var(--rule);
    background: var(--paper-2);
    font-family: var(--pro-mono); font-size: 11px;
    height: 38px;
    cursor: pointer; text-decoration: none; color: var(--ink);
    margin-bottom: 4px;
  }
  .pd-pro :global(.run-row:hover) { background: var(--paper-3); }
  .pd-pro :global(.run-row .ix) { color: var(--ash); font-size: 10px; }
  .pd-pro :global(.run-row .id) {
    color: var(--graphite);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .pd-pro :global(.run-row .title) {
    font-family: var(--pro-sans); font-size: 12px; font-weight: 500;
    color: var(--ink);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .pd-pro :global(.run-row .num) {
    text-align: right; font-variant-numeric: tabular-nums;
  }
  .pd-pro :global(.run-row .nu) { color: var(--ash); }

  .pd-pro :global(.empty) {
    font-family: var(--pro-mono); font-size: 12px; color: var(--ash);
    padding: 24px 14px; text-align: center;
    border: 1px dashed var(--rule);
  }

  /* Vision tab — children come from VisionTab.svelte; flatten its glass */
  .pd-pro :global(.vision-pane .glass) {
    background: var(--paper-2) !important;
    border: 1px solid var(--rule) !important;
    border-radius: 0 !important;
    backdrop-filter: none !important;
    -webkit-backdrop-filter: none !important;
    box-shadow: none !important;
  }

  @media (max-width: 1180px) {
    .pd-pro :global(.cards) { grid-template-columns: 1fr; }
    .pd-pro :global(.qstats) { grid-template-columns: repeat(3, 1fr); }
    .pd-pro :global(.run-row) {
      grid-template-columns: 24px 44px 60px 1fr 78px 50px 62px 60px;
    }
    .pd-pro :global(.run-row .id) { display: none; }
  }
</style>
