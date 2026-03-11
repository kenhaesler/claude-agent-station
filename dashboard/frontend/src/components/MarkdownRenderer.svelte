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
    color: var(--color-text, #e2e8f0);
  }

  .markdown-rendered :global(h2) {
    font-size: 1.25rem;
    font-weight: 600;
    margin-top: 1rem;
    margin-bottom: 0.5rem;
    color: var(--color-text, #e2e8f0);
  }

  .markdown-rendered :global(h3) {
    font-size: 1.1rem;
    font-weight: 600;
    margin-top: 0.75rem;
    margin-bottom: 0.375rem;
    color: var(--color-text, #e2e8f0);
  }

  .markdown-rendered :global(h4),
  .markdown-rendered :global(h5),
  .markdown-rendered :global(h6) {
    font-size: 1rem;
    font-weight: 600;
    margin-top: 0.5rem;
    margin-bottom: 0.25rem;
    color: var(--color-text, #e2e8f0);
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
    background-color: rgba(255, 255, 255, 0.08);
    color: #f0abfc;
  }

  .markdown-rendered :global(pre) {
    margin-bottom: 0.75rem;
    padding: 0.75rem 1rem;
    border-radius: 0.5rem;
    background-color: rgba(0, 0, 0, 0.3);
    overflow-x: auto;
    border: 1px solid rgba(255, 255, 255, 0.06);
  }

  .markdown-rendered :global(pre code) {
    padding: 0;
    background-color: transparent;
    color: #e2e8f0;
    font-size: 0.8rem;
    line-height: 1.6;
  }

  .markdown-rendered :global(blockquote) {
    border-left: 3px solid rgba(99, 102, 241, 0.5);
    padding-left: 1rem;
    margin-bottom: 0.5rem;
    color: rgba(226, 232, 240, 0.7);
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
    border: 1px solid rgba(255, 255, 255, 0.1);
    text-align: left;
  }

  .markdown-rendered :global(th) {
    background-color: rgba(255, 255, 255, 0.05);
    font-weight: 600;
    color: var(--color-text, #e2e8f0);
  }

  .markdown-rendered :global(td) {
    color: rgba(226, 232, 240, 0.8);
  }

  .markdown-rendered :global(hr) {
    border: none;
    border-top: 1px solid rgba(255, 255, 255, 0.1);
    margin: 1rem 0;
  }

  .markdown-rendered :global(a) {
    color: #60a5fa;
    text-decoration: underline;
    text-decoration-color: rgba(96, 165, 250, 0.3);
  }

  .markdown-rendered :global(a:hover) {
    text-decoration-color: rgba(96, 165, 250, 0.8);
  }

  .markdown-rendered :global(strong) {
    font-weight: 600;
    color: var(--color-text, #e2e8f0);
  }

  .markdown-rendered :global(img) {
    max-width: 100%;
    border-radius: 0.5rem;
  }
</style>
