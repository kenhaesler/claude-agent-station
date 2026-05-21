<!-- dashboard/frontend/src/components/vision/VisionChat.svelte -->
<script lang="ts">
  import { onDestroy } from 'svelte';
  import { streamVisionChat } from '../../lib/vision-sse';
  import {
    visionChatTurnUrl, getVisionChatSession, cancelVisionChat,
    commitVision, getStoredApiKey,
    uploadVisionAttachment, deleteVisionAttachment,
  } from '../../lib/api';
  import { toastError, toastSuccess, addToast } from '../../lib/toast.svelte';
  import type { VisionDoc, VisionSseEvent, VisionAttachment } from '../../lib/types';
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

  type Msg = { role: 'user' | 'assistant'; content: string; attachments?: VisionAttachment[] };

  let messages = $state<Msg[]>([]);
  let covered = $state<string[]>([]);
  let phase = $state<'freeform' | 'structured'>('freeform');
  let assembledDoc = $state<VisionDoc | null>(null);
  let input = $state('');
  let streaming = $state(false);
  let sessionId = $state<string | null>(null);
  let abortCtrl: AbortController | null = null;

  let pendingAttachments = $state<VisionAttachment[]>([]);
  let uploadingCount = $state(0);
  let attachInput: HTMLInputElement;

  const ALLOWED_EXT = '.pdf,.png,.jpg,.jpeg,.gif,.webp,.txt,.md,.csv,.xlsx,.docx';
  const MAX_FILE_BYTES = 10 * 1024 * 1024;
  const MAX_SESSION_BYTES = 40 * 1024 * 1024;

  // Try to resume an existing session on mount
  $effect(() => { resume(); });

  onDestroy(() => abortCtrl?.abort());

  async function resume() {
    try {
      const s = await getVisionChatSession(projectId);
      sessionId = s.id;
      messages = s.messages.map(m => ({
        role: m.role,
        content: m.content,
        attachments: (m as any).attachments,
      }));
      covered = Object.entries(s.coverage).filter(([, v]) => v).map(([k]) => k);
      phase = s.phase;
      if (s.assembled) assembledDoc = s.assembled;
      pendingAttachments = s.pending_attachments ?? [];
    } catch {
      // 404 = no active session, normal fresh-start case
    }
  }

  function pendingTotalBytes(): number {
    return pendingAttachments.reduce((sum, a) => sum + a.size_bytes, 0);
  }

  async function handleFiles(files: FileList | File[]) {
    for (const file of Array.from(files)) {
      if (file.size > MAX_FILE_BYTES) {
        toastError(`${file.name} is ${Math.round(file.size / 1024 / 1024)} MB — max 10 MB per file`);
        continue;
      }
      if (pendingTotalBytes() + file.size > MAX_SESSION_BYTES) {
        toastError(`Adding ${file.name} would exceed the 40 MB session limit`);
        continue;
      }
      uploadingCount++;
      try {
        const att = await uploadVisionAttachment(projectId, file);
        pendingAttachments = [...pendingAttachments, att];
      } catch (e: any) {
        toastError(e?.message ?? 'upload failed');
      } finally {
        uploadingCount--;
      }
    }
  }

  async function removeAttachment(id: string) {
    try {
      await deleteVisionAttachment(projectId, id);
    } catch (e: any) {
      toastError(e?.message ?? 'delete failed');
      return;
    }
    pendingAttachments = pendingAttachments.filter(a => a.id !== id);
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    if (e.dataTransfer?.files?.length) handleFiles(e.dataTransfer.files);
  }

  function onDragOver(e: DragEvent) { e.preventDefault(); }

  function onPick(e: Event) {
    const input = e.target as HTMLInputElement;
    if (input.files?.length) handleFiles(input.files);
    input.value = '';
  }

  async function send() {
    const text = input.trim();
    if (!text || streaming) return;
    input = '';
    streaming = true;

    const attachmentsForTurn = pendingAttachments;
    pendingAttachments = [];

    messages = [
      ...messages,
      {
        role: 'user',
        content: text,
        attachments: attachmentsForTurn.length ? attachmentsForTurn : undefined,
      },
      { role: 'assistant', content: '' },
    ];

    abortCtrl = new AbortController();
    const headers: Record<string, string> = {};
    const apiKey = getStoredApiKey();
    if (apiKey) headers['Authorization'] = `Bearer ${apiKey}`;

    try {
      for await (const ev of streamVisionChat({
        url: visionChatTurnUrl(projectId),
        headers,
        payload: {
          session_id: sessionId,
          message: text,
          attachment_ids: attachmentsForTurn.map(a => a.id),
        },
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
      const result = await commitVision(projectId, assembledDoc);
      toastSuccess('Vision saved to GitHub');
      if (result.analyst_dispatched) {
        addToast('info', 'Vision analyst running — proposals will appear in a few minutes.');
      }
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
  <div
    class="card p-4 max-h-96 overflow-y-auto space-y-3"
    data-testid="vision-chat-transcript"
    role="region"
    aria-label="Chat transcript — drop files here to attach"
    ondragover={onDragOver}
    ondrop={onDrop}
  >
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
        {#if m.attachments?.length}
          <div class="flex flex-wrap gap-1 mt-1">
            {#each m.attachments as a (a.id)}
              <span class="inline-flex items-center gap-1 text-[10px] bg-tertiary/10 px-2 py-0.5 rounded">📎 {a.filename}</span>
            {/each}
          </div>
        {/if}
      </div>
    {/each}
  </div>

  <!-- Pending attachment chips above the input -->
  {#if pendingAttachments.length || uploadingCount}
    <div class="flex flex-wrap gap-1" data-testid="vision-chat-pending-strip">
      {#each pendingAttachments as a (a.id)}
        <span class="inline-flex items-center gap-1 text-[10px] bg-tertiary/15 px-2 py-1 rounded">
          📎 {a.filename} · {Math.max(1, Math.round(a.size_bytes / 1024))} KB
          <button
            type="button"
            class="text-tertiary hover:text-primary"
            aria-label={`Remove ${a.filename}`}
            data-testid={`vision-chat-attachment-remove-${a.id}`}
            onclick={() => removeAttachment(a.id)}
          >×</button>
        </span>
      {/each}
      {#if uploadingCount > 0}
        <span class="text-[10px] text-tertiary">Uploading {uploadingCount}…</span>
      {/if}
    </div>
  {/if}

  <!-- Input + attach -->
  <div class="flex gap-2">
    <input
      type="file"
      bind:this={attachInput}
      accept={ALLOWED_EXT}
      multiple
      onchange={onPick}
      class="hidden"
      data-testid="vision-chat-attach-input"
    />
    <button
      type="button"
      onclick={() => attachInput.click()}
      class="btn btn-ghost btn-sm text-xs"
      data-testid="vision-chat-attach-btn"
      aria-label="Attach reference file"
    >📎</button>
    <input
      type="text"
      bind:value={input}
      placeholder="Type a message…"
      class="input flex-1 text-sm"
      disabled={streaming || uploadingCount > 0}
      onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter') send(); }}
      data-testid="vision-chat-input"
    />
    <button
      type="button"
      onclick={send}
      disabled={streaming || uploadingCount > 0 || !input.trim()}
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
