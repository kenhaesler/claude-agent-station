<script lang="ts">
  import type { DiffFile } from '../../lib/types';

  let {
    files,
  }: {
    files: DiffFile[];
  } = $props();

  let expanded = $state<Record<string, boolean>>({});

  function toggle(filename: string) {
    expanded[filename] = !expanded[filename];
  }

  function isExpanded(filename: string): boolean {
    return expanded[filename] ?? true;
  }
</script>

<div class="flex flex-col gap-3">
  {#each files as file}
    <div class="glass rounded-lg overflow-hidden">
      <!-- File header -->
      <button
        class="w-full flex items-center gap-2 px-3 py-2 text-left bg-surface-2 hover:bg-surface-3 transition-colors duration-100"
        onclick={() => toggle(file.filename)}
      >
        <span class="text-text-muted text-xs">{isExpanded(file.filename) ? '\u25BC' : '\u25B6'}</span>
        <span class="font-data text-xs text-text-dim truncate flex-1">{file.filename}</span>
        {#if file.is_new}
          <span class="text-[10px] text-approve font-medium uppercase">new</span>
        {:else if file.is_deleted}
          <span class="text-[10px] text-reject font-medium uppercase">deleted</span>
        {/if}
        <span class="text-xs text-approve font-data">+{file.additions}</span>
        <span class="text-xs text-reject font-data">-{file.deletions}</span>
      </button>

      <!-- Diff content -->
      {#if isExpanded(file.filename)}
        {#if file.is_binary}
          <div class="px-3 py-4 text-xs text-text-muted text-center">Binary file</div>
        {:else}
          <div class="overflow-x-auto">
            {#each file.hunks as hunk}
              <div class="text-[11px] font-data text-text-muted bg-info/5 px-3 py-0.5 border-y border-border-subtle/30">
                {hunk.header}
              </div>
              {#each hunk.lines as line}
                <div
                  class="font-data text-[12px] leading-5 px-3 whitespace-pre
                         {line.type === 'add' ? 'bg-approve/8 text-approve' : ''}
                         {line.type === 'remove' ? 'bg-reject/8 text-reject' : ''}
                         {line.type === 'context' ? 'text-text-muted' : ''}"
                >
                  <span class="inline-block w-8 text-right text-text-muted/50 select-none mr-1">{line.old_line ?? ' '}</span>
                  <span class="inline-block w-8 text-right text-text-muted/50 select-none mr-2">{line.new_line ?? ' '}</span>
                  <span>{line.type === 'add' ? '+' : line.type === 'remove' ? '-' : ' '}{line.content}</span>
                </div>
              {/each}
            {/each}
          </div>
        {/if}
      {/if}
    </div>
  {/each}
</div>
