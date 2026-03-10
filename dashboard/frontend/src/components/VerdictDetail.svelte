<script lang="ts">
  interface Props {
    detail: string | null;
  }

  let { detail }: Props = $props();

  interface ParsedVerdict {
    verdict: string | null;
    reasoning: string | null;
    project: string | null;
    issue_number: number | null;
    branch: string | null;
    raw: string;
    isJson: boolean;
    // Legacy plain-text sections
    reasoningLines: string[];
    requirements: string[];
    feedback: string[];
  }

  let parsed = $derived<ParsedVerdict>(parseVerdict(detail));

  function parseVerdict(text: string | null): ParsedVerdict {
    const empty: ParsedVerdict = {
      verdict: null, reasoning: null, project: null,
      issue_number: null, branch: null, raw: text ?? '',
      isJson: false, reasoningLines: [], requirements: [], feedback: [],
    };
    if (!text) return empty;

    // Try JSON parse first (verdict_detail is stored as json.dumps())
    try {
      const data = JSON.parse(text);
      if (data && typeof data === 'object') {
        return {
          verdict: data.verdict ?? null,
          reasoning: data.reasoning ?? null,
          project: data.project ?? null,
          issue_number: data.issue_number ?? null,
          branch: data.branch ?? null,
          raw: text,
          isJson: true,
          reasoningLines: [],
          requirements: [],
          feedback: [],
        };
      }
    } catch {
      // Not JSON - fall through to plain-text parsing
    }

    // Legacy plain-text parsing for backward compatibility
    const lines = text.split('\n').filter(l => l.trim());
    const reasoningLines: string[] = [];
    const requirements: string[] = [];
    const feedback: string[] = [];
    let section = 'reasoning';

    for (const line of lines) {
      const lower = line.toLowerCase();
      if (lower.includes('requirement') || lower.includes('checklist')) {
        section = 'requirements';
        continue;
      }
      if (lower.includes('feedback') || lower.includes('suggestion')) {
        section = 'feedback';
        continue;
      }

      const cleaned = line.replace(/^[-*]\s*/, '').trim();
      if (!cleaned) continue;

      if (section === 'requirements') requirements.push(cleaned);
      else if (section === 'feedback') feedback.push(cleaned);
      else reasoningLines.push(cleaned);
    }

    return {
      verdict: null, reasoning: null, project: null,
      issue_number: null, branch: null, raw: text,
      isJson: false, reasoningLines, requirements, feedback,
    };
  }
</script>

{#if detail}
  <div class="space-y-4">
    {#if parsed.isJson}
      <!-- Structured JSON verdict display -->
      {#if parsed.verdict}
        <div class="flex items-center gap-2">
          <span class="text-xs text-text-dim">Verdict:</span>
          <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium {
            parsed.verdict === 'APPROVE' ? 'bg-approve/20 text-approve shadow-[0_0_8px_rgba(34,197,94,0.2)]' :
            parsed.verdict === 'REJECT' ? 'bg-reject/20 text-reject shadow-[0_0_8px_rgba(239,68,68,0.2)]' :
            parsed.verdict === 'PR' ? 'bg-pr/20 text-pr shadow-[0_0_8px_rgba(168,85,247,0.2)]' :
            'bg-surface-2 text-text-dim'
          }">{parsed.verdict}</span>
        </div>
      {/if}

      {#if parsed.project}
        <div class="flex items-center gap-2 text-sm">
          <span class="text-text-dim">Project:</span>
          <span class="font-data text-xs">{parsed.project}</span>
        </div>
      {/if}

      <div class="flex flex-wrap gap-4 text-sm">
        {#if parsed.issue_number}
          <div class="flex items-center gap-1.5">
            <span class="text-text-dim">Issue:</span>
            <span class="font-data">#{parsed.issue_number}</span>
          </div>
        {/if}
        {#if parsed.branch}
          <div class="flex items-center gap-1.5">
            <span class="text-text-dim">Branch:</span>
            <span class="font-data text-xs">{parsed.branch}</span>
          </div>
        {/if}
      </div>

      {#if parsed.reasoning}
        <div>
          <h4 class="text-sm font-medium text-text-dim mb-2">Reasoning</h4>
          <p class="text-sm whitespace-pre-wrap leading-relaxed">{parsed.reasoning}</p>
        </div>
      {/if}

    {:else}
      <!-- Legacy plain-text verdict display -->
      {#if parsed.reasoningLines.length > 0}
        <div>
          <h4 class="text-sm font-medium text-text-dim mb-2">Reasoning</h4>
          <ul class="space-y-1">
            {#each parsed.reasoningLines as line}
              <li class="text-sm flex gap-2">
                <span class="text-text-dim shrink-0">-</span>
                <span>{line}</span>
              </li>
            {/each}
          </ul>
        </div>
      {/if}

      {#if parsed.requirements.length > 0}
        <div>
          <h4 class="text-sm font-medium text-text-dim mb-2">Requirements</h4>
          <ul class="space-y-1">
            {#each parsed.requirements as req}
              <li class="text-sm flex gap-2">
                <span class="text-approve shrink-0">&#10003;</span>
                <span>{req}</span>
              </li>
            {/each}
          </ul>
        </div>
      {/if}

      {#if parsed.feedback.length > 0}
        <div>
          <h4 class="text-sm font-medium text-text-dim mb-2">Feedback</h4>
          <ul class="space-y-1">
            {#each parsed.feedback as fb}
              <li class="text-sm flex gap-2">
                <span class="text-info shrink-0">i</span>
                <span>{fb}</span>
              </li>
            {/each}
          </ul>
        </div>
      {/if}
    {/if}

    <!-- Raw fallback toggle -->
    <details class="text-xs">
      <summary class="text-text-dim cursor-pointer hover:text-text">Show raw</summary>
      <pre class="mt-2 p-3 bg-surface-2 rounded-lg overflow-x-auto whitespace-pre-wrap">{parsed.isJson ? JSON.stringify(JSON.parse(parsed.raw), null, 2) : parsed.raw}</pre>
    </details>
  </div>
{:else}
  <p class="text-text-dim text-sm">No verdict detail available</p>
{/if}
