<script lang="ts">
  import { marked } from 'marked';
  import DOMPurify from 'dompurify';

  let {
    content,
  }: {
    content: string;
  } = $props();

  let html = $derived(DOMPurify.sanitize(marked.parse(content) as string));
</script>

<div class="prose-station text-sm leading-relaxed">
  {@html html}
</div>

<style>
  .prose-station :global(h1),
  .prose-station :global(h2),
  .prose-station :global(h3),
  .prose-station :global(h4) {
    color: var(--color-text);
    margin-top: 1.25em;
    margin-bottom: 0.5em;
    font-weight: 600;
  }

  .prose-station :global(h1) { font-size: 1.5em; }
  .prose-station :global(h2) { font-size: 1.25em; }
  .prose-station :global(h3) { font-size: 1.1em; }

  .prose-station :global(p) {
    color: var(--color-text-dim);
    margin: 0.5em 0;
  }

  .prose-station :global(a) {
    color: var(--color-link);
    text-decoration: underline;
    text-underline-offset: 2px;
  }

  .prose-station :global(a:hover) {
    opacity: 0.8;
  }

  .prose-station :global(code) {
    background: var(--color-code-bg);
    color: var(--color-code-text);
    padding: 0.15em 0.35em;
    border-radius: 0.25em;
    font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.9em;
  }

  .prose-station :global(pre) {
    background: var(--color-pre-bg);
    border: 1px solid var(--color-pre-border);
    border-radius: 0.5em;
    padding: 0.75em 1em;
    overflow-x: auto;
    margin: 0.75em 0;
  }

  .prose-station :global(pre code) {
    background: none;
    padding: 0;
    color: var(--color-text-dim);
  }

  .prose-station :global(blockquote) {
    border-left: 3px solid var(--color-blockquote-border);
    color: var(--color-blockquote-text);
    padding-left: 1em;
    margin: 0.75em 0;
  }

  .prose-station :global(ul),
  .prose-station :global(ol) {
    color: var(--color-text-dim);
    padding-left: 1.5em;
    margin: 0.5em 0;
  }

  .prose-station :global(li) {
    margin: 0.25em 0;
  }

  .prose-station :global(table) {
    width: 100%;
    border-collapse: collapse;
    margin: 0.75em 0;
  }

  .prose-station :global(th),
  .prose-station :global(td) {
    border: 1px solid var(--color-table-border);
    padding: 0.4em 0.75em;
    text-align: left;
    color: var(--color-text-dim);
  }

  .prose-station :global(th) {
    background: var(--color-table-header-bg);
    color: var(--color-text);
    font-weight: 600;
  }

  .prose-station :global(hr) {
    border: none;
    border-top: 1px solid var(--color-hr);
    margin: 1em 0;
  }

  .prose-station :global(img) {
    max-width: 100%;
    border-radius: 0.375em;
  }

  .prose-station :global(strong) {
    color: var(--color-text);
    font-weight: 600;
  }
</style>
