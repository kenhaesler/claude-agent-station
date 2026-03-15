<script lang="ts">
  import { listBrainstormSessions, createBrainstormSession, getBrainstormSession, deleteBrainstormSession, streamBrainstormMessage, listProjects } from '../lib/api';
  import type { BrainstormSession, BrainstormMessage, BrainstormSessionDetail, Project } from '../lib/types';
  import MarkdownRenderer from '../components/MarkdownRenderer.svelte';
  import GlassCard from '../components/GlassCard.svelte';
  import TimeAgo from '../components/TimeAgo.svelte';

  interface Props {
    sessionId?: string | null;
  }

  let { sessionId = null }: Props = $props();

  // State
  let sessions = $state<BrainstormSession[]>([]);
  let activeSession = $state<BrainstormSessionDetail | null>(null);
  let projects = $state<Project[]>([]);
  let messages = $state<BrainstormMessage[]>([]);
  let inputText = $state('');
  let streaming = $state(false);
  let streamingContent = $state('');
  let loading = $state(true);
  let loadingSession = $state(false);
  let showNewSession = $state(false);
  let newSessionProject = $state<number | undefined>(undefined);
  let newSessionPersona = $state('architect');
  let abortController = $state<AbortController | null>(null);
  let chatContainer: HTMLDivElement | undefined = $state(undefined);
  let sidebarOpen = $state(true);
  let confirmDeleteId = $state<string | null>(null);

  const personas = [
    { id: 'architect', label: 'Architect', desc: 'Systems design & architecture' },
    { id: 'security', label: 'Security', desc: 'Threat analysis & hardening' },
    { id: 'performance', label: 'Performance', desc: 'Optimization & profiling' },
    { id: 'devops', label: 'DevOps', desc: 'Infrastructure & deployment' },
  ];

  function getPersonaLabel(id: string): string {
    return personas.find(p => p.id === id)?.label ?? 'Architect';
  }

  // Load sessions and projects on mount
  async function loadSessions() {
    try {
      const [sessRes, projRes] = await Promise.allSettled([
        listBrainstormSessions(),
        listProjects(),
      ]);
      if (sessRes.status === 'fulfilled') sessions = sessRes.value;
      if (projRes.status === 'fulfilled') projects = projRes.value;
    } catch { /* silent */ }
    loading = false;
  }

  // Load a specific session
  async function loadSession(id: string) {
    loadingSession = true;
    try {
      activeSession = await getBrainstormSession(id);
      messages = activeSession.messages;
      scrollToBottom();
    } catch {
      activeSession = null;
      messages = [];
    }
    loadingSession = false;
  }

  // Create new session
  async function handleNewSession() {
    try {
      const session = await createBrainstormSession({
        project_id: newSessionProject,
        persona: newSessionPersona,
      });
      sessions = [session, ...sessions];
      showNewSession = false;
      newSessionProject = undefined;
      newSessionPersona = 'architect';
      window.location.hash = `/brainstorm/${session.id}`;
    } catch { /* silent */ }
  }

  // Delete a session
  async function handleDelete(id: string) {
    try {
      await deleteBrainstormSession(id);
      sessions = sessions.filter(s => s.id !== id);
      if (activeSession?.id === id) {
        activeSession = null;
        messages = [];
        window.location.hash = '/brainstorm';
      }
    } catch { /* silent */ }
    confirmDeleteId = null;
  }

  // Send message
  async function handleSend() {
    if (!inputText.trim() || !activeSession || streaming) return;

    const userContent = inputText.trim();
    inputText = '';
    streaming = true;
    streamingContent = '';

    // Add user message to UI immediately
    const tempUserMsg: BrainstormMessage = {
      id: `temp-${Date.now()}`,
      session_id: activeSession.id,
      role: 'user',
      content: userContent,
      created_at: new Date().toISOString(),
    };
    messages = [...messages, tempUserMsg];
    scrollToBottom();

    abortController = streamBrainstormMessage(
      activeSession.id,
      userContent,
      // onDelta
      (text: string) => {
        streamingContent += text;
        scrollToBottom();
      },
      // onDone
      (fullContent: string, messageId: string) => {
        const assistantMsg: BrainstormMessage = {
          id: messageId,
          session_id: activeSession!.id,
          role: 'assistant',
          content: fullContent,
          created_at: new Date().toISOString(),
        };
        messages = [...messages, assistantMsg];
        streamingContent = '';
        streaming = false;
        abortController = null;

        // Update session title in sidebar if it was auto-generated
        const idx = sessions.findIndex(s => s.id === activeSession!.id);
        if (idx >= 0 && sessions[idx].title === 'New brainstorm') {
          sessions[idx] = {
            ...sessions[idx],
            title: userContent.slice(0, 60) + (userContent.length > 60 ? '...' : ''),
          };
        }
        // Move session to top
        if (idx > 0) {
          const s = sessions[idx];
          sessions = [s, ...sessions.filter((_, i) => i !== idx)];
        }

        scrollToBottom();
      },
      // onError
      (error: string) => {
        streamingContent = '';
        streaming = false;
        abortController = null;
        // Add error as system message
        messages = [...messages, {
          id: `err-${Date.now()}`,
          session_id: activeSession!.id,
          role: 'assistant',
          content: `**Error:** ${error}`,
          created_at: new Date().toISOString(),
        }];
        scrollToBottom();
      },
    );
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  function stopStreaming() {
    if (abortController) {
      abortController.abort();
      streaming = false;
      if (streamingContent) {
        messages = [...messages, {
          id: `partial-${Date.now()}`,
          session_id: activeSession!.id,
          role: 'assistant',
          content: streamingContent + '\n\n*[Response interrupted]*',
          created_at: new Date().toISOString(),
        }];
        streamingContent = '';
      }
      abortController = null;
    }
  }

  function scrollToBottom() {
    requestAnimationFrame(() => {
      if (chatContainer) {
        chatContainer.scrollTop = chatContainer.scrollHeight;
      }
    });
  }

  function getShortRepo(repo: string): string {
    return repo.split('/').pop() ?? repo;
  }

  // React to sessionId changes
  $effect(() => {
    loadSessions();
  });

  $effect(() => {
    if (sessionId) {
      loadSession(sessionId);
      // On mobile, close sidebar when viewing a session
      if (window.innerWidth < 768) sidebarOpen = false;
    } else {
      activeSession = null;
      messages = [];
    }
  });
</script>

<div class="flex h-full -m-3 md:-m-6 animate-fade-in-up">
  <!-- Session sidebar -->
  {#if sidebarOpen}
    <div class="w-full md:w-72 shrink-0 border-r border-border bg-surface flex flex-col {activeSession ? 'hidden md:flex' : 'flex'}">
      <!-- Sidebar header -->
      <div class="p-3 border-b border-border flex items-center justify-between">
        <h2 class="text-sm font-semibold text-text">Brainstorm</h2>
        <button
          onclick={() => showNewSession = !showNewSession}
          class="px-2 py-1 text-xs rounded-md bg-info/20 text-info hover:bg-info/30 transition-colors cursor-pointer"
        >
          + New
        </button>
      </div>

      <!-- New session form -->
      {#if showNewSession}
        <div class="p-3 border-b border-border space-y-2 bg-surface-2/50">
          <!-- Persona selector -->
          <div class="grid grid-cols-2 gap-1">
            {#each personas as p}
              <button
                onclick={() => newSessionPersona = p.id}
                class="px-2 py-1.5 text-[11px] rounded-md cursor-pointer transition-colors text-left
                  {newSessionPersona === p.id ? 'bg-info/20 text-info border border-info/30' : 'bg-surface text-text-dim hover:text-text border border-border'}"
              >
                <span class="font-medium block">{p.label}</span>
                <span class="text-[10px] opacity-70">{p.desc}</span>
              </button>
            {/each}
          </div>

          <!-- Project selector -->
          <select
            bind:value={newSessionProject}
            class="w-full text-xs bg-surface border border-border rounded-md px-2 py-1.5 text-text"
          >
            <option value={undefined}>No project context</option>
            {#each projects as project}
              <option value={project.id}>{getShortRepo(project.repo)}</option>
            {/each}
          </select>

          <div class="flex gap-1">
            <button
              onclick={handleNewSession}
              class="flex-1 px-2 py-1.5 text-xs rounded-md bg-info text-bg font-medium hover:bg-info/90 transition-colors cursor-pointer"
            >
              Start session
            </button>
            <button
              onclick={() => showNewSession = false}
              class="px-2 py-1.5 text-xs rounded-md text-text-dim hover:text-text cursor-pointer"
            >
              Cancel
            </button>
          </div>
        </div>
      {/if}

      <!-- Session list -->
      <div class="flex-1 overflow-y-auto">
        {#if loading}
          <div class="p-4 text-center text-text-muted text-xs">Loading...</div>
        {:else if sessions.length === 0}
          <div class="p-4 text-center text-text-muted text-xs">
            No brainstorm sessions yet.
            <br />Click "+ New" to start one.
          </div>
        {:else}
          {#each sessions as session (session.id)}
            <div class="relative group">
              <a
                href="#/brainstorm/{session.id}"
                class="block px-3 py-2.5 border-b border-border/50 transition-colors no-underline
                  {activeSession?.id === session.id ? 'bg-white/[0.06] border-l-2 border-l-info' : 'hover:bg-white/[0.03]'}"
              >
                <div class="text-xs font-medium text-text truncate pr-6">
                  {session.title || 'Untitled'}
                </div>
                <div class="flex items-center gap-2 mt-0.5">
                  <span class="text-[10px] text-text-muted">{getPersonaLabel(session.persona)}</span>
                  {#if session.project_repo}
                    <span class="text-[10px] text-info/70">{getShortRepo(session.project_repo)}</span>
                  {/if}
                  <span class="text-[10px] text-text-muted ml-auto">{session.message_count} msgs</span>
                </div>
                {#if session.updated_at}
                  <div class="text-[10px] text-text-muted mt-0.5">
                    <TimeAgo date={session.updated_at} />
                  </div>
                {/if}
              </a>

              <!-- Delete button -->
              {#if confirmDeleteId === session.id}
                <div class="absolute right-1 top-1 flex gap-0.5">
                  <button
                    onclick={(e) => { e.stopPropagation(); handleDelete(session.id); }}
                    class="px-1.5 py-0.5 text-[10px] rounded bg-reject/20 text-reject hover:bg-reject/30 cursor-pointer"
                  >
                    Delete
                  </button>
                  <button
                    onclick={(e) => { e.stopPropagation(); confirmDeleteId = null; }}
                    class="px-1.5 py-0.5 text-[10px] rounded text-text-dim hover:text-text cursor-pointer"
                  >
                    No
                  </button>
                </div>
              {:else}
                <button
                  onclick={(e) => { e.stopPropagation(); confirmDeleteId = session.id; }}
                  class="absolute right-2 top-2 opacity-0 group-hover:opacity-100 transition-opacity text-text-muted hover:text-reject text-xs cursor-pointer"
                  title="Delete session"
                >
                  &#x2715;
                </button>
              {/if}
            </div>
          {/each}
        {/if}
      </div>
    </div>
  {/if}

  <!-- Chat area -->
  <div class="flex-1 flex flex-col min-w-0">
    {#if !activeSession}
      <!-- Empty state -->
      <div class="flex-1 flex items-center justify-center">
        <div class="text-center space-y-3 max-w-md px-4">
          <div class="text-4xl">&#x1F9E0;</div>
          <h2 class="text-lg font-semibold text-text">Brainstorm Mode</h2>
          <p class="text-sm text-text-dim leading-relaxed">
            Collaborate with an expert AI advisor. Select a session from the sidebar
            or create a new one to start brainstorming about your projects.
          </p>
          <div class="flex flex-wrap justify-center gap-2 pt-2">
            {#each personas as p}
              <span class="px-2 py-1 text-[11px] rounded-full bg-surface-2 text-text-dim border border-border">
                {p.label}
              </span>
            {/each}
          </div>
          <button
            onclick={() => { showNewSession = true; sidebarOpen = true; }}
            class="mt-4 px-4 py-2 text-sm rounded-lg bg-info text-bg font-medium hover:bg-info/90 transition-colors cursor-pointer"
          >
            Start a brainstorm
          </button>
          {#if !sidebarOpen}
            <div>
              <button
                onclick={() => sidebarOpen = true}
                class="mt-2 text-xs text-info hover:text-info/80 cursor-pointer"
              >
                Show sidebar
              </button>
            </div>
          {/if}
        </div>
      </div>
    {:else}
      <!-- Chat header -->
      <div class="px-4 py-2.5 border-b border-border flex items-center gap-3 shrink-0 bg-surface/50">
        <!-- Mobile back button -->
        <button
          onclick={() => { window.location.hash = '/brainstorm'; sidebarOpen = true; }}
          class="md:hidden text-text-dim hover:text-text cursor-pointer text-sm"
        >
          &#8592;
        </button>

        <!-- Toggle sidebar on desktop -->
        <button
          onclick={() => sidebarOpen = !sidebarOpen}
          class="hidden md:block text-text-dim hover:text-text cursor-pointer text-sm"
          title="{sidebarOpen ? 'Hide' : 'Show'} sidebar"
        >
          {#if sidebarOpen}
            &#x25C0;
          {:else}
            &#x25B6;
          {/if}
        </button>

        <div class="flex-1 min-w-0">
          <h3 class="text-sm font-medium text-text truncate">{activeSession.title || 'Untitled'}</h3>
          <div class="flex items-center gap-2">
            <span class="text-[10px] text-info">{getPersonaLabel(activeSession.persona)}</span>
            {#if activeSession.project_repo}
              <span class="text-[10px] text-text-muted">&#183; {getShortRepo(activeSession.project_repo)}</span>
            {/if}
          </div>
        </div>

        {#if streaming}
          <button
            onclick={stopStreaming}
            class="px-2 py-1 text-[11px] rounded-md bg-reject/20 text-reject hover:bg-reject/30 transition-colors cursor-pointer"
          >
            Stop
          </button>
        {/if}
      </div>

      <!-- Messages -->
      <div class="flex-1 overflow-y-auto px-4 py-4 space-y-4" bind:this={chatContainer}>
        {#if loadingSession}
          <div class="text-center py-8 text-text-muted text-sm">Loading session...</div>
        {:else if messages.length === 0 && !streaming}
          <div class="text-center py-12 text-text-dim text-sm">
            Start the conversation. Ask anything about your project.
          </div>
        {:else}
          {#each messages as msg (msg.id)}
            <div class="flex {msg.role === 'user' ? 'justify-end' : 'justify-start'}">
              <div class="max-w-[85%] md:max-w-[75%] {msg.role === 'user'
                  ? 'bg-info/15 border border-info/20 rounded-2xl rounded-br-md px-4 py-2.5'
                  : 'bg-surface-2/60 border border-border rounded-2xl rounded-bl-md px-4 py-3'}">
                {#if msg.role === 'assistant'}
                  <div class="text-[10px] text-info font-medium mb-1.5 uppercase tracking-wider">JARVIS</div>
                  <MarkdownRenderer content={msg.content} />
                {:else}
                  <p class="text-sm text-text whitespace-pre-wrap">{msg.content}</p>
                {/if}
              </div>
            </div>
          {/each}

          <!-- Streaming response -->
          {#if streaming && streamingContent}
            <div class="flex justify-start">
              <div class="max-w-[85%] md:max-w-[75%] bg-surface-2/60 border border-border rounded-2xl rounded-bl-md px-4 py-3">
                <div class="text-[10px] text-info font-medium mb-1.5 uppercase tracking-wider">JARVIS</div>
                <MarkdownRenderer content={streamingContent} />
              </div>
            </div>
          {:else if streaming}
            <div class="flex justify-start">
              <div class="bg-surface-2/60 border border-border rounded-2xl rounded-bl-md px-4 py-3">
                <div class="text-[10px] text-info font-medium mb-1.5 uppercase tracking-wider">JARVIS</div>
                <div class="flex items-center gap-1.5 py-1">
                  <span class="w-1.5 h-1.5 rounded-full bg-info/60" style="animation: pulse-dot 1.4s infinite"></span>
                  <span class="w-1.5 h-1.5 rounded-full bg-info/60" style="animation: pulse-dot 1.4s infinite 0.2s"></span>
                  <span class="w-1.5 h-1.5 rounded-full bg-info/60" style="animation: pulse-dot 1.4s infinite 0.4s"></span>
                </div>
              </div>
            </div>
          {/if}
        {/if}
      </div>

      <!-- Input area -->
      <div class="px-4 py-3 border-t border-border bg-surface/50 shrink-0">
        <div class="flex items-end gap-2 max-w-4xl mx-auto">
          <textarea
            bind:value={inputText}
            onkeydown={handleKeydown}
            placeholder="Ask JARVIS anything..."
            rows="1"
            disabled={streaming}
            class="flex-1 resize-none bg-surface-2 border border-border rounded-xl px-4 py-2.5 text-sm text-text placeholder:text-text-muted focus:outline-none focus:border-info/50 transition-colors disabled:opacity-50"
            style="max-height: 120px; min-height: 40px;"
            oninput={(e) => {
              const el = e.currentTarget;
              el.style.height = 'auto';
              el.style.height = Math.min(el.scrollHeight, 120) + 'px';
            }}
          ></textarea>
          <button
            onclick={handleSend}
            disabled={!inputText.trim() || streaming}
            class="px-4 py-2.5 rounded-xl bg-info text-bg text-sm font-medium hover:bg-info/90 transition-colors cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed shrink-0"
          >
            Send
          </button>
        </div>
        <div class="text-center mt-1.5">
          <span class="text-[10px] text-text-muted">Enter to send &#183; Shift+Enter for new line</span>
        </div>
      </div>
    {/if}
  </div>
</div>
