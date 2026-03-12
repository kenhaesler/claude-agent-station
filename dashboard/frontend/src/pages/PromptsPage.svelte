<script lang="ts">
  import { listPrompts, updatePrompt, resetPrompt, type PromptData } from '../lib/api';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';
  import GlassCard from '../components/GlassCard.svelte';

  let loading = $state(true);
  let prompts = $state<PromptData[]>([]);
  let activeRole = $state('');
  let editorContent = $state('');
  let saving = $state(false);
  let resetting = $state(false);

  // Track whether the editor has unsaved changes
  let originalContent = $state('');
  let hasChanges = $derived(editorContent !== originalContent);

  function activePrompt(): PromptData | undefined {
    return prompts.find(p => p.role === activeRole);
  }

  function selectRole(role: string) {
    activeRole = role;
    const p = prompts.find(pr => pr.role === role);
    if (p) {
      const content = p.custom_content ?? p.default_content;
      editorContent = content;
      originalContent = content;
    }
  }

  async function load() {
    loading = true;
    try {
      prompts = await listPrompts();
      if (prompts.length > 0 && !activeRole) {
        selectRole(prompts[0].role);
      } else if (activeRole) {
        // Refresh current selection
        selectRole(activeRole);
      }
    } catch (e: any) {
      toastError(e.message);
    } finally {
      loading = false;
    }
  }

  async function save() {
    if (!activeRole || !editorContent.trim()) return;
    saving = true;
    try {
      const updated = await updatePrompt(activeRole, editorContent);
      // Update local state
      prompts = prompts.map(p => p.role === activeRole ? updated : p);
      originalContent = editorContent;
      toastSuccess(`${updated.label} prompt saved`);
    } catch (e: any) {
      toastError(`Failed to save: ${e.message}`);
    } finally {
      saving = false;
    }
  }

  async function handleReset() {
    if (!activeRole) return;
    resetting = true;
    try {
      const updated = await resetPrompt(activeRole);
      prompts = prompts.map(p => p.role === activeRole ? updated : p);
      editorContent = updated.default_content;
      originalContent = updated.default_content;
      toastSuccess(`${updated.label} prompt reset to default`);
    } catch (e: any) {
      toastError(`Failed to reset: ${e.message}`);
    } finally {
      resetting = false;
    }
  }

  function restoreDefault() {
    const p = activePrompt();
    if (p) {
      editorContent = p.default_content;
    }
  }

  $effect(() => { load(); });
</script>

<div class="space-y-6 animate-fade-in-up">
  <div class="flex items-center justify-between">
    <div>
      <h1 class="text-2xl font-bold">System Prompts</h1>
      <p class="text-sm text-text-dim mt-1">Customize the instructions given to each agent role</p>
    </div>
    <button onclick={() => load()} class="px-3 py-1.5 text-sm glass rounded-lg text-text-dim hover:text-text cursor-pointer transition-colors">
      Refresh
    </button>
  </div>

  {#if loading}
    <div class="flex justify-center py-12"><LoadingSpinner /></div>
  {:else}
    <!-- Role tabs -->
    <div class="flex gap-2 overflow-x-auto pb-1">
      {#each prompts as p}
        <button
          onclick={() => selectRole(p.role)}
          class="relative px-4 py-2 rounded-lg text-sm font-medium whitespace-nowrap cursor-pointer transition-all duration-200
            {activeRole === p.role
              ? 'bg-accent-blue/15 text-accent-blue border border-accent-blue/30'
              : 'glass text-text-dim hover:text-text hover:bg-white/[0.03] border border-transparent'}"
        >
          {p.label}
          {#if p.has_override}
            <span class="absolute -top-1 -right-1 w-2.5 h-2.5 bg-accent-emerald rounded-full border border-bg" title="Custom override active"></span>
          {/if}
        </button>
      {/each}
    </div>

    {#if activeRole}
      {@const current = activePrompt()}
      {#if current}
        <!-- Role info -->
        <GlassCard class="p-4">
          <div class="flex items-center justify-between">
            <div>
              <h3 class="font-semibold">{current.label} Agent</h3>
              <p class="text-sm text-text-dim mt-0.5">{current.description}</p>
            </div>
            <div class="flex items-center gap-2">
              {#if current.has_override}
                <span class="px-2 py-0.5 text-xs rounded-full bg-accent-emerald/15 text-accent-emerald border border-accent-emerald/30">
                  Custom
                </span>
              {:else}
                <span class="px-2 py-0.5 text-xs rounded-full bg-white/5 text-text-dim border border-border/30">
                  Default
                </span>
              {/if}
            </div>
          </div>
        </GlassCard>

        <!-- Editor -->
        <GlassCard glow={hasChanges ? 'amber' : current.has_override ? 'emerald' : undefined} class="p-4">
          <div class="flex items-center justify-between mb-3">
            <h3 class="font-semibold text-sm">
              System Prompt
              {#if hasChanges}
                <span class="text-amber-400 ml-2 text-xs font-normal">Unsaved changes</span>
              {/if}
            </h3>
            {#if current.has_override}
              <button
                onclick={restoreDefault}
                class="text-xs text-text-dim hover:text-text cursor-pointer transition-colors"
                title="Preview what the default looks like (doesn't save)"
              >
                View Default
              </button>
            {/if}
          </div>
          <textarea
            bind:value={editorContent}
            rows="24"
            spellcheck="false"
            class="w-full bg-black/30 border border-border/50 rounded-lg px-4 py-3 text-sm text-text font-mono leading-relaxed resize-y focus:outline-none focus:border-accent-blue/40 transition-colors"
            placeholder="Enter the system prompt for this agent role..."
          ></textarea>
          <p class="text-xs text-text-dim mt-2">
            {editorContent.length.toLocaleString()} characters
          </p>
        </GlassCard>

        <!-- Actions -->
        <div class="flex items-center gap-3">
          <button
            onclick={save}
            disabled={saving || !hasChanges}
            class="px-5 py-2 bg-gradient-to-r from-accent-blue to-accent-emerald text-white rounded-lg text-sm font-medium hover:shadow-lg disabled:opacity-50 cursor-pointer transition-all"
          >
            {saving ? 'Saving...' : 'Save Prompt'}
          </button>
          {#if current.has_override}
            <button
              onclick={handleReset}
              disabled={resetting}
              class="px-5 py-2 glass text-red-400 rounded-lg text-sm font-medium hover:bg-red-500/10 disabled:opacity-50 cursor-pointer transition-colors border border-red-500/20"
            >
              {resetting ? 'Resetting...' : 'Reset to Default'}
            </button>
          {/if}
          {#if hasChanges}
            <button
              onclick={() => selectRole(activeRole)}
              class="px-5 py-2 glass text-text rounded-lg text-sm font-medium hover:bg-white/[0.03] cursor-pointer transition-colors"
            >
              Discard Changes
            </button>
          {/if}
        </div>

        <p class="text-xs text-text-dim">
          Custom prompts are saved to the database and written to <code class="font-data">agent/prompts/custom/</code>.
          The agent scripts will use your custom prompt instead of the default.
          Reset removes the override and reverts to the built-in default.
        </p>
      {/if}
    {/if}
  {/if}
</div>
