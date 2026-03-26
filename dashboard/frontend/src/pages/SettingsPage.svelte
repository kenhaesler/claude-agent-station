<script lang="ts">
  import { getConfig, updateConfig, getSystemStatus, getAuthStatus, serviceAction,
           listPrompts, updatePrompt, resetPrompt, startOAuthLogin, getGitHubOAuthStatus,
           startGitHubDeviceFlow, pollGitHubDeviceFlow, refreshOAuthToken } from '../lib/api';
  import type { PromptData, SystemStatus, AuthStatus } from '../lib/types';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import Toggle from '../components/forms/Toggle.svelte';

  let { tab = null }: { tab?: string | null } = $props();

  let config = $state<Record<string, any>>({});
  let prompts = $state<PromptData[]>([]);
  let systemStatus = $state<SystemStatus | null>(null);
  let authStatus = $state<AuthStatus | null>(null);
  let githubStatus = $state<any>(null);
  let activeTab = $state(tab ?? 'general');
  let selectedPrompt = $state<string | null>(null);
  let promptContent = $state('');

  $effect(() => { loadAll(); });

  async function loadAll() {
    const [cRes, pRes, sRes, aRes, gRes] = await Promise.allSettled([
      getConfig(), listPrompts(), getSystemStatus(), getAuthStatus(), getGitHubOAuthStatus(),
    ]);
    if (cRes.status === 'fulfilled') config = cRes.value;
    if (pRes.status === 'fulfilled') prompts = pRes.value;
    if (sRes.status === 'fulfilled') systemStatus = sRes.value;
    if (aRes.status === 'fulfilled') authStatus = aRes.value;
    if (gRes.status === 'fulfilled') githubStatus = gRes.value;
  }

  async function saveConfig(field: string, value: any) {
    try {
      await updateConfig({ [field]: value });
      toastSuccess('Config saved');
    } catch (e: any) { toastError(e.message); }
  }

  async function handleServiceAction(action: string, unit?: string) {
    try {
      await serviceAction(action, unit);
      toastSuccess(`Service ${action}`);
      loadAll();
    } catch (e: any) { toastError(e.message); }
  }

  async function selectPrompt(role: string) {
    selectedPrompt = role;
    const p = prompts.find(p => p.role === role);
    promptContent = p?.custom_content ?? p?.default_content ?? '';
  }

  async function savePrompt() {
    if (!selectedPrompt) return;
    try {
      await updatePrompt(selectedPrompt, promptContent);
      toastSuccess('Prompt saved');
      prompts = await listPrompts();
    } catch (e: any) { toastError(e.message); }
  }

  async function handleResetPrompt() {
    if (!selectedPrompt) return;
    try {
      await resetPrompt(selectedPrompt);
      toastSuccess('Prompt reset');
      prompts = await listPrompts();
      selectPrompt(selectedPrompt);
    } catch (e: any) { toastError(e.message); }
  }
</script>

<div class="space-y-4 animate-fade-in-up max-w-4xl">
  <h1 class="text-lg font-semibold text-text">Settings</h1>

  <!-- Tabs -->
  <div class="flex gap-1 border-b border-border-subtle">
    {#each ['general', 'models', 'services', 'auth', 'prompts'] as t}
      <button
        class="px-3 py-2 text-xs font-medium capitalize transition-colors
               {activeTab === t ? 'text-text border-b-2 border-accent-blue' : 'text-text-muted hover:text-text-dim'}"
        onclick={() => activeTab = t}
      >{t}</button>
    {/each}
  </div>

  {#if activeTab === 'general'}
    <div class="glass rounded-lg p-4 space-y-4">
      <h2 class="text-xs font-semibold text-text-dim uppercase tracking-wider">Limits</h2>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
        <div>
          <label class="text-xs text-text-muted mb-1 block">Max Usage %</label>
          <input
            type="number" min="10" max="100"
            value={config.limits?.max_usage_percent ?? 80}
            onchange={(e) => saveConfig('limits', { ...config.limits, max_usage_percent: parseInt((e.target as HTMLInputElement).value) })}
            class="w-full px-3 py-2 rounded-lg bg-bg text-text text-sm border border-border focus:border-focus outline-none"
          />
        </div>
        <div>
          <label class="text-xs text-text-muted mb-1 block">Max Concurrent Employees</label>
          <input
            type="number" min="1" max="20"
            value={config.limits?.max_concurrent_employees ?? 2}
            onchange={(e) => saveConfig('limits', { ...config.limits, max_concurrent_employees: parseInt((e.target as HTMLInputElement).value) })}
            class="w-full px-3 py-2 rounded-lg bg-bg text-text text-sm border border-border focus:border-focus outline-none"
          />
        </div>
        <div>
          <label class="text-xs text-text-muted mb-1 block">Max Employee Turns</label>
          <input
            type="number" min="10" max="500"
            value={config.limits?.max_employee_turns ?? 200}
            onchange={(e) => saveConfig('limits', { ...config.limits, max_employee_turns: parseInt((e.target as HTMLInputElement).value) })}
            class="w-full px-3 py-2 rounded-lg bg-bg text-text text-sm border border-border focus:border-focus outline-none"
          />
        </div>
        <div>
          <label class="text-xs text-text-muted mb-1 block">Schedule (cron)</label>
          <input
            type="text"
            value={config.schedule ?? ''}
            onchange={(e) => saveConfig('schedule', (e.target as HTMLInputElement).value)}
            class="w-full px-3 py-2 rounded-lg bg-bg text-text text-sm border border-border focus:border-focus outline-none font-mono"
            placeholder="0 * * * *"
          />
        </div>
      </div>
    </div>

  {:else if activeTab === 'models'}
    <div class="glass rounded-lg p-4 space-y-4">
      <h2 class="text-xs font-semibold text-text-dim uppercase tracking-wider">Model Assignments</h2>
      {#each ['employee', 'manager', 'analyst', 'planner'] as role}
        <div class="flex items-center justify-between">
          <span class="text-sm text-text-dim capitalize">{role}</span>
          <input
            type="text"
            value={config.models?.[role] ?? ''}
            onchange={(e) => saveConfig('models', { ...config.models, [role]: (e.target as HTMLInputElement).value })}
            class="w-64 px-3 py-1.5 rounded bg-bg text-text text-xs border border-border focus:border-focus outline-none font-mono"
            placeholder="claude-opus-4-6"
          />
        </div>
      {/each}
    </div>

  {:else if activeTab === 'services'}
    <div class="glass rounded-lg p-4 space-y-4">
      <h2 class="text-xs font-semibold text-text-dim uppercase tracking-wider">System Services</h2>
      <div class="flex items-center justify-between">
        <div>
          <div class="text-sm text-text">Agent Service</div>
          <div class="text-xs text-text-muted">{systemStatus?.service.active ? 'Active' : 'Inactive'}</div>
        </div>
        <div class="flex gap-2">
          <button onclick={() => handleServiceAction('start')} class="px-3 py-1.5 rounded text-xs bg-approve/20 text-approve hover:bg-approve/30 transition-colors">Start</button>
          <button onclick={() => handleServiceAction('stop')} class="px-3 py-1.5 rounded text-xs bg-reject/20 text-reject hover:bg-reject/30 transition-colors">Stop</button>
          <button onclick={() => handleServiceAction('restart')} class="px-3 py-1.5 rounded text-xs bg-warning/20 text-warning hover:bg-warning/30 transition-colors">Restart</button>
        </div>
      </div>
      {#if systemStatus?.timer}
        <div class="flex items-center justify-between text-sm">
          <div>
            <div class="text-text">Timer</div>
            <div class="text-xs text-text-muted">Next: {systemStatus.timer.next_trigger ?? 'N/A'}</div>
          </div>
          <span class="w-2 h-2 rounded-full {systemStatus.timer.active ? 'bg-status-active' : 'bg-status-inactive'}"></span>
        </div>
      {/if}
    </div>

  {:else if activeTab === 'auth'}
    <div class="space-y-4">
      <div class="glass rounded-lg p-4">
        <h2 class="text-xs font-semibold text-text-dim uppercase tracking-wider mb-3">Claude Auth</h2>
        <div class="flex items-center gap-2 text-sm">
          <span class="w-2 h-2 rounded-full {authStatus?.logged_in && !authStatus?.expired ? 'bg-status-active' : 'bg-status-inactive'}"></span>
          <span class="text-text-dim">{authStatus?.logged_in ? (authStatus?.expired ? 'Expired' : 'Authenticated') : 'Not logged in'}</span>
        </div>
        {#if authStatus?.expires_at}
          <div class="text-xs text-text-muted mt-1">Expires: {new Date(authStatus.expires_at).toLocaleString()}</div>
        {/if}
      </div>

      <div class="glass rounded-lg p-4">
        <h2 class="text-xs font-semibold text-text-dim uppercase tracking-wider mb-3">GitHub</h2>
        <div class="flex items-center gap-2 text-sm">
          <span class="w-2 h-2 rounded-full {githubStatus?.connected ? 'bg-status-active' : 'bg-status-inactive'}"></span>
          <span class="text-text-dim">{githubStatus?.connected ? `Connected as ${githubStatus.username}` : 'Not connected'}</span>
        </div>
      </div>
    </div>

  {:else if activeTab === 'prompts'}
    <div class="flex gap-4">
      <!-- Prompt list -->
      <div class="w-48 space-y-1 shrink-0">
        {#each prompts as p}
          <button
            class="w-full text-left px-3 py-2 rounded text-xs transition-colors
                   {selectedPrompt === p.role ? 'bg-surface-2 text-text' : 'text-text-muted hover:text-text-dim hover:bg-surface/50'}"
            onclick={() => selectPrompt(p.role)}
          >
            <div class="capitalize font-medium">{p.label}</div>
            {#if p.has_override}
              <span class="text-[10px] text-warning">customized</span>
            {/if}
          </button>
        {/each}
      </div>

      <!-- Prompt editor -->
      <div class="flex-1">
        {#if selectedPrompt}
          <div class="glass rounded-lg p-4">
            <div class="flex items-center justify-between mb-3">
              <h3 class="text-sm font-semibold text-text capitalize">{selectedPrompt}</h3>
              <div class="flex gap-2">
                <button onclick={handleResetPrompt} class="text-xs text-text-muted hover:text-text transition-colors">Reset</button>
                <button onclick={savePrompt} class="px-3 py-1 rounded text-xs bg-accent-blue/20 text-accent-blue hover:bg-accent-blue/30 transition-colors">Save</button>
              </div>
            </div>
            <textarea
              bind:value={promptContent}
              rows={20}
              class="w-full px-3 py-2 rounded-lg bg-bg text-text text-xs border border-border
                     focus:border-focus outline-none resize-y font-data leading-relaxed"
            ></textarea>
          </div>
        {:else}
          <div class="text-sm text-text-muted text-center py-12">Select a prompt to edit</div>
        {/if}
      </div>
    </div>
  {/if}
</div>
