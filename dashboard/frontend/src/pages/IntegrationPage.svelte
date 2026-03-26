<script lang="ts">
  import { listProjects, getIntegrationStatus, getIntegrationFeatures } from '../lib/api';
  import type { Project, IntegrationStatus, IntegrationFeature } from '../lib/types';

  let projects = $state<Project[]>([]);
  let selectedRepo = $state('');
  let status = $state<IntegrationStatus | null>(null);
  let features = $state<IntegrationFeature[]>([]);

  $effect(() => {
    loadProjects();
  });

  async function loadProjects() {
    try { projects = await listProjects(); } catch { /* silent */ }
  }

  $effect(() => {
    if (selectedRepo) loadIntegration(selectedRepo);
  });

  async function loadIntegration(repo: string) {
    const [sRes, fRes] = await Promise.allSettled([
      getIntegrationStatus(repo),
      getIntegrationFeatures({ project_repo: repo }),
    ]);
    if (sRes.status === 'fulfilled') status = sRes.value;
    if (fRes.status === 'fulfilled') features = fRes.value.items;
  }

  const stateColors: Record<string, string> = {
    merged_to_dev: 'text-info',
    validated: 'text-approve',
    excluded: 'text-text-muted',
    validation_failed: 'text-reject',
    conflict: 'text-warning',
  };
</script>

<div class="space-y-4 animate-fade-in-up">
  <div class="flex items-center justify-between">
    <h1 class="text-lg font-semibold text-text">Integration Pipeline</h1>
    <select
      bind:value={selectedRepo}
      class="bg-surface text-text-dim text-xs px-3 py-1.5 rounded border border-border-subtle focus:border-focus outline-none"
    >
      <option value="">Select project...</option>
      {#each projects as p}
        <option value={p.repo}>{p.repo}</option>
      {/each}
    </select>
  </div>

  {#if status}
    <!-- Status overview -->
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
      <div class="glass rounded-lg px-4 py-3">
        <div class="text-[10px] text-text-muted uppercase">Features</div>
        <div class="text-xl font-semibold text-text data-readout">{status.feature_count}</div>
      </div>
      <div class="glass rounded-lg px-4 py-3">
        <div class="text-[10px] text-text-muted uppercase">Validated</div>
        <div class="text-xl font-semibold text-approve data-readout">{status.validated_count}</div>
      </div>
      <div class="glass rounded-lg px-4 py-3">
        <div class="text-[10px] text-text-muted uppercase">Conflicts</div>
        <div class="text-xl font-semibold data-readout {status.conflict_count > 0 ? 'text-warning' : 'text-text-dim'}">{status.conflict_count}</div>
      </div>
      <div class="glass rounded-lg px-4 py-3">
        <div class="text-[10px] text-text-muted uppercase">Dev Branch</div>
        <div class="text-sm text-text-dim font-mono truncate">{status.dev_branch}</div>
      </div>
    </div>
  {/if}

  <!-- Feature list -->
  {#if features.length > 0}
    <div class="glass rounded-lg overflow-hidden">
      <table class="w-full text-xs">
        <thead>
          <tr class="border-b border-border-subtle text-text-muted">
            <th class="px-4 py-2 text-left">Issue</th>
            <th class="px-4 py-2 text-left">Branch</th>
            <th class="px-4 py-2 text-left">State</th>
            <th class="px-4 py-2 text-left">Validation</th>
          </tr>
        </thead>
        <tbody>
          {#each features as feature}
            <tr class="border-b border-border-subtle/50">
              <td class="px-4 py-2 text-text">
                {#if feature.issue_number}<span class="text-info">#{feature.issue_number}</span>{/if}
                {feature.issue_title ?? ''}
              </td>
              <td class="px-4 py-2 text-text-dim font-mono">{feature.branch}</td>
              <td class="px-4 py-2 {stateColors[feature.state] ?? 'text-text-dim'} capitalize">{feature.state.replace(/_/g, ' ')}</td>
              <td class="px-4 py-2 text-text-dim">{feature.validation_status ?? '-'}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {:else if selectedRepo}
    <div class="text-sm text-text-muted text-center py-8">No features in integration pipeline</div>
  {:else}
    <div class="text-sm text-text-muted text-center py-12">Select a project to view its integration pipeline</div>
  {/if}
</div>
