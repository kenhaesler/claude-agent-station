<script lang="ts">
  import { themeStore } from '../lib/theme.svelte';

  let expanded = $state(false);

  function selectTheme(id: string) {
    themeStore.setTheme(id);
    expanded = false;
  }

  function handleClickOutside(e: MouseEvent) {
    const target = e.target as HTMLElement;
    if (!target.closest('.theme-switcher')) {
      expanded = false;
    }
  }

  /** 5-swatch preview of a theme's key colors */
  function swatches(colors: Record<string, string>): string[] {
    return [
      colors['--color-bg'],
      colors['--color-surface'],
      colors['--color-text'],
      colors['--color-info'],
      colors['--color-approve'],
    ];
  }
</script>

<svelte:window onclick={handleClickOutside} />

<div class="theme-switcher relative">
  <button
    onclick={() => expanded = !expanded}
    class="flex items-center gap-2 px-3 py-2 text-xs font-medium rounded-lg
      bg-[var(--color-surface)] border border-[var(--color-border-subtle)]
      text-[var(--color-text)] hover:border-[var(--color-border)]
      transition-colors cursor-pointer"
    title="Switch theme"
  >
    <!-- Palette icon -->
    <svg class="w-4 h-4 shrink-0 text-[var(--color-text-dim)]" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="8" cy="8" r="6" />
      <circle cx="6" cy="6" r="1.2" fill="currentColor" stroke="none" />
      <circle cx="10" cy="6" r="1.2" fill="currentColor" stroke="none" />
      <circle cx="8" cy="10" r="1.2" fill="currentColor" stroke="none" />
      <circle cx="5" cy="9" r="1.2" fill="currentColor" stroke="none" />
    </svg>
    <span class="hidden sm:inline">{themeStore.theme.label}</span>
    <svg class="w-3 h-3 text-[var(--color-text-muted)] transition-transform {expanded ? 'rotate-180' : ''}" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M3 5l3 3 3-3" />
    </svg>
  </button>

  {#if expanded}
    <div class="absolute right-0 top-full mt-1.5 z-50 w-64 max-h-[420px] overflow-y-auto
      rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)]
      shadow-lg shadow-black/30 animate-fade-in-up"
      style="animation-duration: 0.15s"
    >
      <div class="p-1.5 space-y-0.5">
        {#each themeStore.themes as t}
          <button
            onclick={() => selectTheme(t.id)}
            class="w-full flex items-center gap-3 px-2.5 py-2 rounded-md text-left transition-colors cursor-pointer
              {themeStore.id === t.id
                ? 'bg-[var(--color-info)]/15 border border-[var(--color-info)]/30'
                : 'hover:bg-[var(--color-surface)] border border-transparent'}"
          >
            <!-- Color swatch strip -->
            <div class="flex shrink-0 rounded overflow-hidden border border-[var(--color-border-subtle)]">
              {#each swatches(t.colors) as color}
                <div class="w-3 h-5" style="background: {color}"></div>
              {/each}
            </div>
            <div class="min-w-0 flex-1">
              <div class="text-xs font-medium text-[var(--color-text)] truncate">{t.label}</div>
              <div class="text-[10px] text-[var(--color-text-muted)] truncate">{t.description}</div>
            </div>
            {#if themeStore.id === t.id}
              <svg class="w-3.5 h-3.5 text-[var(--color-info)] shrink-0" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <path d="M3 8.5l3 3 7-7" />
              </svg>
            {/if}
          </button>
        {/each}
      </div>

      <!-- Reset to system -->
      <div class="border-t border-[var(--color-border-subtle)] p-1.5">
        <button
          onclick={() => { themeStore.resetToSystem(); expanded = false; }}
          class="w-full px-2.5 py-1.5 text-[10px] text-[var(--color-text-muted)]
            hover:text-[var(--color-text)] hover:bg-[var(--color-surface)]
            rounded-md transition-colors cursor-pointer text-left"
        >
          Reset to system preference
        </button>
      </div>
    </div>
  {/if}
</div>
