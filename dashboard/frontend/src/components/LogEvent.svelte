<script lang="ts">
  import type { ParsedLogEvent } from '../lib/log-parser';
  import { formatToolInput, truncate } from '../lib/log-parser';
  import { formatDuration, formatTokens } from '../lib/format';

  interface Props {
    event: ParsedLogEvent;
  }

  let { event }: Props = $props();
  let expanded = $state(false);

  function toolInputSummary(): string {
    if (event.type !== 'assistant_tool_use' || !event.toolName) return '';
    return formatToolInput(event.toolName, event.toolInput);
  }

  function resultContentPreview(): string {
    if (!event.toolResultContent) return '';
    return truncate(event.toolResultContent, 300);
  }

  function hasExpandableContent(): boolean {
    if (event.type === 'assistant_thinking') return true;
    if (event.type === 'assistant_tool_use' && event.toolInput) {
      const summary = toolInputSummary();
      return JSON.stringify(event.toolInput).length > summary.length + 10;
    }
    if (event.type === 'tool_result' && event.toolResultContent) {
      return event.toolResultContent.length > 300;
    }
    if (event.type === 'system_init') return true;
    if (event.type === 'unknown') return (event.raw?.length || 0) > 200;
    return false;
  }

  const expandable = hasExpandableContent();
</script>

{#if event.type === 'system_init'}
  <!-- Session Init -->
  <div class="flex items-start gap-2 py-1.5 px-2 rounded-md bg-accent-blue/5 border border-accent-blue/10">
    <span class="text-accent-blue text-xs mt-0.5 shrink-0">&#9654;</span>
    <div class="min-w-0 flex-1">
      <span class="text-xs font-medium text-accent-blue">Session started</span>
      {#if event.cwd}
        <span class="text-xs text-text-dim ml-2 font-data">{event.cwd}</span>
      {/if}
      {#if expandable}
        <button onclick={() => expanded = !expanded} class="ml-2 text-xs text-text-dim hover:text-text cursor-pointer">
          {expanded ? '▾ hide' : '▸ tools'}
        </button>
      {/if}
      {#if expanded && event.tools}
        <div class="mt-1 text-xs text-text-dim font-data flex flex-wrap gap-1">
          {#each event.tools as tool}
            <span class="px-1.5 py-0.5 rounded bg-white/[0.04]">{tool}</span>
          {/each}
        </div>
      {/if}
    </div>
  </div>

{:else if event.type === 'assistant_text'}
  <!-- Assistant Text Output -->
  <div class="flex items-start gap-2 py-1.5 px-2">
    <span class="text-accent-emerald text-xs mt-0.5 shrink-0">&#9679;</span>
    <div class="min-w-0 flex-1 text-xs text-text whitespace-pre-wrap break-words leading-relaxed">{event.text}</div>
  </div>

{:else if event.type === 'assistant_thinking'}
  <!-- Thinking Block -->
  <div class="flex items-start gap-2 py-1 px-2 opacity-60">
    <span class="text-purple-400 text-xs mt-0.5 shrink-0">&#10047;</span>
    <div class="min-w-0 flex-1">
      <button
        onclick={() => expanded = !expanded}
        class="text-xs text-purple-400 hover:text-purple-300 cursor-pointer"
      >
        {expanded ? '▾' : '▸'} thinking
        {#if !expanded && event.thinking}
          <span class="text-text-dim ml-1 font-data">{truncate(event.thinking, 80)}</span>
        {/if}
      </button>
      {#if expanded}
        <div class="mt-1 text-xs text-text-dim whitespace-pre-wrap break-words font-data max-h-60 overflow-auto">{event.thinking}</div>
      {/if}
    </div>
  </div>

{:else if event.type === 'assistant_tool_use'}
  <!-- Tool Use -->
  <div class="flex items-start gap-2 py-1 px-2 rounded-md bg-amber-500/5 border-l-2 border-amber-500/30">
    <span class="text-amber-400 text-xs mt-0.5 shrink-0">&#9881;</span>
    <div class="min-w-0 flex-1">
      <span class="text-xs font-semibold text-amber-400">{event.toolName}</span>
      {#if toolInputSummary()}
        <span class="text-xs text-text-dim ml-1.5 font-data break-all">{toolInputSummary()}</span>
      {/if}
      {#if expandable}
        <button onclick={() => expanded = !expanded} class="ml-2 text-xs text-text-dim hover:text-text cursor-pointer">
          {expanded ? '▾ less' : '▸ more'}
        </button>
      {/if}
      {#if expanded}
        <pre class="mt-1 text-xs text-text-dim whitespace-pre-wrap break-all font-data max-h-60 overflow-auto bg-white/[0.02] rounded p-2">{JSON.stringify(event.toolInput, null, 2)}</pre>
      {/if}
    </div>
  </div>

{:else if event.type === 'tool_result'}
  <!-- Tool Result -->
  <div class="flex items-start gap-2 py-1 px-2 {event.isError ? 'bg-red-500/5' : 'bg-white/[0.02]'} border-l-2 {event.isError ? 'border-red-500/40' : 'border-white/10'}">
    <span class="text-xs mt-0.5 shrink-0 {event.isError ? 'text-red-400' : 'text-text-dim'}">{event.isError ? '✗' : '↩'}</span>
    <div class="min-w-0 flex-1">
      {#if event.isError}
        <span class="text-xs font-medium text-red-400 mr-1">Error</span>
      {/if}
      <span class="text-xs text-text-dim font-data whitespace-pre-wrap break-all">{expanded ? event.toolResultContent : resultContentPreview()}</span>
      {#if expandable}
        <button onclick={() => expanded = !expanded} class="ml-1 text-xs text-text-dim hover:text-text cursor-pointer">
          {expanded ? '▾ less' : '▸ more'}
        </button>
      {/if}
    </div>
  </div>

{:else if event.type === 'result'}
  <!-- Run Result Summary -->
  <div class="flex items-start gap-2 py-2 px-3 rounded-md bg-gradient-to-r {event.resultStatus === 'success' ? 'from-accent-emerald/10 to-transparent border-accent-emerald/20' : 'from-amber-500/10 to-transparent border-amber-500/20'} border">
    <span class="text-xs mt-0.5 shrink-0">{event.resultStatus === 'success' ? '✓' : '⚑'}</span>
    <div class="min-w-0 flex-1 flex flex-wrap gap-x-4 gap-y-1 text-xs">
      <span class="font-medium {event.resultStatus === 'success' ? 'text-accent-emerald' : 'text-amber-400'}">
        {event.resultStatus}
      </span>
      {#if event.numTurns}
        <span class="text-text-dim">{event.numTurns} turns</span>
      {/if}
      {#if event.durationMs}
        <span class="text-text-dim">{formatDuration(event.durationMs)}</span>
      {/if}
      {#if event.tokensTotal}
        <span class="text-text-dim">{formatTokens(event.tokensTotal)} tokens</span>
      {/if}
      {#if event.model}
        <span class="text-text-dim font-data">{event.model}</span>
      {/if}
    </div>
  </div>

{:else if event.type === 'rate_limit'}
  <!-- Rate Limit - minimal -->
  <div class="py-0.5 px-2 opacity-30 text-xs text-text-dim">
    ⏳ rate limit check
  </div>

{:else}
  <!-- Unknown / raw -->
  <div class="flex items-start gap-2 py-0.5 px-2 opacity-50">
    <span class="text-text-dim text-xs mt-0.5 shrink-0">·</span>
    <div class="min-w-0 flex-1 text-xs text-text-dim font-data break-all">
      {#if expandable}
        {expanded ? event.raw : truncate(event.raw || event.text || '', 200)}
        <button onclick={() => expanded = !expanded} class="ml-1 text-xs hover:text-text cursor-pointer">
          {expanded ? '▾' : '▸'}
        </button>
      {:else}
        {event.text || event.raw}
      {/if}
    </div>
  </div>
{/if}
