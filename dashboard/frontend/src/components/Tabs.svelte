<script lang="ts">
  interface Props {
    tabs: { id: string; label: string; icon?: string; badge?: number }[];
    activeTab: string;
    onTabChange: (id: string) => void;
    class?: string;
  }

  let { tabs, activeTab, onTabChange, class: className = '' }: Props = $props();

  function handleKeydown(e: KeyboardEvent, index: number) {
    let nextIndex = index;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault();
      nextIndex = (index + 1) % tabs.length;
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault();
      nextIndex = (index - 1 + tabs.length) % tabs.length;
    } else if (e.key === 'Home') {
      e.preventDefault();
      nextIndex = 0;
    } else if (e.key === 'End') {
      e.preventDefault();
      nextIndex = tabs.length - 1;
    } else {
      return;
    }
    onTabChange(tabs[nextIndex].id);
    // Focus the new tab button
    const tablist = (e.target as HTMLElement).closest('[role="tablist"]');
    const buttons = tablist?.querySelectorAll('[role="tab"]');
    (buttons?.[nextIndex] as HTMLElement)?.focus();
  }
</script>

<div role="tablist" aria-orientation="horizontal" class="flex items-center gap-1 border-b border-border {className}">
  {#each tabs as tab, i}
    <button
      role="tab"
      aria-selected={activeTab === tab.id}
      aria-controls="tabpanel-{tab.id}"
      id="tab-{tab.id}"
      tabindex={activeTab === tab.id ? 0 : -1}
      onclick={() => onTabChange(tab.id)}
      onkeydown={(e) => handleKeydown(e, i)}
      class="relative px-3 py-2 text-xs font-medium transition-colors cursor-pointer
        {activeTab === tab.id
          ? 'text-text'
          : 'text-text-muted hover:text-text-dim'}"
    >
      {tab.label}
      {#if tab.badge != null && tab.badge > 0}
        <span class="ml-1 inline-flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full bg-warning text-[10px] font-bold text-bg">
          {tab.badge}
        </span>
      {/if}
      {#if activeTab === tab.id}
        <span class="absolute bottom-0 left-1 right-1 h-0.5 bg-info rounded-t"></span>
      {/if}
    </button>
  {/each}
</div>
