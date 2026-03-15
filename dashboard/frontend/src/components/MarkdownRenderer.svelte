<script lang="ts">
  import { marked } from 'marked';
  import DOMPurify from 'dompurify';

  interface Props {
    content: string;
    class?: string;
  }

  let { content, class: className = '' }: Props = $props();

  // Configure marked for safe, well-formatted output
  marked.setOptions({
    gfm: true,
    breaks: false,
  });

  let htmlContent = $derived(renderMarkdown(content));

  function renderMarkdown(raw: string): string {
    if (!raw) return '';
    try {
      const html = marked.parse(raw) as string;
      return DOMPurify.sanitize(html, {
        ALLOWED_TAGS: [
          'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
          'p', 'br', 'hr',
          'ul', 'ol', 'li',
          'strong', 'em', 'del', 'code', 'pre',
          'blockquote',
          'table', 'thead', 'tbody', 'tr', 'th', 'td',
          'a', 'img',
          'span', 'div',
        ],
        ALLOWED_ATTR: ['href', 'src', 'alt', 'title', 'class', 'target', 'rel'],
      });
    } catch {
      return DOMPurify.sanitize(raw);
    }
  }
</script>

<div class="markdown-rendered prose prose-invert prose-sm max-w-none {className}">
  {@html htmlContent}
</div>

<style>
  .markdown-rendered :global(h1) {
    font-size: 1.5rem;
    font-weight: 700;
    margin-top: 1.25rem;
    margin-bottom: 0.5rem;
    color: var(--color-text);
  }

  .markdown-rendered :global(h2) {
    font-size: 1.25rem;
    font-weight: 600;
    margin-top: 1rem;
    margin-bottom: 0.5rem;
    color: var(--color-text);
  }

  .markdown-rendered :global(h3) {
    font-size: 1.1rem;
    font-weight: 600;
    margin-top: 0.75rem;
    margin-bottom: 0.375rem;
    color: var(--color-text);
  }

  .markdown-rendered :global(h4),
  .markdown-rendered :global(h5),
  .markdown-rendered :global(h6) {
    font-size: 1rem;
    font-weight: 600;
    margin-top: 0.5rem;
    margin-bottom: 0.25rem;
    color: var(--color-text);
  }

  .markdown-rendered :global(p) {
    margin-bottom: 0.5rem;
    line-height: 1.6;
  }

  .markdown-rendered :global(ul),
  .markdown-rendered :global(ol) {
    margin-left: 1.5rem;
    margin-bottom: 0.5rem;
  }

  .markdown-rendered :global(ul) {
    list-style-type: disc;
  }

  .markdown-rendered :global(ol) {
    list-style-type: decimal;
  }

  .markdown-rendered :global(li) {
    margin-bottom: 0.25rem;
    line-height: 1.5;
  }

  .markdown-rendered :global(code) {
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    font-size: 0.8em;
    padding: 0.15em 0.35em;
    border-radius: 0.25rem;
    background-color: var(--color-code-bg);
    color: var(--color-code-text);
  }

  .markdown-rendered :global(pre) {
    margin-bottom: 0.75rem;
    padding: 0.75rem 1rem;
    border-radius: 0.5rem;
    background-color: var(--color-pre-bg);
    overflow-x: auto;
    border: 1px solid var(--color-pre-border);
  }

  .markdown-rendered :global(pre code) {
    padding: 0;
    background-color: transparent;
    color: var(--color-text);
    font-size: 0.8rem;
    line-height: 1.6;
  }

  .markdown-rendered :global(blockquote) {
    border-left: 3px solid var(--color-blockquote-border);
    padding-left: 1rem;
    margin-bottom: 0.5rem;
    color: var(--color-blockquote-text);
    font-style: italic;
  }

  .markdown-rendered :global(table) {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 0.75rem;
    font-size: 0.85rem;
  }

  .markdown-rendered :global(th),
  .markdown-rendered :global(td) {
    padding: 0.5rem 0.75rem;
    border: 1px solid var(--color-table-border);
    text-align: left;
  }

  .markdown-rendered :global(th) {
    background-color: var(--color-table-header-bg);
    font-weight: 600;
    color: var(--color-text);
  }

  .markdown-rendered :global(td) {
    color: var(--color-text-dim);
  }

  .markdown-rendered :global(hr) {
    border: none;
    border-top: 1px solid var(--color-hr);
    margin: 1rem 0;
  }

  .markdown-rendered :global(a) {
    color: var(--color-link);
    text-decoration: underline;
    text-decoration-color: color-mix(in srgb, var(--color-link) 30%, transparent);
  }

  .markdown-rendered :global(a:hover) {
    text-decoration-color: color-mix(in srgb, var(--color-link) 80%, transparent);
  }

  .markdown-rendered :global(strong) {
    font-weight: 600;
    color: var(--color-text);
  }

  .markdown-rendered :global(img) {
    max-width: 100%;
    border-radius: 0.5rem;
  }
</style>
