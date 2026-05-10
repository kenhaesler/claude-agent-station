<script lang="ts">
  import { onMount } from 'svelte';
  import { openHelpDrawer } from '../../lib/help-drawer.svelte';

  let { source }: { source: string } = $props();

  let host: HTMLDivElement;
  let error = $state<string | null>(null);

  // Mermaid `click <node> call openHelpDrawer("section")` directives need a
  // global function reference. Expose it on `window` once.
  //
  // Trust boundary: this is safe ONLY because Mermaid `source` is bundled
  // markdown loaded via `import.meta.glob`, never user-provided at runtime.
  // If a future caller passes user-controlled markdown to this component,
  // `securityLevel: 'loose'` lets the diagram source invoke any window
  // callback by name — at which point this exposure must be reworked.
  if (typeof window !== 'undefined') {
    (window as unknown as { openHelpDrawer?: (s: string) => void }).openHelpDrawer = openHelpDrawer;
  }

  onMount(() => {
    let cancelled = false;

    (async () => {
      try {
        const mermaid = (await import('mermaid')).default;
        if (cancelled) return;
        mermaid.initialize({
          startOnLoad: false,
          theme: 'base',
          securityLevel: 'loose',
          themeVariables: {
            primaryColor: 'var(--color-surface-1, #1f2937)',
            primaryTextColor: 'var(--color-text, #e5e7eb)',
            primaryBorderColor: 'var(--color-border, #374151)',
            lineColor: 'var(--color-border, #6b7280)',
            fontFamily: 'inherit',
          },
        });
        const id = `mermaid-${Math.random().toString(36).slice(2)}`;
        const { svg, bindFunctions } = await mermaid.render(id, source);
        if (cancelled || !host) return;
        host.innerHTML = svg;
        bindFunctions?.(host);
      } catch (e) {
        if (cancelled) return;
        error = e instanceof Error ? e.message : String(e);
      }
    })();

    return () => { cancelled = true; };
  });
</script>

<div class="mermaid-host" bind:this={host}>
  {#if error}
    <pre class="mermaid-fallback">{source}</pre>
    <p class="mermaid-error" role="alert">Diagram failed to render: {error}</p>
  {/if}
</div>

<style>
  .mermaid-host { display: flex; justify-content: center; margin: 1rem 0; }
  .mermaid-host :global(svg) { max-width: 100%; height: auto; cursor: default; }
  .mermaid-fallback {
    background: var(--color-pre-bg);
    border: 1px solid var(--color-pre-border);
    border-radius: 0.5em;
    padding: 0.75em 1em;
    overflow-x: auto;
    font-size: 0.85em;
  }
  .mermaid-error { color: var(--abort, #dc2626); font-size: 0.85em; margin-top: 0.5em; }
</style>
