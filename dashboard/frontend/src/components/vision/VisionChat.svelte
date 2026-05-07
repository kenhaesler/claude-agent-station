<!-- dashboard/frontend/src/components/vision/VisionChat.svelte -->
<script lang="ts">
  import { onDestroy } from 'svelte';
  import { streamVisionChat } from '../../lib/vision-sse';
  import {
    visionChatTurnUrl, getVisionChatSession, cancelVisionChat,
    commitVision, getStoredApiKey,
  } from '../../lib/api';
  import { toastError, toastSuccess } from '../../lib/toast.svelte';
  import type { VisionDoc, VisionSseEvent } from '../../lib/types';
  import CoverageChecklist from './CoverageChecklist.svelte';

  let {
    projectId,
    onApproved = () => {},
    onCancelled = () => {},
  }: {
    projectId: number;
    onApproved?: () => void;
    onCancelled?: () => void;
  } = $props();

  type Msg = { role: 'user' | 'assistant'; content: string };

  let messages = $state<Msg[]>([]);
  let covered = $state<string[]>([]);
  let phase = $state<'freeform' | 'structured'>('freeform');
  let assembledDoc = $state<VisionDoc | null>(null);
  let input = $state('');
  let streaming = $state(false);
  let sessionId = $state<string | null>(null);
  let abortCtrl: AbortController | null = null;

  // Try to resume an existing session on mount
  $effect(() => { resume(); });

  onDestroy(() => abortCtrl?.abort());

  async function resume() {
    try {
      const s = await getVisionChatSession(projectId);
      sessionId = s.id;
      messages = s.messages.map(m => ({ role: m.role, content: m.content }));
      covered = Object.entries(s.coverage).filter(([, v]) => v).map(([k]) => k);
      phase = s.phase;
      if (s.assembled) assembledDoc = s.assembled;
    } catch {
      // 404 = no active session, normal fresh-start case
    }
  }

  async function send() {
    const text = input.trim();
    if (!text || streaming) return;
    input = '';
    streaming = true;
    messages = [...messages, { role: 'user', content: text }, { role: 'assistant', content: '' }];

    abortCtrl = new AbortController();
    const headers: Record<string, string> = {};
    const apiKey = getStoredApiKey();
    if (apiKey) headers['Authorization'] = `Bearer ${apiKey}`;

    try {
      for await (const ev of streamVisionChat({
        url: visionChatTurnUrl(projectId),
        headers,
        payload: { session_id: sessionId, message: text },
        signal: abortCtrl.signal,
      })) {
        handleEvent(ev);
      }
    } finally {
      streaming = false;
      abortCtrl = null;
    }
  }

  function handleEvent(ev: VisionSseEvent) {
    if (ev.type === 'assistant_text') {
      const i = messages.length - 1;
      if (i >= 0 && messages[i].role === 'assistant') {
        messages[i] = { role: 'assistant', content: messages[i].content + ev.delta };
        messages = [...messages];
      }
    } else if (ev.type === 'coverage_update') {
      covered = ev.covered;
    } else if (ev.type === 'phase_change') {
      phase = ev.phase;
    } else if (ev.type === 'vision_ready') {
      assembledDoc = ev.vision_doc;
    } else if (ev.type === 'error') {
      toastError(`Chat error: ${ev.message} (${ev.code})`);
    }
    // After the first send the backend has a session; refresh the id
    if (!sessionId) refreshSessionId();
  }

  async function refreshSessionId() {
    try {
      const s = await getVisionChatSession(projectId);
      sessionId = s.id;
    } catch { /* ignore */ }
  }

  async function approveAndCommit() {
    if (!assembledDoc) return;
    try {
      await commitVision(projectId, assembledDoc);
      toastSuccess('Vision saved to GitHub');
      onApproved();
    } catch (e: any) {
      toastError(e.message);
    }
  }

  async function cancel() {
    abortCtrl?.abort();
    try { await cancelVisionChat(projectId); } catch { /* ignore */ }
    onCancelled();
  }
</script>

<div class="space-y-3">
  <CoverageChecklist {covered} />

  <!-- Transcript -->
  <div class="card p-4 max-h-96 overflow-y-auto space-y-3" data-testid="vision-chat-transcript">
    {#if messages.length === 0}
      <p class="text-xs text-tertiary">
        Hi — describe your project in your own words. I'll listen, then walk
        through the seven sections of the vision.
      </p>
    {/if}
    {#each messages as m, i (i)}
      <div class="text-sm">
        <div class="text-[10px] font-semibold text-tertiary mb-1">{m.role === 'user' ? 'You' : 'Claude'}</div>
        <div class="whitespace-pre-wrap text-secondary">{m.content || (streaming && i === messages.length - 1 ? '…' : '')}</div>
      </div>
    {/each}
  </div>

  <!-- Input -->
  <div class="flex gap-2">
    <input
      type="text"
      bind:value={input}
      placeholder="Type a message…"
      class="input flex-1 text-sm"
      disabled={streaming}
      onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter') send(); }}
      data-testid="vision-chat-input"
    />
    <button
      type="button"
      onclick={send}
      disabled={streaming || !input.trim()}
      class="btn btn-primary btn-sm text-xs"
    >Send</button>
  </div>

  <!-- Terminal actions -->
  <div class="flex justify-between items-center">
    <button type="button" onclick={cancel} class="btn btn-ghost btn-sm text-xs">Cancel</button>
    <button
      type="button"
      onclick={approveAndCommit}
      disabled={!assembledDoc}
      data-testid="vision-chat-approve-btn"
      class="btn btn-primary btn-sm text-xs"
    >{assembledDoc ? '✓ Approve & commit' : 'Continue the conversation…'}</button>
  </div>
</div>
