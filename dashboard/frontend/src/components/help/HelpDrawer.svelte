<script lang="ts">
  import SlidePanel from '../overlays/SlidePanel.svelte';
  import HelpSection from './HelpSection.svelte';
  import { helpDrawer, closeHelpDrawer } from '../../lib/help-drawer.svelte';
  import { navigate } from '../../lib/router.svelte';
  import { HELP_SECTION_TITLES } from '../../lib/help-sections';

  const open = $derived(helpDrawer.openSection !== null);
  const sectionId = $derived(helpDrawer.openSection ?? '');
  const title = $derived(HELP_SECTION_TITLES[sectionId] ?? 'Help');

  function viewFullPage() {
    const target = sectionId;
    closeHelpDrawer();
    navigate(`/help#${target}`);
    // navigate() updates URL+route but does not fire `hashchange` (it uses
    // pushState). When already on /help, HelpPage stays mounted and only
    // re-scrolls in response to hashchange — so dispatch one explicitly.
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    }
  }
</script>

<SlidePanel {open} onClose={closeHelpDrawer} {title} width="w-[480px]">
  {#if sectionId}
    <HelpSection id={sectionId} />
    <div class="full-link">
      <button type="button" onclick={viewFullPage}>View full Help page →</button>
    </div>
  {/if}
</SlidePanel>

<style>
  .full-link {
    margin-top: 1.25rem;
    padding-top: 1rem;
    border-top: 1px solid var(--color-border);
  }
  .full-link button {
    background: none;
    border: none;
    padding: 0;
    color: var(--color-link, #06b6d4);
    font-size: 0.9rem;
    cursor: pointer;
    text-decoration: underline;
    text-underline-offset: 2px;
  }
</style>
