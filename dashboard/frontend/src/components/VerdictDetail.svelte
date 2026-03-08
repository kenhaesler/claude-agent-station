<script lang="ts">
  interface Props {
    detail: string | null;
  }

  let { detail }: Props = $props();

  interface ParsedVerdict {
    reasoning: string[];
    requirements: string[];
    feedback: string[];
    raw: string;
  }

  let parsed = $derived<ParsedVerdict>(parseVerdict(detail));

  function parseVerdict(text: string | null): ParsedVerdict {
    if (!text) return { reasoning: [], requirements: [], feedback: [], raw: '' };

    const lines = text.split('\n').filter(l => l.trim());
    const reasoning: string[] = [];
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

      const cleaned = line.replace(/^[-*•]\s*/, '').trim();
      if (!cleaned) continue;

      if (section === 'requirements') requirements.push(cleaned);
      else if (section === 'feedback') feedback.push(cleaned);
      else reasoning.push(cleaned);
    }

    return { reasoning, requirements, feedback, raw: text };
  }
</script>

{#if detail}
  <div class="space-y-4">
    {#if parsed.reasoning.length > 0}
      <div>
        <h4 class="text-sm font-medium text-text-dim mb-2">Reasoning</h4>
        <ul class="space-y-1">
          {#each parsed.reasoning as line}
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

    <!-- Raw fallback -->
    <details class="text-xs">
      <summary class="text-text-dim cursor-pointer hover:text-text">Raw verdict detail</summary>
      <pre class="mt-2 p-3 bg-surface-2 rounded-lg overflow-x-auto whitespace-pre-wrap">{parsed.raw}</pre>
    </details>
  </div>
{:else}
  <p class="text-text-dim text-sm">No verdict detail available</p>
{/if}
