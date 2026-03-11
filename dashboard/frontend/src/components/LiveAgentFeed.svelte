<script lang="ts">
  import { liveActivity } from '../lib/live-activity.svelte';
  import { truncate, formatToolInput } from '../lib/log-parser';

  // Show last 8 items
  let visibleActions = $derived(liveActivity.recentActions.slice(-8).reverse());

  // Tool category colors and icons
  function getToolMeta(name: string): { icon: string; badgeClass: string } {
    switch (name) {
      case 'Read':
      case 'Glob':
      case 'Grep':
        return { icon: '&#128269;', badgeClass: 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30' };
      case 'Write':
      case 'Edit':
      case 'NotebookEdit':
        return { icon: '&#128196;', badgeClass: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' };
      case 'Bash':
        return { icon: '&#9000;', badgeClass: 'bg-amber-500/20 text-amber-400 border-amber-500/30' };
      case 'Agent':
        return { icon: '&#129302;', badgeClass: 'bg-purple-500/20 text-purple-400 border-purple-500/30' };
      case 'WebFetch':
      case 'WebSearch':
        return { icon: '&#127760;', badgeClass: 'bg-blue-500/20 text-blue-400 border-blue-500/30' };
      default:
        return { icon: '&#9881;', badgeClass: 'bg-slate-500/20 text-text-dim border-slate-500/30' };
    }
  }
</script>

<div class="glass rounded-xl overflow-hidden animate-fade-in-up" style="height: 180px">
  <!-- Header -->
  <div class="flex items-center justify-between px-3 py-1.5 border-b border-border">
    <div class="flex items-center gap-2">
      <span class="text-xs font-medium text-text-dim">Live Activity</span>
    </div>
    <div class="flex items-center gap-1.5">
      <div class="w-1.5 h-1.5 rounded-full {liveActivity.connected ? 'bg-emerald-400' : 'bg-red-400'}"></div>
      <span class="text-[10px] text-text-dim">{liveActivity.connected ? 'Connected' : 'Disconnected'}</span>
    </div>
  </div>

  <!-- Feed -->
  <div class="relative" style="height: 148px; overflow: hidden">
    {#if visibleActions.length === 0}
      <div class="flex items-center justify-center h-full">
        <span class="text-xs text-text-dim animate-shimmer px-4 py-1 rounded">
          Waiting for agent activity...
        </span>
      </div>
    {:else}
      <div class="p-1.5 space-y-0.5">
        {#each visibleActions as action, i (liveActivity.recentActions.length - i)}
          {@const meta = getToolMeta(action.toolName ?? '')}
          {@const summary = action.toolName ? truncate(formatToolInput(action.toolName, action.toolInput), 55) : ''}
          <div
            class="flex items-center gap-2 px-2 py-1 rounded text-xs {i === 0 ? 'activity-enter' : ''}"
          >
            <!-- Icon -->
            <span class="text-[11px] shrink-0 w-4 text-center">{@html meta.icon}</span>

            <!-- Tool badge -->
            <span class="shrink-0 text-[10px] px-1.5 py-0.5 rounded border font-medium {meta.badgeClass}">
              {action.toolName ?? '?'}
            </span>

            <!-- Summary -->
            <span class="text-text-dim text-[11px] truncate font-data flex-1 min-w-0">
              {summary}
            </span>
          </div>
        {/each}
      </div>
    {/if}

    <!-- Bottom fade gradient -->
    <div class="absolute bottom-0 left-0 right-0 h-6 pointer-events-none"
      style="background: linear-gradient(to top, rgba(5, 8, 22, 0.9), transparent)"
    ></div>
  </div>
</div>
