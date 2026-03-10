<script lang="ts">
  interface Props {
    report: string | null;
  }

  let { report }: Props = $props();

  interface Requirement {
    description: string;
    source: string;
    completed: boolean;
  }

  interface ParsedReport {
    status: string | null;
    issue_number: number | null;
    issue_title: string | null;
    branch: string | null;
    base_branch: string | null;
    requirements: Requirement[];
    files_changed: string[];
    commits: string[];
    tests_run: boolean | null;
    tests_passed: boolean | null;
    test_output_summary: string | null;
    notes: string | null;
    isJson: boolean;
    raw: string;
  }

  let parsed = $derived<ParsedReport>(parseReport(report));

  function parseReport(text: string | null): ParsedReport {
    const empty: ParsedReport = {
      status: null, issue_number: null, issue_title: null,
      branch: null, base_branch: null, requirements: [],
      files_changed: [], commits: [], tests_run: null,
      tests_passed: null, test_output_summary: null, notes: null,
      isJson: false, raw: text ?? '',
    };
    if (!text) return empty;

    try {
      const data = JSON.parse(text);
      if (data && typeof data === 'object') {
        return {
          status: data.status ?? null,
          issue_number: data.issue_number ?? null,
          issue_title: data.issue_title ?? null,
          branch: data.branch ?? null,
          base_branch: data.base_branch ?? null,
          requirements: Array.isArray(data.requirements) ? data.requirements : [],
          files_changed: Array.isArray(data.files_changed) ? data.files_changed : [],
          commits: Array.isArray(data.commits) ? data.commits : [],
          tests_run: data.tests_run ?? null,
          tests_passed: data.tests_passed ?? null,
          test_output_summary: data.test_output_summary ?? null,
          notes: data.notes ?? null,
          isJson: true,
          raw: text,
        };
      }
    } catch {
      // Not JSON
    }

    return { ...empty, raw: text };
  }

  function statusColor(status: string | null): string {
    if (status === 'success') return 'bg-approve/20 text-approve shadow-[0_0_8px_rgba(34,197,94,0.2)]';
    if (status === 'partial') return 'bg-warning/20 text-warning shadow-[0_0_8px_rgba(245,158,11,0.2)]';
    if (status === 'failure') return 'bg-reject/20 text-reject shadow-[0_0_8px_rgba(239,68,68,0.2)]';
    return 'bg-surface-2 text-text-dim';
  }
</script>

{#if report}
  <div class="space-y-4">
    {#if parsed.isJson}
      <!-- Header: status + issue info -->
      <div class="flex flex-wrap items-center gap-3">
        {#if parsed.status}
          <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium {statusColor(parsed.status)}">
            {parsed.status}
          </span>
        {/if}
        {#if parsed.issue_title}
          <span class="text-sm font-medium">
            {#if parsed.issue_number}
              <span class="text-text-dim">#{parsed.issue_number}</span>
            {/if}
            {parsed.issue_title}
          </span>
        {/if}
      </div>

      <!-- Branch info -->
      {#if parsed.branch || parsed.base_branch}
        <div class="flex flex-wrap gap-4 text-sm">
          {#if parsed.branch}
            <div class="flex items-center gap-1.5">
              <span class="text-text-dim">Branch:</span>
              <span class="font-data text-xs">{parsed.branch}</span>
            </div>
          {/if}
          {#if parsed.base_branch}
            <div class="flex items-center gap-1.5">
              <span class="text-text-dim">Base:</span>
              <span class="font-data text-xs">{parsed.base_branch}</span>
            </div>
          {/if}
        </div>
      {/if}

      <!-- Requirements checklist -->
      {#if parsed.requirements.length > 0}
        <div>
          <h4 class="text-sm font-medium text-text-dim mb-2">Requirements</h4>
          <ul class="space-y-1.5">
            {#each parsed.requirements as req}
              <li class="text-sm flex gap-2 items-start">
                {#if req.completed}
                  <span class="text-approve shrink-0 mt-0.5">&#10003;</span>
                {:else}
                  <span class="text-reject shrink-0 mt-0.5">&#10007;</span>
                {/if}
                <span>
                  {req.description}
                  {#if req.source}
                    <span class="text-xs text-text-dim ml-1">({req.source})</span>
                  {/if}
                </span>
              </li>
            {/each}
          </ul>
        </div>
      {/if}

      <!-- Files changed -->
      {#if parsed.files_changed.length > 0}
        <div>
          <h4 class="text-sm font-medium text-text-dim mb-2">Files Changed ({parsed.files_changed.length})</h4>
          <div class="flex flex-wrap gap-1.5">
            {#each parsed.files_changed as file}
              <span class="px-2 py-0.5 bg-surface-2 rounded text-xs font-data">{file}</span>
            {/each}
          </div>
        </div>
      {/if}

      <!-- Test results -->
      {#if parsed.tests_run !== null}
        <div>
          <h4 class="text-sm font-medium text-text-dim mb-2">Tests</h4>
          <div class="flex items-center gap-2 text-sm">
            {#if parsed.tests_passed}
              <span class="text-approve">Passed</span>
            {:else if parsed.tests_run && !parsed.tests_passed}
              <span class="text-reject">Failed</span>
            {:else}
              <span class="text-text-dim">Not run</span>
            {/if}
            {#if parsed.test_output_summary}
              <span class="text-text-dim text-xs">- {parsed.test_output_summary}</span>
            {/if}
          </div>
        </div>
      {/if}

      <!-- Commits -->
      {#if parsed.commits.length > 0}
        <div>
          <h4 class="text-sm font-medium text-text-dim mb-2">Commits</h4>
          <div class="flex flex-wrap gap-1.5">
            {#each parsed.commits as commit}
              <span class="px-2 py-0.5 bg-surface-2 rounded text-xs font-data">{commit}</span>
            {/each}
          </div>
        </div>
      {/if}

      <!-- Notes -->
      {#if parsed.notes}
        <div>
          <h4 class="text-sm font-medium text-text-dim mb-2">Notes</h4>
          <p class="text-sm whitespace-pre-wrap">{parsed.notes}</p>
        </div>
      {/if}

    {:else}
      <!-- Fallback: plain text -->
      <pre class="text-sm whitespace-pre-wrap text-text-dim">{parsed.raw}</pre>
    {/if}

    <!-- Raw toggle -->
    <details class="text-xs">
      <summary class="text-text-dim cursor-pointer hover:text-text">Show raw</summary>
      <pre class="mt-2 p-3 bg-surface-2 rounded-lg overflow-x-auto whitespace-pre-wrap">{parsed.isJson ? JSON.stringify(JSON.parse(parsed.raw), null, 2) : parsed.raw}</pre>
    </details>
  </div>
{:else}
  <p class="text-text-dim text-sm">No employee report available</p>
{/if}
