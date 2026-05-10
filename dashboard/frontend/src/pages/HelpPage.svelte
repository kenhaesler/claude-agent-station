<script lang="ts">
  import HelpSection from '../components/help/HelpSection.svelte';
  import { onMount } from 'svelte';

  const SECTIONS: Array<{ id: string; title: string }> = [
    { id: 'run-lifecycle', title: 'Run lifecycle' },
    { id: 'roles', title: 'The three roles' },
    { id: 'verdicts', title: 'Verdicts' },
    { id: 'eligibility', title: 'Issue eligibility' },
    { id: 'throttling', title: 'Plan-tier throttling' },
    { id: 'plans-worktrees', title: 'Plans & worktrees' },
    { id: 'pages-tour', title: 'Page-by-page tour' },
    { id: 'troubleshooting', title: 'Troubleshooting' },
  ];

  onMount(() => {
    if (window.location.hash) {
      const id = window.location.hash.slice(1);
      requestAnimationFrame(() => {
        document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    }
  });
</script>

<div class="help-layout">
  <aside class="help-sidebar">
    <h2>Help</h2>
    <ul>
      {#each SECTIONS as s}
        <li><a href="#{s.id}">{s.title}</a></li>
      {/each}
    </ul>
  </aside>

  <main class="help-main">
    <h1>Claude Agent Station — Help</h1>
    <p class="lede">How the station works, layered for end users, operators, and contributors.</p>

    {#each SECTIONS as s}
      <section id={s.id} class="help-section-block">
        <h2>{s.title}</h2>
        <HelpSection id={s.id} />
      </section>
    {/each}
  </main>
</div>

<style>
  .help-layout {
    display: grid;
    grid-template-columns: 220px 1fr;
    gap: 2rem;
    padding: 1.5rem;
    max-width: 1100px;
    margin: 0 auto;
  }
  .help-sidebar {
    position: sticky;
    top: 1rem;
    align-self: start;
    border-right: 1px solid var(--color-border);
    padding-right: 1rem;
  }
  .help-sidebar h2 {
    font-size: 0.7rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--color-text-dim);
    margin-bottom: 0.75rem;
  }
  .help-sidebar ul { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.25rem; }
  .help-sidebar a {
    color: var(--color-text-dim);
    text-decoration: none;
    font-size: 0.9rem;
    padding: 0.25rem 0;
    display: block;
  }
  .help-sidebar a:hover { color: var(--color-text); }

  .help-main h1 { font-size: 1.5rem; margin: 0 0 0.25rem 0; }
  .lede { color: var(--color-text-dim); margin-bottom: 1.5rem; }
  .help-section-block { margin-top: 2.5rem; scroll-margin-top: 1rem; }
  .help-section-block > h2 {
    font-size: 1.2rem;
    margin: 0 0 0.75rem 0;
    padding-bottom: 0.4rem;
    border-bottom: 1px solid var(--color-border);
  }

  @media (max-width: 720px) {
    .help-layout { grid-template-columns: 1fr; }
    .help-sidebar { position: static; border-right: none; border-bottom: 1px solid var(--color-border); padding-right: 0; padding-bottom: 0.75rem; }
    .help-sidebar ul { flex-direction: row; flex-wrap: wrap; gap: 0.5rem 1rem; }
  }
</style>
