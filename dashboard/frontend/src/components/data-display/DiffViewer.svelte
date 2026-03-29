<script lang="ts">
  import type { DiffFile } from '../../lib/types';

  let {
    files,
  }: {
    files: DiffFile[];
  } = $props();

  let expanded = $state<Record<string, boolean>>({});

  function toggle(path: string) {
    expanded[path] = !expanded[path];
  }

  function isExpanded(path: string): boolean {
    return expanded[path] ?? true;
  }

  function hunkHeader(hunk: DiffFile['hunks'][0]): string {
    return `@@ -${hunk.old_start},${hunk.old_count} +${hunk.new_start},${hunk.new_count} @@`;
  }
</script>

<div class="flex flex-col gap-3">
  {#each files as file}
    <div class="glass rounded-lg overflow-hidden">
      <!-- File header -->
      <button
        class="w-full flex items-center gap-2 px-3 py-2 text-left bg-surface-2 hover:bg-surface-3 transition-colors duration-100"
        onclick={() => toggle(file.path)}
      >
        <span class="text-tertiary text-xs">{isExpanded(file.path) ? '\u25BC' : '\u25B6'}</span>
        <span class="font-mono text-xs text-secondary truncate flex-1">{file.path}</span>
        {#if file.status === 'added'}
          <span class="text-[10px] text-approve font-medium uppercase">new</span>
        {:else if file.status === 'deleted'}
          <span class="text-[10px] text-reject font-medium uppercase">deleted</span>
        {/if}
        <span class="text-xs text-approve font-mono">+{file.additions}</span>
        <span class="text-xs text-reject font-mono">-{file.deletions}</span>
      </button>

      <!-- Diff content -->
      {#if isExpanded(file.path)}
        <div class="overflow-x-auto">
          {#each file.hunks as hunk}
            <div class="text-[11px] font-mono text-tertiary bg-info/5 px-3 py-0.5 border-y border-border/30">
              {hunkHeader(hunk)}
            </div>
            {#each hunk.lines as line}
              <div
                class="font-mono text-[12px] leading-5 px-3 whitespace-pre
                       {line.type === 'add' ? 'bg-approve/8 text-approve' : ''}
                       {line.type === 'delete' ? 'bg-reject/8 text-reject' : ''}
                       {line.type === 'context' ? 'text-tertiary' : ''}"
              >
                <span class="inline-block w-8 text-right text-tertiary/50 select-none mr-1">{line.old_line ?? ' '}</span>
                <span class="inline-block w-8 text-right text-tertiary/50 select-none mr-2">{line.new_line ?? ' '}</span>
                <span>{line.type === 'add' ? '+' : line.type === 'delete' ? '-' : ' '}{line.content}</span>
              </div>
            {/each}
          {/each}
        </div>
      {/if}
    </div>
  {/each}
</div>
