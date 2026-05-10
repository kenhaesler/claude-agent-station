<script lang="ts">
  import SlidePanel from '../overlays/SlidePanel.svelte';
  import HelpSection from './HelpSection.svelte';
  import { helpDrawer, closeHelpDrawer } from '../../lib/help-drawer.svelte';
  import { navigate } from '../../lib/router.svelte';

  const TITLES: Record<string, string> = {
    'run-lifecycle': 'Run lifecycle',
    'roles': 'The three roles',
    'verdicts': 'Verdicts',
    'eligibility': 'Issue eligibility',
    'throttling': 'Plan-tier throttling',
    'plans-worktrees': 'Plans & worktrees',
    'pages-tour': 'Page-by-page tour',
    'troubleshooting': 'Troubleshooting',
  };

  const open = $derived(helpDrawer.openSection !== null);
  const sectionId = $derived(helpDrawer.openSection ?? '');
  const title = $derived(TITLES[sectionId] ?? 'Help');

  function viewFullPage() {
    const target = sectionId;
    closeHelpDrawer();
    navigate(`/help#${target}`);
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
