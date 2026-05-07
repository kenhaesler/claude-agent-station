<!-- dashboard/frontend/src/components/vision/VisionTab.svelte -->
<script lang="ts">
  import { getVision, findVisionGaps } from '../../lib/api';
  import type { VisionRead, Project } from '../../lib/types';
  import VisionChat from './VisionChat.svelte';
  import { toastSuccess, toastError } from '../../lib/toast.svelte';

  let { project }: { project: Project } = $props();

  let vision = $state<VisionRead | null>(null);
  let loading = $state(true);
  let mode = $state<'view' | 'chat'>('view');
  let findingGaps = $state(false);

  $effect(() => { load(); });

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
  function onApproved() { mode = 'view'; load(); }
  function onCancelled() { mode = 'view'; }

  async function findGaps() {
    findingGaps = true;
    try {
      await findVisionGaps(project.id);
      toastSuccess('Gap analysis started — proposed issues will appear on GitHub shortly');
    } catch (e: any) {
      toastError(e.message);
    } finally {
      findingGaps = false;
    }
  }

  const githubBaseUrl = $derived(
    `https://github.com/${project.repo}/blob/${project.branch || 'main'}/docs/vision.md`,
  );
</script>

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
        <button type="button" onclick={findGaps} disabled={findingGaps}
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
