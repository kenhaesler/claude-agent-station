<script lang="ts">
  import Fuse from 'fuse.js';
  import { navigate, route } from '../lib/router.svelte';
  import { agentPresence, togglePanel } from '../lib/agent-presence.svelte';
  import { listRuns, listProjects, triggerRun } from '../lib/api';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import type { Run, Project } from '../lib/types';

  interface Props {
    open: boolean;
    onclose: () => void;
  }

  let { open, onclose }: Props = $props();

  let query = $state('');
  let selectedIndex = $state(0);
  let inputEl: HTMLInputElement | undefined = $state(undefined);
  let recentRuns = $state<Run[]>([]);
  let projects = $state<Project[]>([]);

  // Fetch data when opened
  $effect(() => {
    if (open) {
      query = '';
      selectedIndex = 0;
      loadData();
      // Focus input on next tick
      setTimeout(() => inputEl?.focus(), 50);
    }
  });

  async function loadData() {
    try {
      const [runsRes, projRes] = await Promise.allSettled([
        listRuns({ limit: 10 }),
        listProjects(),
      ]);
      if (runsRes.status === 'fulfilled') recentRuns = runsRes.value.runs;
      if (projRes.status === 'fulfilled') projects = projRes.value;
    } catch { /* silent */ }
  }

  // Build searchable items
  interface CommandItem {
    id: string;
    label: string;
    group: 'Pages' | 'Actions' | 'Recent Runs' | 'Projects' | 'Agents';
    icon: string;
    action: () => void;
  }

  let allItems = $derived<CommandItem[]>([
    // Pages
    { id: 'page-command', label: 'Pulse / Command Center', group: 'Pages', icon: '◎', action: () => navigate('/command') },
    { id: 'page-stream', label: 'Work Stream', group: 'Pages', icon: '☰', action: () => navigate('/stream') },
    { id: 'page-decide', label: 'Decisions', group: 'Pages', icon: '✓', action: () => navigate('/decide') },
    { id: 'page-config', label: 'Configuration', group: 'Pages', icon: '⚙', action: () => navigate('/config') },
    { id: 'page-agents', label: 'Agent Observatory', group: 'Pages', icon: '◉', action: () => navigate('/agents') },
    { id: 'page-analytics', label: 'Analytics', group: 'Pages', icon: '▤', action: () => navigate('/analytics') },
    // Actions
    { id: 'action-trigger', label: 'Trigger Run', group: 'Actions', icon: '▶', action: async () => {
      try { await triggerRun(); toastSuccess('Run triggered'); } catch (e: any) { toastError(`Failed: ${e.message}`); }
    }},
    { id: 'action-panel', label: 'Toggle Agent Panel', group: 'Actions', icon: '◧', action: () => togglePanel() },
    // Recent Runs
    ...recentRuns.map(r => ({
      id: `run-${r.run_id}`,
      label: `Run ${r.run_id.slice(0, 8)}${r.issue_number ? ` #${r.issue_number}` : ''} — ${r.verdict ?? r.status ?? 'unknown'}`,
      group: 'Recent Runs' as const,
      icon: r.verdict === 'APPROVE' ? '✓' : r.verdict === 'REJECT' ? '✗' : '●',
      action: () => navigate(`/stream/${r.run_id}`),
    })),
    // Projects
    ...projects.map(p => ({
      id: `project-${p.id}`,
      label: `${p.repo.split('/').pop() ?? p.repo}${p.enabled ? '' : ' (disabled)'}`,
      group: 'Projects' as const,
      icon: '📁',
      action: () => navigate(`/config/projects`),
    })),
    // Agents
    ...agentPresence.agents.map(a => ({
      id: `agent-${a.name}`,
      label: `${a.name} — ${a.status}`,
      group: 'Agents' as const,
      icon: a.role === 'manager' ? '👔' : '🤖',
      action: () => togglePanel(a.name),
    })),
  ]);

  let fuse = $derived(new Fuse(allItems, {
    keys: ['label', 'group'],
    threshold: 0.4,
    includeScore: true,
  }));

  let results = $derived<CommandItem[]>(
    query.trim()
      ? fuse.search(query).map(r => r.item)
      : allItems
  );

  // Group results
  let groupedResults = $derived(() => {
    const groups = new Map<string, CommandItem[]>();
    for (const item of results) {
      const group = groups.get(item.group) ?? [];
      group.push(item);
      groups.set(item.group, group);
    }
    return groups;
  });

  let flatResults = $derived(results);

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      onclose();
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      selectedIndex = Math.min(selectedIndex + 1, flatResults.length - 1);
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      selectedIndex = Math.max(selectedIndex - 1, 0);
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      const item = flatResults[selectedIndex];
      if (item) {
        item.action();
        onclose();
      }
      return;
    }
  }

  function handleSelect(item: CommandItem) {
    item.action();
    onclose();
  }

  function portal(node: HTMLElement) {
    document.body.appendChild(node);
    return {
      destroy() {
        node.remove();
      }
    };
  }
</script>

{#if open}
  <div
    use:portal
    class="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-start justify-center pt-[15vh] z-modal"
    role="dialog"
    aria-modal="true"
    aria-label="Command palette"
    tabindex="-1"
    onclick={(e) => { if (e.target === e.currentTarget) onclose(); }}
    onkeydown={handleKeydown}
  >
    <div class="w-full max-w-lg mx-4 glass rounded-xl shadow-2xl border border-border/50 overflow-hidden animate-fade-in-up">
      <!-- Search input -->
      <div class="flex items-center gap-2 px-4 py-3 border-b border-border/50">
        <svg class="w-4 h-4 text-text-muted shrink-0" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
          <circle cx="6.5" cy="6.5" r="4.5" />
          <line x1="10" y1="10" x2="14" y2="14" />
        </svg>
        <input
          bind:this={inputEl}
          bind:value={query}
          type="text"
          placeholder="Search pages, runs, projects, actions..."
          class="flex-1 bg-transparent text-sm text-text outline-none placeholder:text-text-muted"
        />
        <kbd class="text-[10px] text-text-muted px-1.5 py-0.5 rounded border border-border-subtle">Esc</kbd>
      </div>

      <!-- Results -->
      <div class="max-h-[50vh] overflow-auto py-1">
        {#if flatResults.length === 0}
          <div class="px-4 py-6 text-center text-xs text-text-muted">No results found</div>
        {:else}
          {#each [...groupedResults()] as [groupName, items]}
            <div class="px-3 pt-2 pb-1">
              <span class="text-[10px] font-semibold text-text-muted uppercase tracking-wider">{groupName}</span>
            </div>
            {#each items as item, i}
              {@const flatIndex = flatResults.indexOf(item)}
              <button
                onclick={() => handleSelect(item)}
                class="w-full flex items-center gap-3 px-4 py-2 text-left text-sm cursor-pointer transition-colors
                  {flatIndex === selectedIndex ? 'bg-white/[0.06] text-text' : 'text-text-dim hover:bg-white/[0.03]'}"
              >
                <span class="w-5 text-center text-xs opacity-60">{item.icon}</span>
                <span class="flex-1 truncate">{item.label}</span>
                {#if flatIndex === selectedIndex}
                  <kbd class="text-[10px] text-text-muted px-1 py-0.5 rounded border border-border-subtle">↵</kbd>
                {/if}
              </button>
            {/each}
          {/each}
        {/if}
      </div>

      <!-- Footer -->
      <div class="flex items-center gap-3 px-4 py-2 border-t border-border/50 text-[10px] text-text-muted">
        <span><kbd class="px-1 py-0.5 rounded border border-border-subtle">↑↓</kbd> Navigate</span>
        <span><kbd class="px-1 py-0.5 rounded border border-border-subtle">↵</kbd> Select</span>
        <span><kbd class="px-1 py-0.5 rounded border border-border-subtle">Esc</kbd> Close</span>
      </div>
    </div>
  </div>
{/if}
