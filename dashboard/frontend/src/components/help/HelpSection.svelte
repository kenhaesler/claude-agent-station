<script lang="ts">
  import { splitHelpSection } from '../../lib/help-content';
  import MarkdownRenderer from '../data-display/MarkdownRenderer.svelte';
  import MermaidDiagram from './MermaidDiagram.svelte';

  let { id }: { id: string } = $props();

  // Eagerly-imported raw markdown for all help sections.
  const sources = import.meta.glob('../../content/help/*.md', {
    query: '?raw',
    import: 'default',
    eager: true,
  }) as Record<string, string>;

  function lookup(sectionId: string): string | null {
    const key = Object.keys(sources).find((p) => p.endsWith(`/${sectionId}.md`));
    return key ? sources[key] : null;
  }

  const raw = $derived(lookup(id));
  const parts = $derived(raw ? splitHelpSection(raw) : null);

  // Split the how-it-works body around fenced ```mermaid blocks so we can
  // render each block with <MermaidDiagram> and the surrounding prose with
  // <MarkdownRenderer>.
  type Chunk = { kind: 'md' | 'mermaid'; content: string };

  function chunkBody(body: string): Chunk[] {
    const re = /```mermaid\n([\s\S]*?)```/g;
    const out: Chunk[] = [];
    let lastIdx = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(body)) !== null) {
      if (m.index > lastIdx) out.push({ kind: 'md', content: body.slice(lastIdx, m.index) });
      out.push({ kind: 'mermaid', content: m[1] });
      lastIdx = re.lastIndex;
    }
    if (lastIdx < body.length) out.push({ kind: 'md', content: body.slice(lastIdx) });
    return out;
  }

  const howItWorksChunks = $derived(parts ? chunkBody(parts.howItWorks) : []);
</script>

{#if parts}
  {#if parts.tldr}
    <div class="tldr">
      <span class="tldr-label">TL;DR</span>
      <span class="tldr-text">{parts.tldr}</span>
    </div>
  {/if}

  {#each howItWorksChunks as chunk}
    {#if chunk.kind === 'md'}
      <MarkdownRenderer content={chunk.content} />
    {:else}
      <MermaidDiagram source={chunk.content} />
    {/if}
  {/each}

  {#if parts.underTheHood}
    <details class="uth">
      <summary>Under the hood</summary>
      <MarkdownRenderer content={parts.underTheHood} />
    </details>
  {/if}
{:else}
  <p class="missing">Help section <code>{id}</code> not found.</p>
{/if}

<style>
  .tldr {
    display: flex;
    gap: 0.5rem;
    align-items: baseline;
    padding: 0.6rem 0.85rem;
    margin: 0 0 0.75rem 0;
    border-left: 3px solid var(--color-link, #06b6d4);
    background: var(--color-surface-1, rgba(6, 182, 212, 0.06));
    border-radius: 0 0.375rem 0.375rem 0;
    font-size: 0.9rem;
  }
  .tldr-label {
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    color: var(--color-link, #06b6d4);
    text-transform: uppercase;
  }
  .tldr-text { color: var(--color-text); }

  .uth {
    margin-top: 1rem;
    border: 1px solid var(--color-border);
    border-radius: 0.5rem;
    padding: 0.5rem 0.85rem;
  }
  .uth > summary {
    cursor: pointer;
    font-weight: 600;
    color: var(--color-text-dim);
    font-size: 0.85rem;
    user-select: none;
  }
  .uth[open] > summary { margin-bottom: 0.5rem; }

  .missing { color: var(--color-text-dim); font-style: italic; }
</style>
