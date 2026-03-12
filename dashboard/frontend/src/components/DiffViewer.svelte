<script lang="ts">
  import type { DiffResult, DiffFile } from '../lib/types';

  interface Props {
    diff: DiffResult;
  }
  let { diff }: Props = $props();

  // Track which files are expanded (collapse all by default for large diffs)
  let expandedFiles = $state<Set<string>>(new Set());

  // Auto-expand files if total file count is small
  $effect(() => {
    if (diff.total_files > 0 && diff.total_files <= 10) {
      expandedFiles = new Set(diff.files.map(f => f.filename));
    } else {
      expandedFiles = new Set();
    }
  });

  function toggleFile(filename: string) {
    const next = new Set(expandedFiles);
    if (next.has(filename)) {
      next.delete(filename);
    } else {
      next.add(filename);
    }
    expandedFiles = next;
  }

  function expandAll() {
    expandedFiles = new Set(diff.files.map(f => f.filename));
  }

  function collapseAll() {
    expandedFiles = new Set();
  }

  function fileExtension(filename: string): string {
    const parts = filename.split('.');
    return parts.length > 1 ? parts[parts.length - 1] : '';
  }

  function fileIcon(file: DiffFile): string {
    if (file.is_binary) return '📦';
    if (file.is_new) return '✨';
    if (file.is_deleted) return '🗑';
    return '📄';
  }

  function fileBadgeClass(file: DiffFile): string {
    if (file.is_new) return 'text-emerald-400 bg-emerald-400/10';
    if (file.is_deleted) return 'text-red-400 bg-red-400/10';
    return 'text-blue-400 bg-blue-400/10';
  }

  function fileBadgeText(file: DiffFile): string {
    if (file.is_new) return 'NEW';
    if (file.is_deleted) return 'DELETED';
    if (file.old_filename) return 'RENAMED';
    return 'MODIFIED';
  }
</script>

{#if diff.total_files === 0}
  <div class="text-text-dim text-sm py-4 text-center">
    No code changes available for this run.
  </div>
{:else}
  <!-- Summary Bar -->
  <div class="flex flex-wrap items-center justify-between gap-2 mb-4">
    <div class="flex items-center gap-3 text-sm">
      <span class="text-text-dim">{diff.total_files} file{diff.total_files !== 1 ? 's' : ''} changed</span>
      {#if diff.total_additions > 0}
        <span class="text-emerald-400 font-data">+{diff.total_additions}</span>
      {/if}
      {#if diff.total_deletions > 0}
        <span class="text-red-400 font-data">-{diff.total_deletions}</span>
      {/if}
    </div>
    <div class="flex gap-2">
      <button
        class="text-xs px-2 py-1 rounded glass hover:bg-white/[0.05] text-text-dim transition-colors"
        onclick={expandAll}
      >
        Expand All
      </button>
      <button
        class="text-xs px-2 py-1 rounded glass hover:bg-white/[0.05] text-text-dim transition-colors"
        onclick={collapseAll}
      >
        Collapse All
      </button>
    </div>
  </div>

  <!-- File List -->
  <div class="space-y-2">
    {#each diff.files as file (file.filename)}
      <div class="rounded-lg border border-border/30 overflow-hidden bg-surface/30">
        <!-- File Header -->
        <button
          class="w-full flex items-center gap-2 px-4 py-2.5 text-left hover:bg-white/[0.02] transition-colors"
          onclick={() => toggleFile(file.filename)}
          aria-expanded={expandedFiles.has(file.filename)}
        >
          <!-- Expand/Collapse Arrow -->
          <span class="text-text-dim text-xs transition-transform {expandedFiles.has(file.filename) ? 'rotate-90' : ''}">
            ▶
          </span>

          <!-- File icon -->
          <span class="text-xs">{fileIcon(file)}</span>

          <!-- Filename -->
          <span class="font-data text-sm text-text flex-1 truncate">
            {#if file.old_filename}
              <span class="text-text-dim">{file.old_filename}</span>
              <span class="text-text-dim mx-1">→</span>
            {/if}
            {file.filename}
          </span>

          <!-- Badge -->
          <span class="text-[10px] font-semibold px-1.5 py-0.5 rounded {fileBadgeClass(file)}">
            {fileBadgeText(file)}
          </span>

          <!-- Stats -->
          {#if !file.is_binary}
            <span class="flex items-center gap-1.5 text-xs font-data">
              {#if file.additions > 0}
                <span class="text-emerald-400">+{file.additions}</span>
              {/if}
              {#if file.deletions > 0}
                <span class="text-red-400">-{file.deletions}</span>
              {/if}
            </span>
          {/if}
        </button>

        <!-- File Content (Hunks) -->
        {#if expandedFiles.has(file.filename)}
          <div class="border-t border-border/20">
            {#if file.is_binary}
              <div class="px-4 py-3 text-text-dim text-sm italic">Binary file changed</div>
            {:else if file.hunks.length === 0}
              <div class="px-4 py-3 text-text-dim text-sm italic">No content changes (file mode or permissions change)</div>
            {:else}
              {#each file.hunks as hunk, hunkIdx}
                <!-- Hunk Header -->
                <div class="px-4 py-1 text-xs font-data text-purple-400/70 bg-purple-400/5 border-b border-border/10 select-none">
                  {hunk.header}
                </div>
                <!-- Hunk Lines -->
                <div class="overflow-x-auto">
                  <table class="w-full text-xs font-data leading-5">
                    <tbody>
                      {#each hunk.lines as line}
                        <tr class="{
                          line.type === 'add' ? 'bg-emerald-500/8' :
                          line.type === 'remove' ? 'bg-red-500/8' :
                          ''
                        }">
                          <!-- Old line number -->
                          <td class="w-12 text-right pr-1 text-text-dim/40 select-none border-r border-border/10 {
                            line.type === 'add' ? 'bg-emerald-500/5' :
                            line.type === 'remove' ? 'bg-red-500/5' : ''
                          }">
                            {line.type !== 'add' && line.old_line != null ? line.old_line : ''}
                          </td>
                          <!-- New line number -->
                          <td class="w-12 text-right pr-2 text-text-dim/40 select-none border-r border-border/10 {
                            line.type === 'add' ? 'bg-emerald-500/5' :
                            line.type === 'remove' ? 'bg-red-500/5' : ''
                          }">
                            {line.type !== 'remove' && line.new_line != null ? line.new_line : ''}
                          </td>
                          <!-- +/- indicator -->
                          <td class="w-5 text-center select-none {
                            line.type === 'add' ? 'text-emerald-400' :
                            line.type === 'remove' ? 'text-red-400' :
                            'text-transparent'
                          }">
                            {line.type === 'add' ? '+' : line.type === 'remove' ? '-' : ' '}
                          </td>
                          <!-- Content -->
                          <td class="whitespace-pre pl-1 {
                            line.type === 'add' ? 'text-emerald-300' :
                            line.type === 'remove' ? 'text-red-300' :
                            'text-text/70'
                          }">{line.content}</td>
                        </tr>
                      {/each}
                    </tbody>
                  </table>
                </div>
              {/each}
            {/if}
          </div>
        {/if}
      </div>
    {/each}
  </div>
{/if}
