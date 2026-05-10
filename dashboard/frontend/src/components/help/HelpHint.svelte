<script lang="ts">
  import { openHelpDrawer } from '../../lib/help-drawer.svelte';

  let {
    section,
    label = 'Help',
  }: {
    section: string;
    label?: string;
  } = $props();
</script>

<button
  type="button"
  class="help-hint"
  aria-label="Help: {label}"
  onclick={(e) => { e.stopPropagation(); openHelpDrawer(section); }}
>?</button>

<style>
  .help-hint {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 14px;
    height: 14px;
    margin-left: 0.35rem;
    padding: 0;
    border: 1px solid var(--color-border);
    border-radius: 999px;
    background: transparent;
    color: var(--color-text-dim);
    font-size: 9px;
    line-height: 1;
    font-weight: 700;
    cursor: pointer;
    vertical-align: middle;
    transition: color 120ms, border-color 120ms;
  }
  .help-hint:hover,
  .help-hint:focus-visible {
    color: var(--color-text);
    border-color: var(--color-link, currentColor);
    outline: none;
  }
</style>
