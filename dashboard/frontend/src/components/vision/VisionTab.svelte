<!-- dashboard/frontend/src/components/vision/VisionTab.svelte -->
<script lang="ts">
  import { getVision, findVisionGaps, getVisionProposals, listRuns } from '../../lib/api';
  import type { VisionRead, Project, VisionProposals, Run } from '../../lib/types';
  import VisionChat from './VisionChat.svelte';
  import { toastSuccess, toastError } from '../../lib/toast.svelte';

  let { project }: { project: Project } = $props();

  let vision = $state<VisionRead | null>(null);
  let loading = $state(true);
  let mode = $state<'view' | 'chat'>('view');
  let findingGaps = $state(false);
  let proposals = $state<VisionProposals | null>(null);
  // Most-recent vision-bootstrap runs for this project (issue #272: gate
  // the "Re-run analyst" button when the last attempt failed and no
  // recovery has happened since).
  let visionRuns = $state<Run[]>([]);

  $effect(() => { load(); });

  $effect(() => {
    if (!project?.id) return;
    let cancelled = false;
    getVisionProposals(project.id)
      .then(p => { if (!cancelled) proposals = p; })
      .catch(() => {});
    loadVisionRuns();
    return () => { cancelled = true; };
  });

  async function loadVisionRuns() {
    if (!project?.id) return;
    try {
      // /api/runs has no `mode` filter; fetch a small window and filter
      // client-side. 20 is plenty — vision-bootstrap is a once-per-commit
      // event, not a hot loop.
      const list = await listRuns({ project_id: project.id, limit: 20 });
      visionRuns = (list.runs ?? []).filter(r => r.mode === 'vision-bootstrap');
    } catch {
      visionRuns = [];
    }
  }

  async function load() {
    loading = true;
    try {
      vision = await getVision(project.id);
      mode = 'view';
    } catch (e: any) {
      // 404 = no vision yet — show empty state
      vision = null;
    } finally {
      loading = false;
    }
  }

  function startChat() { mode = 'chat'; }
  function onApproved() { mode = 'view'; load(); loadVisionRuns(); }
  function onCancelled() { mode = 'view'; }

  // Issue #272: disable Re-run analyst when the most-recent vision-bootstrap
  // run failed AND no completed run has happened since. The runs list
  // returned by /api/runs is ordered by started_at desc, so visionRuns[0]
  // is the latest. A "completed" run anywhere later in the list (i.e. with
  // a more recent started_at than the failure) is treated as recovery —
  // but since the array is desc-ordered, recovery is just "latest is not
  // failed".
  const lastFailedRun = $derived.by<Run | null>(() => {
    if (visionRuns.length === 0) return null;
    const latest = visionRuns[0];
    return latest.status === 'failed' ? latest : null;
  });
  const rerunDisabled = $derived(findingGaps || lastFailedRun !== null);
  const disabledReason = $derived.by(() => {
    if (!lastFailedRun) return '';
    const idLabel = lastFailedRun.run_id.slice(-8);
    return `Last analyst run failed — see run ${idLabel}. Fix gh auth or commit a new vision to re-enable.`;
  });

  async function findGaps() {
    findingGaps = true;
    try {
      await findVisionGaps(project.id);
      toastSuccess('Gap analysis started — proposed issues will appear on GitHub shortly');
      // Refresh the run list so the new run appears and the button stays
      // accurately gated.
      loadVisionRuns();
    } catch (e: any) {
      toastError(e.message);
    } finally {
      findingGaps = false;
    }
  }

  const githubBaseUrl = $derived(
    `https://github.com/${project.repo}/blob/${project.branch || 'main'}/docs/vision.md`,
  );

  const githubProposalsUrl = $derived(
    `https://github.com/${project.repo}/issues?q=is:open+label:vision-suggested`,
  );
</script>

<!-- Vision analyst info strip — Pro design (issue #272) -->
<div class="vision-strip">
  <strong class="strip-label">Vision analyst</strong>
  <span class="strip-meta">
    {#if proposals}
      {proposals.open} proposal{proposals.open === 1 ? '' : 's'} open
      · {proposals.accepted_recent} accepted last week
    {:else}
      Loading…
    {/if}
  </span>
  {#if lastFailedRun}
    <span class="status planx" data-testid="vision-rerun-failed-pill">FAILED</span>
    <a class="failure-link"
       href={`/runs/${lastFailedRun.run_id}`}
       data-testid="vision-rerun-failure-link"
       title={disabledReason}>
      see run {lastFailedRun.run_id.slice(-8)}
    </a>
  {/if}
  <span class="strip-spacer"></span>
  <button
    type="button"
    class="opbtn"
    onclick={findGaps}
    disabled={rerunDisabled}
    aria-label={lastFailedRun ? disabledReason : 'Re-run vision analyst'}
    title={lastFailedRun ? disabledReason : ''}
    data-testid="vision-rerun-analyst-btn"
  >
    {findingGaps ? 'Starting…' : 'Re-run analyst'}
  </button>
  <a class="opbtn"
     href={githubProposalsUrl}
     target="_blank" rel="noopener">View on GitHub →</a>
</div>

{#if loading}
  <div class="text-sm text-tertiary">Loading…</div>
{:else if mode === 'chat'}
  <VisionChat projectId={project.id} {onApproved} {onCancelled} />
{:else if vision === null}
  <!-- Empty state -->
  <div class="card p-6 text-center space-y-3">
    <h3 class="font-heading text-base">No vision yet</h3>
    <p class="text-xs text-tertiary max-w-md mx-auto">
      A vision describes what this project is for and where it's headed.
      Claude will help you author it through a short conversation, then
      commit it to <code class="text-accent-orange">docs/vision.md</code>
      on the project's base branch.
    </p>
    <button type="button" onclick={startChat} data-testid="vision-start-btn"
            class="btn btn-primary btn-sm text-xs">Start vision chat</button>
  </div>
{:else}
  <!-- Read state -->
  <div class="card p-5 space-y-4">
    <div class="flex justify-between items-start gap-3 pb-2 border-b border-tertiary/15">
      <div class="text-xs text-tertiary">
        {#if vision.last_refined_at}
          Last refined {new Date(vision.last_refined_at).toLocaleDateString()}
          {#if vision.last_refined_by} by {vision.last_refined_by}{/if}
        {:else}
          docs/vision.md on {project.branch || 'main'}
        {/if}
      </div>
      <div class="flex gap-2">
        <button type="button" onclick={startChat} class="btn btn-primary btn-sm text-xs">Refine via chat</button>
        <button type="button" onclick={findGaps}
                disabled={rerunDisabled}
                aria-label={lastFailedRun ? disabledReason : 'Find gaps in vision'}
                title={lastFailedRun ? disabledReason : ''}
                data-testid="vision-find-gaps-btn"
                class="btn btn-ghost btn-sm text-xs">
          {findingGaps ? 'Finding…' : 'Find gaps'}
        </button>
        <a href={githubBaseUrl} target="_blank" rel="noopener" class="btn btn-ghost btn-sm text-xs">View on GitHub →</a>
      </div>
    </div>
    <pre class="whitespace-pre-wrap font-mono text-xs text-secondary">{vision.body}</pre>
  </div>
{/if}

<style>
  /* Vision analyst strip — Pro tokens (issue #272). */
  .vision-strip {
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    padding: 10px 14px;
    background: var(--paper-2);
    border: 1px solid var(--rule);
    margin-bottom: 12px;
    font-family: var(--pro-sans);
  }
  .vision-strip .strip-label {
    font-family: var(--pro-sans);
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--ink);
  }
  .vision-strip .strip-meta {
    font-family: var(--pro-mono);
    font-size: 11px;
    color: var(--graphite);
  }
  .vision-strip .strip-spacer { flex: 1; }
  .vision-strip .failure-link {
    font-family: var(--pro-mono);
    font-size: 11px;
    color: var(--abort);
    text-decoration: underline;
    text-underline-offset: 2px;
  }
  .vision-strip .failure-link:hover { color: var(--ink); }
</style>
