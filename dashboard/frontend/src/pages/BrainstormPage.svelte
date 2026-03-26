<script lang="ts">
  import { listBrainstormSessions, createBrainstormSession, deleteBrainstormSession } from '../lib/api';
  import { navigate } from '../lib/router.svelte';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import type { BrainstormSession } from '../lib/types';

  let sessions = $state<BrainstormSession[]>([]);
  let creating = $state(false);

  $effect(() => { loadSessions(); });

  async function loadSessions() {
    try { sessions = await listBrainstormSessions(); } catch { /* silent */ }
  }

  async function handleCreate(persona: string) {
    creating = true;
    try {
      const session = await createBrainstormSession({ persona, title: `${persona} session` });
      toastSuccess('Session created');
      navigate(`/brainstorm/${session.id}`);
    } catch (e: any) { toastError(e.message); }
    creating = false;
  }

  async function handleDelete(id: string) {
    try {
      await deleteBrainstormSession(id);
      sessions = sessions.filter(s => s.id !== id);
      toastSuccess('Deleted');
    } catch (e: any) { toastError(e.message); }
  }

  const personas = [
    { id: 'architect', label: 'Architect', icon: '🏗', color: 'var(--color-info)' },
    { id: 'security', label: 'Security', icon: '🛡', color: 'var(--color-reject)' },
    { id: 'performance', label: 'Performance', icon: '⚡', color: 'var(--color-warning)' },
    { id: 'devops', label: 'DevOps', icon: '🔧', color: 'var(--color-approve)' },
  ];
</script>

<div class="space-y-6 animate-fade-in-up">
  <h1 class="text-lg font-semibold text-text">Brainstorm</h1>

  <!-- New session cards -->
  <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
    {#each personas as p}
      <button
        onclick={() => handleCreate(p.id)}
        disabled={creating}
        class="glass rounded-lg p-4 text-center hover:bg-surface-2/50 transition-colors disabled:opacity-50"
      >
        <div class="text-2xl mb-2">{p.icon}</div>
        <div class="text-sm font-medium text-text">{p.label}</div>
        <div class="text-[10px] text-text-muted mt-1">New session</div>
      </button>
    {/each}
  </div>

  <!-- Existing sessions -->
  {#if sessions.length > 0}
    <div>
      <h2 class="text-xs font-semibold text-text-dim uppercase tracking-wider mb-3">Previous Sessions</h2>
      <div class="space-y-2">
        {#each sessions as session (session.id)}
          <div class="glass rounded-lg px-4 py-3 flex items-center justify-between hover:bg-surface-2/30 transition-colors">
            <button onclick={() => navigate(`/brainstorm/${session.id}`)} class="flex-1 text-left">
              <div class="text-sm text-text">{session.title ?? `${session.persona} session`}</div>
              <div class="text-[10px] text-text-muted flex items-center gap-2 mt-0.5">
                <span class="capitalize">{session.persona}</span>
                <span>{session.message_count} messages</span>
                {#if session.project_repo}<span>{session.project_repo}</span>{/if}
              </div>
            </button>
            <button onclick={() => handleDelete(session.id)} class="text-text-muted hover:text-reject text-sm transition-colors ml-2">×</button>
          </div>
        {/each}
      </div>
    </div>
  {/if}
</div>
