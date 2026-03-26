<script lang="ts">
  import { getBrainstormSession, streamBrainstormMessage } from '../lib/api';
  import type { BrainstormSessionDetail, BrainstormMessage } from '../lib/types';
  import { marked } from 'marked';
  import DOMPurify from 'dompurify';

  let { sessionId = '' }: { sessionId: string } = $props();

  let session = $state<BrainstormSessionDetail | null>(null);
  let messages = $state<BrainstormMessage[]>([]);
  let input = $state('');
  let streaming = $state(false);
  let streamContent = $state('');
  let chatEl: HTMLDivElement | undefined = $state();

  $effect(() => {
    if (sessionId) loadSession();
  });

  async function loadSession() {
    try {
      session = await getBrainstormSession(sessionId);
      messages = session.messages;
      scrollToBottom();
    } catch { /* silent */ }
  }

  function scrollToBottom() {
    requestAnimationFrame(() => {
      if (chatEl) chatEl.scrollTop = chatEl.scrollHeight;
    });
  }

  function renderMarkdown(content: string): string {
    return DOMPurify.sanitize(marked.parse(content) as string);
  }

  async function send() {
    if (!input.trim() || streaming) return;
    const content = input.trim();
    input = '';

    // Add user message
    messages = [...messages, { id: crypto.randomUUID(), session_id: sessionId, role: 'user', content, created_at: new Date().toISOString() }];
    scrollToBottom();

    // Stream response
    streaming = true;
    streamContent = '';

    streamBrainstormMessage(
      sessionId,
      content,
      (delta) => {
        streamContent += delta;
        scrollToBottom();
      },
      (fullContent, messageId) => {
        messages = [...messages, { id: messageId, session_id: sessionId, role: 'assistant', content: fullContent, created_at: new Date().toISOString() }];
        streaming = false;
        streamContent = '';
        scrollToBottom();
      },
      (error) => {
        streaming = false;
        streamContent = `Error: ${error}`;
      },
    );
  }
</script>

<div class="flex flex-col h-[calc(100vh-7rem)] animate-fade-in-up">
  <!-- Header -->
  <div class="flex items-center gap-3 mb-3">
    <a href="/brainstorm" class="text-text-muted hover:text-text text-sm transition-colors">← Back</a>
    <h1 class="text-sm font-semibold text-text">{session?.title ?? 'Session'}</h1>
    {#if session}
      <span class="text-[10px] px-1.5 py-0.5 rounded bg-surface-2 text-text-muted capitalize">{session.persona}</span>
    {/if}
  </div>

  <!-- Messages -->
  <div bind:this={chatEl} class="flex-1 overflow-y-auto space-y-4 px-1 pb-4">
    {#each messages as msg (msg.id)}
      <div class="flex {msg.role === 'user' ? 'justify-end' : 'justify-start'}">
        <div class="max-w-[80%] rounded-lg px-4 py-3 text-sm
          {msg.role === 'user'
            ? 'bg-accent-blue/20 text-text'
            : 'glass text-text-dim'}">
          {#if msg.role === 'assistant'}
            <div class="prose-station">{@html renderMarkdown(msg.content)}</div>
          {:else}
            {msg.content}
          {/if}
        </div>
      </div>
    {/each}

    <!-- Streaming response -->
    {#if streaming && streamContent}
      <div class="flex justify-start">
        <div class="max-w-[80%] glass rounded-lg px-4 py-3 text-sm text-text-dim">
          <div class="prose-station">{@html renderMarkdown(streamContent)}</div>
          <span class="inline-block w-2 h-4 bg-text-muted animate-pulse ml-1"></span>
        </div>
      </div>
    {:else if streaming}
      <div class="flex justify-start">
        <div class="glass rounded-lg px-4 py-3 text-sm text-text-muted animate-pulse">
          Thinking...
        </div>
      </div>
    {/if}
  </div>

  <!-- Input -->
  <div class="flex gap-2 pt-2 border-t border-border-subtle">
    <input
      bind:value={input}
      placeholder="Ask a question..."
      class="flex-1 px-4 py-2.5 rounded-lg bg-surface text-text text-sm border border-border
             focus:border-focus outline-none placeholder:text-text-muted"
      onkeydown={(e) => e.key === 'Enter' && !e.shiftKey && send()}
      disabled={streaming}
    />
    <button
      onclick={send}
      disabled={streaming || !input.trim()}
      class="px-4 py-2.5 rounded-lg text-sm font-medium bg-accent-blue text-white
             hover:bg-accent-blue/80 disabled:opacity-40 transition-colors"
    >
      Send
    </button>
  </div>
</div>
