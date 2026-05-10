<script lang="ts">
  import { openHelpDrawer } from '../../lib/help-drawer.svelte';
  import { appearance } from '../../lib/appearance.svelte';

  let { source }: { source: string } = $props();

  let host = $state<HTMLDivElement | null>(null);
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

  // Resolve a CSS custom property to its computed color, falling back if
  // the variable isn't defined. Mermaid's theme engine needs literal
  // hex/rgb values — it cannot parse `var(--x)`.
  function resolveColor(varName: string, fallback: string): string {
    if (typeof window === 'undefined') return fallback;
    const v = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    return v || fallback;
  }

  // Re-render whenever the theme changes. Mermaid bakes themeVariables into
  // the SVG at render time, so a `var(--x)` reference can't carry the
  // toggle through; we resolve and re-render instead. The reactive read
  // of `appearance.theme` is what wires this `$effect` to the rune.
  $effect(() => {
    // eslint-disable-next-line @typescript-eslint/no-unused-expressions
    appearance.theme; // dependency
    if (!host) return;

    let cancelled = false;
    (async () => {
      try {
        const mermaid = (await import('mermaid')).default;
        if (cancelled || !host) return;
        mermaid.initialize({
          startOnLoad: false,
          theme: 'base',
          securityLevel: 'loose',
          themeVariables: {
            // Node fill: surface-1 (translucent, theme-matched).
            primaryColor: resolveColor('--color-surface-1', '#1f2937'),
            // Node text: --color-primary (high-contrast text token).
            primaryTextColor: resolveColor('--color-primary', '#e5e7eb'),
            // Node border + edges: --color-primary again. The `--color-border`
            // token is a 10% / 30% alpha rgba designed for subtle hairlines
            // on glass surfaces — Mermaid edges need to be readable, so use
            // the higher-contrast text token instead.
            primaryBorderColor: resolveColor('--color-primary', '#374151'),
            lineColor: resolveColor('--color-primary', '#6b7280'),
            fontFamily: 'inherit',
          },
        });
        const id = `mermaid-${Math.random().toString(36).slice(2)}`;
        const { svg, bindFunctions } = await mermaid.render(id, source);
        if (cancelled || !host) return;
        host.innerHTML = svg;
        bindFunctions?.(host);
        error = null;
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
