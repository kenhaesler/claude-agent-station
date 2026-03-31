<script lang="ts">
  import { getConfig, updateConfig, getSystemStatus, getAuthStatus, serviceAction,
           listPrompts, updatePrompt, resetPrompt, startOAuthLogin, getGitHubOAuthStatus,
           startGitHubDeviceFlow, pollGitHubDeviceFlow, refreshOAuthToken } from '../lib/api';
  import type { PromptInfo, SystemStatus, AuthStatus } from '../lib/types';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import Toggle from '../components/forms/Toggle.svelte';

  let { tab = null }: { tab?: string | null } = $props();

  let config = $state<Record<string, any>>({});
  let prompts = $state<PromptInfo[]>([]);
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

<div class="space-y-4 animate-fade-in max-w-4xl">
  <h1 class="font-heading text-xl">Settings</h1>

  <!-- Tabs -->
  <div class="flex gap-1" style="border-bottom: 1px solid rgba(240,220,200,0.20);">
    {#each ['general', 'models', 'services', 'auth', 'prompts'] as t}
      <button
        class="px-4 py-2.5 text-xs font-medium capitalize transition-colors cursor-pointer"
        style="{activeTab === t ? 'color: #3D2A1A; border-bottom: 2px solid #B06030;' : 'color: #8C7A66; border-bottom: 2px solid transparent;'}"
        onclick={() => activeTab = t}
      >{t}</button>
    {/each}
  </div>

  {#if activeTab === 'general'}
    <div class="card p-5 space-y-5">
      <h2 class="section-header">Limits</h2>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-5 text-sm">
        <div>
          <label class="text-xs text-secondary mb-1.5 block font-medium">Max Usage %</label>
          <input
            type="number" min="10" max="100"
            value={config.limits?.max_usage_percent ?? 80}
            onchange={(e) => saveConfig('limits', { ...config.limits, max_usage_percent: parseInt((e.target as HTMLInputElement).value) })}
            class="input"
          />
          <p class="text-[10px] text-tertiary mt-1">API budget threshold before throttling</p>
        </div>
        <div>
          <label class="text-xs text-secondary mb-1.5 block font-medium">Max Concurrent Employees</label>
          <input
            type="number" min="1" max="20"
            value={config.limits?.max_concurrent_employees ?? 2}
            onchange={(e) => saveConfig('limits', { ...config.limits, max_concurrent_employees: parseInt((e.target as HTMLInputElement).value) })}
            class="input"
          />
          <p class="text-[10px] text-tertiary mt-1">Number of agents that can run in parallel</p>
        </div>
        <div>
          <label class="text-xs text-secondary mb-1.5 block font-medium">Max Employee Turns</label>
          <input
            type="number" min="10" max="500"
            value={config.limits?.max_employee_turns ?? 200}
            onchange={(e) => saveConfig('limits', { ...config.limits, max_employee_turns: parseInt((e.target as HTMLInputElement).value) })}
            class="input"
          />
          <p class="text-[10px] text-tertiary mt-1">Maximum tool calls per agent before auto-stop</p>
        </div>
        <div>
          <label class="text-xs text-secondary mb-1.5 block font-medium">Schedule (cron)</label>
          <input
            type="text"
            value={config.schedule ?? ''}
            onchange={(e) => saveConfig('schedule', (e.target as HTMLInputElement).value)}
            class="input font-mono"
            placeholder="0 * * * *"
          />
          <p class="text-[10px] text-tertiary mt-1">How often to trigger autonomous agent runs</p>
        </div>
      </div>
    </div>

  {:else if activeTab === 'models'}
    <div class="card p-5 space-y-4">
      <h2 class="section-header">Model Assignments</h2>
      <p class="text-xs text-tertiary">Configure which Claude model each agent role uses</p>
      {#each ['employee', 'manager', 'analyst', 'planner'] as role}
        <div class="flex items-center justify-between gap-4">
          <span class="text-sm text-secondary capitalize font-medium w-24">{role}</span>
          <input
            type="text"
            value={config.models?.[role] ?? ''}
            onchange={(e) => saveConfig('models', { ...config.models, [role]: (e.target as HTMLInputElement).value })}
            class="input font-mono text-xs flex-1"
            placeholder="claude-opus-4-6"
          />
        </div>
      {/each}
    </div>

  {:else if activeTab === 'services'}
    <div class="card p-5 space-y-4">
      <h2 class="section-header">System Services</h2>
      <div class="flex items-center justify-between">
        <div>
          <div class="text-sm text-primary">Agent Service</div>
          <div class="text-xs text-tertiary">{systemStatus?.service.active ? 'Active' : 'Inactive'}</div>
        </div>
        <div class="flex gap-2">
          <button onclick={() => handleServiceAction('start')} class="btn btn-sm" style="background: rgba(46,125,50,0.10); color: #2E7D32;">Start</button>
          <button onclick={() => handleServiceAction('stop')} class="btn btn-sm" style="background: rgba(208,96,80,0.10); color: #D06050;">Stop</button>
          <button onclick={() => handleServiceAction('restart')} class="btn btn-sm" style="background: rgba(176,96,48,0.10); color: #B06030;">Restart</button>
        </div>
      </div>
      {#if systemStatus?.timer}
        <div class="flex items-center justify-between text-sm">
          <div>
            <div class="text-primary">Timer</div>
            <div class="text-xs text-tertiary">Next: {systemStatus.timer.next ?? 'N/A'}</div>
          </div>
          <span class="w-2 h-2 rounded-full {systemStatus.timer.active ? 'bg-status-active' : 'bg-status-inactive'}"></span>
        </div>
      {/if}
    </div>

  {:else if activeTab === 'auth'}
    <div class="space-y-4">
      <div class="card p-5">
        <h2 class="section-header mb-3">Claude Auth</h2>
        <div class="flex items-center gap-2 text-sm">
          <span class="w-2 h-2 rounded-full {authStatus?.logged_in && !authStatus?.expired ? 'bg-status-active' : 'bg-status-inactive'}"></span>
          <span class="text-secondary">{authStatus?.logged_in ? (authStatus?.expired ? 'Expired' : 'Authenticated') : 'Not logged in'}</span>
        </div>
        {#if authStatus?.expires_at}
          <div class="text-xs text-tertiary mt-1">Expires: {new Date(authStatus.expires_at).toLocaleString()}</div>
        {/if}
      </div>

      <div class="card p-5">
        <h2 class="section-header mb-3">GitHub</h2>
        <div class="flex items-center gap-2 text-sm">
          <span class="w-2 h-2 rounded-full {githubStatus?.connected ? 'bg-status-active' : 'bg-status-inactive'}"></span>
          <span class="text-secondary">{githubStatus?.connected ? `Connected as ${githubStatus.username}` : 'Not connected'}</span>
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
                   {selectedPrompt === p.role ? 'bg-surface-2 text-primary' : 'text-tertiary hover:text-secondary hover:bg-surface-0/50'}"
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
          <div class="card p-5">
            <div class="flex items-center justify-between mb-3">
              <h3 class="text-sm font-semibold text-primary capitalize">{selectedPrompt}</h3>
              <div class="flex gap-2">
                <button onclick={handleResetPrompt} class="btn btn-ghost btn-sm text-xs">Reset</button>
                <button onclick={savePrompt} class="btn btn-primary btn-sm text-xs">Save</button>
              </div>
            </div>
            <textarea
              bind:value={promptContent}
              rows={20}
              class="input resize-y font-mono text-xs leading-relaxed"
            ></textarea>
          </div>
        {:else}
          <div class="text-sm text-tertiary text-center py-12">Select a prompt to edit</div>
        {/if}
      </div>
    </div>
  {/if}
</div>
