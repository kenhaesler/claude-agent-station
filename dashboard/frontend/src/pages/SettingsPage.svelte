<script lang="ts">
  import { getConfig, updateConfig, getSystemStatus, getAuthStatus, serviceAction,
           listPrompts, updatePrompt, resetPrompt, startOAuthLogin, submitOAuthCode,
           getGitHubAppStatus, startGitHubAppManifest, disconnectGitHubApp,
           setGitHubPAT, clearGitHubPAT,
           refreshOAuthToken } from '../lib/api';
  import type { GitHubAppStatus } from '../lib/api';
  import type { PromptInfo, SystemStatus, AuthStatus } from '../lib/types';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import Toggle from '../components/forms/Toggle.svelte';
  import { appearance, setTheme, setAnimationsEnabled } from '../lib/appearance.svelte';

  let { tab = null }: { tab?: string | null } = $props();

  let config = $state<Record<string, any>>({});
  let prompts = $state<PromptInfo[]>([]);
  let systemStatus = $state<SystemStatus | null>(null);
  let authStatus = $state<AuthStatus | null>(null);
  let githubStatus = $state<GitHubAppStatus | null>(null);
  let activeTab = $state(tab ?? 'general');
  let selectedPrompt = $state<string | null>(null);
  let promptContent = $state('');

  $effect(() => { loadAll(); });

  async function loadAll() {
    const [cRes, pRes, sRes, aRes, gRes] = await Promise.allSettled([
      getConfig(), listPrompts(), getSystemStatus(), getAuthStatus(), getGitHubAppStatus(),
    ]);
    if (cRes.status === 'fulfilled') config = cRes.value;
    if (pRes.status === 'fulfilled') prompts = pRes.value;
    if (sRes.status === 'fulfilled') systemStatus = sRes.value;
    if (aRes.status === 'fulfilled') authStatus = aRes.value;
    if (gRes.status === 'fulfilled') githubStatus = gRes.value;
  }

  // Claude OAuth flow state
  type OAuthFlow = 'idle' | 'waiting_for_code' | 'submitting' | 'done';
  let oauthFlow = $state<OAuthFlow>('idle');
  let oauthState = $state('');
  let oauthCode = $state('');
  let oauthError = $state('');

  async function handleOAuthStart() {
    oauthError = '';
    try {
      const res = await startOAuthLogin();
      oauthState = res.state;
      oauthCode = '';
      oauthFlow = 'waiting_for_code';
      window.open(res.auth_url, '_blank', 'noopener,noreferrer');
    } catch (e: any) {
      oauthError = e.message;
    }
  }

  async function handleOAuthSubmit() {
    if (!oauthCode.trim()) return;
    oauthError = '';
    oauthFlow = 'submitting';
    try {
      const res = await submitOAuthCode(oauthCode.trim(), oauthState);
      if (res.success) {
        oauthFlow = 'done';
        toastSuccess('Authentication successful');
        authStatus = await getAuthStatus();
        setTimeout(() => { oauthFlow = 'idle'; }, 2000);
      } else {
        oauthError = res.error || 'Token exchange failed';
        oauthFlow = 'waiting_for_code';
      }
    } catch (e: any) {
      oauthError = e.message;
      oauthFlow = 'waiting_for_code';
    }
  }

  function handleOAuthCancel() {
    oauthFlow = 'idle';
    oauthState = '';
    oauthCode = '';
    oauthError = '';
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

  let creating = $state(false);

  async function createGitHubApp() {
    if (creating) return;
    creating = true;
    try {
      const { post_url, manifest } = await startGitHubAppManifest();
      // GitHub's manifest endpoint requires a real form POST — fetch can't
      // submit cross-origin with redirects the way GitHub expects. Build a
      // form, append manifest as a hidden input, submit it.
      const form = document.createElement('form');
      form.method = 'POST';
      form.action = post_url;
      form.target = '_self';
      const input = document.createElement('input');
      input.type = 'hidden';
      input.name = 'manifest';
      input.value = JSON.stringify(manifest);
      form.appendChild(input);
      document.body.appendChild(form);
      form.submit();
      // Browser navigates away to GitHub at this point — the page won't
      // come back here until GitHub redirects to /api/github/app/manifest/exchange.
    } catch (e: any) {
      toastError(`Failed to start GitHub App creation: ${e.message}`);
      creating = false;
    }
  }

  async function installGitHubApp() {
    if (!githubStatus?.slug) return;
    // Open install page in a new tab so the dashboard stays loaded; GitHub
    // will redirect back to setup_url (our /api/github/app/install/callback)
    // which then redirects to /settings?tab=auth, so this tab will end up
    // on the right page after the install flow completes.
    window.location.assign(`https://github.com/apps/${githubStatus.slug}/installations/new`);
  }

  async function handleDisconnectGitHubApp() {
    if (!confirm('Disconnect GitHub? The App will remain in your GitHub account; uninstall manually at github.com/settings/installations if you want to fully remove it.')) {
      return;
    }
    try {
      await disconnectGitHubApp();
      githubStatus = await getGitHubAppStatus();
    } catch (e: any) {
      toastError(e.message);
    }
  }

  // PAT (Personal Access Token) — alternative auth path. When set it
  // takes precedence over the App on the agent's /token endpoint.
  let patInput = $state('');
  let patSaving = $state(false);

  async function savePAT() {
    if (!patInput.trim() || patSaving) return;
    patSaving = true;
    try {
      await setGitHubPAT(patInput.trim());
      patInput = '';
      githubStatus = await getGitHubAppStatus();
    } catch (e: any) {
      toastError(e.message);
    } finally {
      patSaving = false;
    }
  }

  async function clearPAT() {
    if (!confirm('Clear the saved PAT? The agent will fall back to the GitHub App (if installed).')) {
      return;
    }
    try {
      await clearGitHubPAT();
      githubStatus = await getGitHubAppStatus();
    } catch (e: any) {
      toastError(e.message);
    }
  }
</script>

<div class="space-y-4 animate-fade-in max-w-4xl">
  <h1 class="font-heading text-xl">Settings</h1>

  <!-- Tabs -->
  <div class="flex gap-1" style="border-bottom: 1px solid var(--color-border);">
    {#each ['general', 'models', 'services', 'auth', 'prompts', 'appearance'] as t}
      <button
        class="px-4 py-2.5 text-xs font-medium capitalize transition-colors cursor-pointer"
        style="{activeTab === t ? 'color: var(--color-primary); border-bottom: 2px solid var(--color-violet);' : 'color: var(--color-tertiary); border-bottom: 2px solid transparent;'}"
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
            <div class="text-xs text-tertiary">Next: {systemStatus.timer.next_trigger ?? 'N/A'}</div>
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

        <div class="mt-4">
          {#if oauthFlow === 'idle'}
            <button
              type="button"
              onclick={handleOAuthStart}
              data-testid="claude-oauth-login-btn"
              class="btn btn-primary btn-sm text-xs"
            >{authStatus?.logged_in && !authStatus?.expired ? 'Re-authenticate' : 'Login with Claude'}</button>
          {:else if oauthFlow === 'waiting_for_code'}
            <p class="text-xs text-tertiary mb-2">
              A new tab opened on claude.ai. Authenticate there, then copy the
              authorization code shown on the callback page and paste it below.
            </p>
            <div class="flex gap-2">
              <input
                type="text"
                bind:value={oauthCode}
                placeholder="Paste authorization code"
                data-testid="claude-oauth-code-input"
                class="input flex-1 text-xs font-mono"
                onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter') handleOAuthSubmit(); }}
                autocomplete="off"
              />
              <button
                type="button"
                onclick={handleOAuthSubmit}
                disabled={!oauthCode.trim()}
                data-testid="claude-oauth-submit-btn"
                class="btn btn-primary btn-sm text-xs"
              >Submit</button>
              <button
                type="button"
                onclick={handleOAuthCancel}
                class="btn btn-ghost btn-sm text-xs"
              >Cancel</button>
            </div>
          {:else if oauthFlow === 'submitting'}
            <div class="text-xs text-tertiary">Exchanging code for tokens…</div>
          {:else if oauthFlow === 'done'}
            <div class="text-xs" style="color: #2E7D32;">Authentication successful!</div>
          {/if}

          {#if oauthError}
            <p class="text-xs mt-2" style="color: #D06050;">{oauthError}</p>
          {/if}
        </div>
      </div>

      <div class="card p-5">
        <h2 class="section-header mb-3">GitHub</h2>

        {#if !githubStatus || githubStatus.state === 'not_created'}
          <div class="text-sm text-secondary mb-3">
            No GitHub App configured. Creating one registers a private App
            in your own GitHub account, so your user is the App's publisher
            (no third-party shown). After creation you'll pick which repos
            it can access.
          </div>
          <button
            type="button"
            onclick={createGitHubApp}
            disabled={creating}
            data-testid="github-app-create-btn"
            class="btn btn-primary btn-sm text-xs"
          >{creating ? 'Redirecting to GitHub…' : 'Create GitHub App'}</button>

        {:else if githubStatus.state === 'created_not_installed'}
          <div class="flex items-center gap-2 text-sm mb-3">
            <span class="w-2 h-2 rounded-full bg-status-pending"></span>
            <span class="text-secondary">
              App <a href={githubStatus.html_url} target="_blank" rel="noopener" class="text-accent-orange underline">{githubStatus.slug}</a>
              created (owner: {githubStatus.owner}) but not installed yet.
            </span>
          </div>
          <button
            type="button"
            onclick={installGitHubApp}
            data-testid="github-app-install-btn"
            class="btn btn-primary btn-sm text-xs"
          >Install on your repos</button>
          <button
            type="button"
            onclick={handleDisconnectGitHubApp}
            class="btn btn-ghost btn-sm text-xs ml-2"
          >Disconnect</button>

        {:else if githubStatus.state === 'installed'}
          <div class="flex items-center gap-2 text-sm mb-3">
            <span class="w-2 h-2 rounded-full bg-status-active"></span>
            <span class="text-secondary">
              Connected — App <a href={githubStatus.html_url} target="_blank" rel="noopener" class="text-accent-orange underline">{githubStatus.slug}</a>
              installed (owner: {githubStatus.owner})
            </span>
          </div>
          <button
            type="button"
            onclick={handleDisconnectGitHubApp}
            class="btn btn-ghost btn-sm text-xs"
          >Disconnect</button>
        {/if}

        <!-- PAT alternative — independent of the App flow. If both are set,
             the PAT wins (treated as an explicit override). -->
        <div class="mt-5 pt-4 border-t border-tertiary/20">
          <h3 class="text-xs font-semibold text-primary mb-2">Personal Access Token</h3>
          <div class="text-xs text-tertiary mb-3">
            Alternative to the GitHub App — useful when the dashboard is on
            localhost or a private VM where GitHub can't validate App URLs.
            Create one at
            <a href="https://github.com/settings/tokens" target="_blank" rel="noopener" class="text-accent-orange underline">github.com/settings/tokens</a>
            with at least <code class="text-accent-orange">repo</code> and <code class="text-accent-orange">workflow</code> scopes.
            When a PAT is saved it takes precedence over the App.
          </div>

          {#if githubStatus?.pat_set}
            <div class="flex items-center gap-2 text-sm mb-3">
              <span class="w-2 h-2 rounded-full bg-status-active"></span>
              <span class="text-secondary">PAT saved (used by the agent)</span>
            </div>
            <button
              type="button"
              onclick={clearPAT}
              data-testid="github-pat-clear-btn"
              class="btn btn-ghost btn-sm text-xs"
            >Clear PAT</button>
          {:else}
            <div class="flex gap-2">
              <input
                type="password"
                bind:value={patInput}
                placeholder="ghp_…"
                data-testid="github-pat-input"
                class="input flex-1 text-xs font-mono"
                autocomplete="off"
              />
              <button
                type="button"
                onclick={savePAT}
                disabled={patSaving || !patInput.trim()}
                data-testid="github-pat-save-btn"
                class="btn btn-primary btn-sm text-xs"
              >{patSaving ? 'Saving…' : 'Save PAT'}</button>
            </div>
          {/if}
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

  {:else if activeTab === 'appearance'}
    <div class="card p-5 space-y-5">
      <h2 class="section-header">Appearance</h2>

      <div class="flex items-start justify-between gap-6">
        <div>
          <div class="text-sm text-primary font-medium">Dark mode</div>
          <p class="text-[11px] text-tertiary mt-1">Use the warm dark theme. Stored per device.</p>
        </div>
        <Toggle
          checked={appearance.theme === 'dark'}
          onchange={(v) => setTheme(v ? 'dark' : 'light')}
        />
      </div>

      <div class="flex items-start justify-between gap-6">
        <div>
          <div class="text-sm text-primary font-medium">Animations</div>
          <p class="text-[11px] text-tertiary mt-1">Disable to remove background motion, transitions, and pulses.</p>
        </div>
        <Toggle
          checked={appearance.animationsEnabled}
          onchange={(v) => setAnimationsEnabled(v)}
        />
      </div>
    </div>
  {/if}
</div>
