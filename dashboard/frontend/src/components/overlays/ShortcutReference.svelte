<script lang="ts">
  import Modal from './Modal.svelte';

  let {
    show = false,
    onClose,
  }: {
    show: boolean;
    onClose: () => void;
  } = $props();

  const sections = [
    {
      title: 'Navigation',
      shortcuts: [
        { keys: '1', desc: 'Command Center' },
        { keys: '2', desc: 'Agent Theater' },
        { keys: '3', desc: 'Runs' },
        { keys: '4', desc: 'Queue Board' },
        { keys: '5', desc: 'Intelligence Hub' },
        { keys: '6', desc: 'Projects' },
        { keys: '7', desc: 'Integration' },
        { keys: '8', desc: 'Settings' },
      ],
    },
    {
      title: 'Global',
      shortcuts: [
        { keys: 'Cmd+K', desc: 'Command Palette' },
        { keys: '?', desc: 'Shortcut Reference' },
        { keys: 'Esc', desc: 'Close overlay' },
      ],
    },
    {
      title: 'Agent Theater',
      shortcuts: [
        { keys: 'j / k', desc: 'Navigate agents' },
        { keys: 'g', desc: 'Focus guidance input' },
        { keys: 'l', desc: 'Toggle log panel' },
      ],
    },
    {
      title: 'Tables & Lists',
      shortcuts: [
        { keys: 'j / k', desc: 'Move selection' },
        { keys: 'Enter', desc: 'Open selected' },
        { keys: '/', desc: 'Focus search' },
      ],
    },
  ];
</script>

<Modal {show} {onClose} title="Keyboard Shortcuts" maxWidth="max-w-xl">
  <div class="grid grid-cols-1 sm:grid-cols-2 gap-6">
    {#each sections as section}
      <div>
        <h3 class="text-xs font-semibold text-text-dim uppercase tracking-wider mb-2">{section.title}</h3>
        <div class="space-y-1">
          {#each section.shortcuts as shortcut}
            <div class="flex items-center justify-between text-xs">
              <span class="text-text-dim">{shortcut.desc}</span>
              <kbd class="px-1.5 py-0.5 rounded bg-surface-2 text-text-muted font-mono text-[10px]">{shortcut.keys}</kbd>
            </div>
          {/each}
        </div>
      </div>
    {/each}
  </div>
</Modal>
