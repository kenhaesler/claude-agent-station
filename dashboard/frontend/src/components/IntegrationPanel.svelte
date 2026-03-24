<script lang="ts">
  import { getIntegrationStatus, getIntegrationFeatures, promoteToMain, syncDevWithMain, validateDev, excludeFeature, includeFeature } from '../lib/api';
  import type { IntegrationStatus, IntegrationFeature, IntegrationFeatureList } from '../lib/types';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import GlassCard from './GlassCard.svelte';
  import Badge from './Badge.svelte';
  import TimeAgo from './TimeAgo.svelte';
  import LoadingSpinner from './LoadingSpinner.svelte';
  import EmptyState from './EmptyState.svelte';
  import StatusOrb from './StatusOrb.svelte';

  interface Props {
    projectRepo?: string;
  }

  let { projectRepo }: Props = $props();

  let status = $state<IntegrationStatus | null>(null);
  let features = $state<IntegrationFeature[]>([]);
  let loading = $state(true);
  let actionLoading = $state<string | null>(null);
  let excludeTarget = $state<IntegrationFeature | null>(null);
  let excludeReason = $state('');

  // Group features by state for kanban columns
  let columns = $derived({
    merged_to_dev: features.filter(f => f.state === 'merged_to_dev'),
    validated: features.filter(f => f.state === 'validated'),
    conflict: features.filter(f => f.state === 'conflict' || f.state === 'validation_failed'),
    promoted: features.filter(f => f.state === 'promoted'),
    excluded: features.filter(f => f.state === 'excluded'),
  });

  let validationColor = $derived(
    status?.validation_status === 'pass' ? 'var(--color-approve)' :
    status?.validation_status === 'fail' ? 'var(--color-reject)' :
    status?.validation_status === 'pending' ? 'var(--color-warning)' :
    'var(--color-text-muted)'
  );

  let validationLabel = $derived(
    status?.validation_status === 'pass' ? 'Validated' :
    status?.validation_status === 'fail' ? 'Failed' :
    status?.validation_status === 'pending' ? 'Pending' :
    'Unknown'
  );

  async function loadData() {
    if (!projectRepo) {
      loading = false;
      return;
    }
    try {
      const [statusRes, featuresRes] = await Promise.allSettled([
        getIntegrationStatus(projectRepo),
        getIntegrationFeatures({ project_repo: projectRepo, limit: 100 }),
      ]);
      if (statusRes.status === 'fulfilled') status = statusRes.value;
      if (featuresRes.status === 'fulfilled') features = featuresRes.value.items;
    } catch { /* silent */ }
    loading = false;
  }

  $effect(() => {
    loading = true;
    loadData();
    const interval = setInterval(loadData, 15000);
    return () => clearInterval(interval);
  });

  async function handleSync() {
    if (!projectRepo || actionLoading) return;
    actionLoading = 'sync';
    try {
      const res = await syncDevWithMain(projectRepo);
      toastSuccess(res.message || 'Dev branch synced with main');
      await loadData();
    } catch (e: any) {
      toastError(`Sync failed: ${e.message}`);
    } finally {
      actionLoading = null;
    }
  }

  async function handleValidate() {
    if (!projectRepo || actionLoading) return;
    actionLoading = 'validate';
    try {
      const res = await validateDev(projectRepo);
      toastSuccess(res.message || 'Validation started');
      await loadData();
    } catch (e: any) {
      toastError(`Validation failed: ${e.message}`);
    } finally {
      actionLoading = null;
    }
  }

  async function handlePromote() {
    if (!projectRepo || actionLoading) return;
    actionLoading = 'promote';
    try {
      const validatedIds = columns.validated.map(f => f.id);
      const res = await promoteToMain(projectRepo, validatedIds.length > 0 ? validatedIds : undefined);
      toastSuccess(res.message || 'Promotion started');
      await loadData();
    } catch (e: any) {
      toastError(`Promote failed: ${e.message}`);
    } finally {
      actionLoading = null;
    }
  }

  async function handleExclude() {
    if (!excludeTarget || !excludeReason.trim()) return;
    try {
      await excludeFeature(excludeTarget.id, excludeReason.trim());
      toastSuccess(`Feature #${excludeTarget.issue_number ?? excludeTarget.id} excluded`);
      excludeTarget = null;
      excludeReason = '';
      await loadData();
    } catch (e: any) {
      toastError(`Exclude failed: ${e.message}`);
    }
  }

  async function handleInclude(feature: IntegrationFeature) {
    try {
      await includeFeature(feature.id);
      toastSuccess(`Feature #${feature.issue_number ?? feature.id} re-included`);
      await loadData();
    } catch (e: any) {
      toastError(`Include failed: ${e.message}`);
    }
  }

  function stateVariant(state: IntegrationFeature['state']): 'info' | 'success' | 'error' | 'warning' | 'purple' | 'muted' {
    switch (state) {
      case 'merged_to_dev': return 'info';
      case 'validated': return 'success';
      case 'promoted': return 'purple';
      case 'conflict': return 'error';
      case 'validation_failed': return 'error';
      case 'excluded': return 'muted';
      default: return 'muted';
    }
  }

  function stateLabel(state: IntegrationFeature['state']): string {
    switch (state) {
      case 'merged_to_dev': return 'On Dev';
      case 'validated': return 'Validated';
      case 'promoted': return 'Promoted';
      case 'conflict': return 'Conflict';
      case 'validation_failed': return 'Failed';
      case 'excluded': return 'Excluded';
      default: return state;
    }
  }
</script>

{#if loading}
  <div class="flex items-center justify-center py-12">
    <LoadingSpinner />
  </div>
{:else if !projectRepo}
  <EmptyState message="Select a project to view integration status" />
{:else if !status}
  <EmptyState message="No integration branch configured for this project" />
{:else}
  <!-- Status Header -->
  <GlassCard class="p-4 mb-4">
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
      <div class="flex items-center gap-3">
        <StatusOrb active={status.validation_status === 'pass'} color={validationColor} size="md" />
        <div>
          <div class="flex items-center gap-2">
            <span class="text-sm font-semibold text-text font-data">{status.dev_branch}</span>
            <Badge label={validationLabel} variant={status.validation_status === 'pass' ? 'success' : status.validation_status === 'fail' ? 'error' : 'warning'} dot />
          </div>
          <div class="flex items-center gap-3 mt-0.5">
            <span class="text-[10px] text-text-muted">{status.feature_count} feature{status.feature_count !== 1 ? 's' : ''}</span>
            <span class="text-[10px] text-text-muted">{status.validated_count} validated</span>
            {#if status.conflict_count > 0}
              <span class="text-[10px] text-reject">{status.conflict_count} conflict{status.conflict_count !== 1 ? 's' : ''}</span>
            {/if}
            {#if status.last_validation}
              <span class="text-[10px] text-text-muted">Last validated <TimeAgo date={status.last_validation} /></span>
            {/if}
          </div>
        </div>
      </div>

      <!-- Action Buttons -->
      <div class="flex items-center gap-2">
        <button
          onclick={handleSync}
          disabled={!!actionLoading}
          class="px-3 py-1.5 text-xs font-medium rounded-lg border border-border bg-surface hover:bg-white/[0.04] text-text transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {actionLoading === 'sync' ? 'Syncing...' : 'Sync'}
        </button>
        <button
          onclick={handleValidate}
          disabled={!!actionLoading}
          class="px-3 py-1.5 text-xs font-medium rounded-lg border border-border bg-surface hover:bg-white/[0.04] text-text transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {actionLoading === 'validate' ? 'Validating...' : 'Validate'}
        </button>
        <button
          onclick={handlePromote}
          disabled={!!actionLoading || columns.validated.length === 0}
          class="px-3 py-1.5 text-xs font-medium rounded-lg bg-approve/15 text-approve hover:bg-approve/25 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {actionLoading === 'promote' ? 'Promoting...' : `Promote to Main${columns.validated.length > 0 ? ` (${columns.validated.length})` : ''}`}
        </button>
      </div>
    </div>
  </GlassCard>

  <!-- Kanban Columns -->
  <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
    <!-- On Dev -->
    <div>
      <div class="flex items-center gap-2 mb-2">
        <span class="text-xs font-semibold text-text">On Dev</span>
        <Badge label={String(columns.merged_to_dev.length)} variant="info" />
      </div>
      <div class="space-y-2">
        {#each columns.merged_to_dev as feature (feature.id)}
          <GlassCard class="p-3" elevation={2}>
            <div class="flex items-start justify-between gap-1">
              <div class="min-w-0">
                {#if feature.issue_number}
                  <span class="text-xs font-semibold text-info font-data">#{feature.issue_number}</span>
                {/if}
                <p class="text-[11px] text-text truncate mt-0.5">{feature.issue_title ?? feature.branch}</p>
              </div>
              <button
                onclick={() => { excludeTarget = feature; }}
                class="text-[10px] text-text-muted hover:text-reject shrink-0 transition-colors"
                title="Exclude from promotion"
              >&times;</button>
            </div>
            <div class="mt-1.5">
              <TimeAgo date={feature.updated_at} />
            </div>
          </GlassCard>
        {:else}
          <p class="text-[10px] text-text-muted py-4 text-center">No features</p>
        {/each}
      </div>
    </div>

    <!-- Validated -->
    <div>
      <div class="flex items-center gap-2 mb-2">
        <span class="text-xs font-semibold text-text">Validated</span>
        <Badge label={String(columns.validated.length)} variant="success" />
      </div>
      <div class="space-y-2">
        {#each columns.validated as feature (feature.id)}
          <GlassCard class="p-3" elevation={2} glow="emerald">
            <div class="flex items-start justify-between gap-1">
              <div class="min-w-0">
                {#if feature.issue_number}
                  <span class="text-xs font-semibold text-approve font-data">#{feature.issue_number}</span>
                {/if}
                <p class="text-[11px] text-text truncate mt-0.5">{feature.issue_title ?? feature.branch}</p>
              </div>
              <Badge label="pass" variant="success" size="sm" />
            </div>
            <div class="mt-1.5">
              <TimeAgo date={feature.updated_at} />
            </div>
          </GlassCard>
        {:else}
          <p class="text-[10px] text-text-muted py-4 text-center">No features</p>
        {/each}
      </div>
    </div>

    <!-- Conflicts / Failed -->
    <div>
      <div class="flex items-center gap-2 mb-2">
        <span class="text-xs font-semibold text-text">Issues</span>
        <Badge label={String(columns.conflict.length)} variant="error" />
      </div>
      <div class="space-y-2">
        {#each columns.conflict as feature (feature.id)}
          <GlassCard class="p-3" elevation={2} glow="red">
            <div class="flex items-start justify-between gap-1">
              <div class="min-w-0">
                {#if feature.issue_number}
                  <span class="text-xs font-semibold text-reject font-data">#{feature.issue_number}</span>
                {/if}
                <p class="text-[11px] text-text truncate mt-0.5">{feature.issue_title ?? feature.branch}</p>
              </div>
              <Badge label={stateLabel(feature.state)} variant="error" size="sm" />
            </div>
            {#if feature.validation_output}
              <p class="text-[10px] text-text-muted mt-1 truncate" title={feature.validation_output}>{feature.validation_output}</p>
            {/if}
            <div class="mt-1.5">
              <TimeAgo date={feature.updated_at} />
            </div>
          </GlassCard>
        {:else}
          <p class="text-[10px] text-text-muted py-4 text-center">No issues</p>
        {/each}
      </div>
    </div>

    <!-- Promoted -->
    <div>
      <div class="flex items-center gap-2 mb-2">
        <span class="text-xs font-semibold text-text">On Main</span>
        <Badge label={String(columns.promoted.length)} variant="purple" />
      </div>
      <div class="space-y-2">
        {#each columns.promoted as feature (feature.id)}
          <GlassCard class="p-3" elevation={2}>
            <div class="min-w-0">
              {#if feature.issue_number}
                <span class="text-xs font-semibold text-accent-purple font-data">#{feature.issue_number}</span>
              {/if}
              <p class="text-[11px] text-text truncate mt-0.5">{feature.issue_title ?? feature.branch}</p>
            </div>
            {#if feature.pr_number}
              <p class="text-[10px] text-text-muted mt-1">PR #{feature.pr_number}</p>
            {/if}
            <div class="mt-1.5">
              <TimeAgo date={feature.updated_at} />
            </div>
          </GlassCard>
        {:else}
          <p class="text-[10px] text-text-muted py-4 text-center">No features</p>
        {/each}
      </div>
    </div>
  </div>

  <!-- Excluded Features (if any) -->
  {#if columns.excluded.length > 0}
    <div class="mt-4">
      <div class="flex items-center gap-2 mb-2">
        <span class="text-xs font-semibold text-text-dim">Excluded</span>
        <Badge label={String(columns.excluded.length)} variant="muted" />
      </div>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-2">
        {#each columns.excluded as feature (feature.id)}
          <GlassCard class="p-3 opacity-60" elevation={2}>
            <div class="flex items-start justify-between gap-1">
              <div class="min-w-0">
                {#if feature.issue_number}
                  <span class="text-xs font-data text-text-muted">#{feature.issue_number}</span>
                {/if}
                <p class="text-[11px] text-text-dim truncate mt-0.5">{feature.issue_title ?? feature.branch}</p>
              </div>
              <button
                onclick={() => handleInclude(feature)}
                class="text-[10px] text-text-muted hover:text-approve shrink-0 transition-colors"
                title="Re-include in promotion"
              >+</button>
            </div>
            {#if feature.excluded_reason}
              <p class="text-[10px] text-text-muted mt-1 truncate" title={feature.excluded_reason}>{feature.excluded_reason}</p>
            {/if}
          </GlassCard>
        {/each}
      </div>
    </div>
  {/if}

  <!-- Exclude Confirmation Dialog -->
  {#if excludeTarget}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" onclick={() => { excludeTarget = null; excludeReason = ''; }}>
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div class="bg-surface border border-border rounded-xl p-5 w-full max-w-sm shadow-xl" onclick={(e) => e.stopPropagation()}>
        <h3 class="text-sm font-semibold text-text mb-1">Exclude Feature</h3>
        <p class="text-xs text-text-dim mb-3">
          Exclude #{excludeTarget.issue_number ?? excludeTarget.id} from promotion pipeline?
        </p>
        <input
          type="text"
          placeholder="Reason for exclusion..."
          bind:value={excludeReason}
          class="w-full px-3 py-2 text-xs bg-surface-raised border border-border rounded-lg text-text placeholder:text-text-muted focus:outline-none focus:border-info mb-3"
        />
        <div class="flex justify-end gap-2">
          <button
            onclick={() => { excludeTarget = null; excludeReason = ''; }}
            class="px-3 py-1.5 text-xs font-medium rounded-lg border border-border bg-surface hover:bg-white/[0.04] text-text transition-colors"
          >Cancel</button>
          <button
            onclick={handleExclude}
            disabled={!excludeReason.trim()}
            class="px-3 py-1.5 text-xs font-medium rounded-lg bg-reject/15 text-reject hover:bg-reject/25 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >Exclude</button>
        </div>
      </div>
    </div>
  {/if}
{/if}
