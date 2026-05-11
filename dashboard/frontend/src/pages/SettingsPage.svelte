<script lang="ts">
  import { getConfig, updateConfig, getSystemStatus, getAuthStatus, serviceAction,
           listPrompts, updatePrompt, resetPrompt, startOAuthLogin, submitOAuthCode,
           getGitHubAppStatus, startGitHubAppManifest, disconnectGitHubApp,
           setGitHubPAT, clearGitHubPAT,
           refreshOAuthToken,
           getProviderKeys, setProviderKey, clearProviderKey,
           getAutonomyAudit, getAutonomySummary,
           type AutonomyAuditRow, type AutonomySummary,
           type GitHubAppStatus } from '../lib/api';
  import type { PromptInfo, SystemStatus, AuthStatus, ProviderKeysOut, ProviderName } from '../lib/types';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import { navigate } from '../lib/router.svelte';
  import { appearance, setTheme, setAnimationsEnabled } from '../lib/appearance.svelte';
  import { timeAgo } from '../lib/format';

  let { tab = null }: { tab?: string | null } = $props();

  // ── Tabs ─────────────────────────────────────────────
  const TAB_ORDER = ['general', 'models', 'auth', 'github', 'integration', 'prompts', 'audit', 'appearance'] as const;
  type TabKey = typeof TAB_ORDER[number];

  // Map legacy/historical tab names so old deep-links keep working.
  function normaliseTab(t: string | null | undefined): TabKey {
    if (!t) return 'general';
    if (t === 'services') return 'general'; // service moved into General
    if ((TAB_ORDER as readonly string[]).includes(t)) return t as TabKey;
    return 'general';
  }

  let activeTab = $state<TabKey>('general');

  // Sync from prop on mount and whenever it changes (e.g. /autonomy-audit
  // redirect, history nav). Reading `tab` here makes the effect reactive.
  $effect(() => { activeTab = normaliseTab(tab); });

  function selectTab(t: TabKey) {
    if (t === activeTab) return;
    activeTab = t;
    // Persist the tab into the URL so refreshes / deep-links work.
    navigate(`/settings/${t}`);
  }

  // Number-key shortcuts (1..7) when not focused on inputs.
  function onKeydown(e: KeyboardEvent) {
    const target = e.target as HTMLElement;
    if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT') return;
    const n = parseInt(e.key, 10);
    if (n >= 1 && n <= TAB_ORDER.length) {
      selectTab(TAB_ORDER[n - 1]);
    }
  }

  // ── Models ──────────────────────────────────────────
  const MODEL_OPTIONS = [
    { id: 'claude-opus-4-7',           label: 'Opus 4.7 — most capable' },
    { id: 'claude-sonnet-4-6',         label: 'Sonnet 4.6 — balanced' },
    { id: 'claude-haiku-4-5-20251001', label: 'Haiku 4.5 — fast & cheap' },
  ];

  const ROLE_DEFAULTS: Record<string, string> = {
    employee: 'claude-opus-4-7',
    manager:  'claude-sonnet-4-6',
    analyst:  'claude-sonnet-4-6',
    planner:  'claude-sonnet-4-6',
    router:   'claude-haiku-4-5-20251001',
  };

  const ROLE_DESCRIPTIONS: Record<string, string> = {
    employee: 'teammates running implementation work',
    manager:  'verdicts, reviews, plan approvals',
    analyst:  'issue triage, scope decisions',
    planner:  'multi-step plan drafts',
    router:   'tool selection, cheap routing decisions',
  };

  function defaultLabel(role: string): string {
    const id = ROLE_DEFAULTS[role];
    const opt = MODEL_OPTIONS.find(o => o.id === id);
    return opt ? `Default — ${opt.label}` : 'Default';
  }

  // ── Top-level state ─────────────────────────────────
  let config = $state<Record<string, any>>({});
  let prompts = $state<PromptInfo[]>([]);
  let systemStatus = $state<SystemStatus | null>(null);
  let authStatus = $state<AuthStatus | null>(null);
  let githubStatus = $state<GitHubAppStatus | null>(null);
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

  // ── Claude OAuth flow ───────────────────────────────
  type OAuthFlow = 'idle' | 'waiting_for_code' | 'submitting' | 'done';
  let oauthFlow = $state<OAuthFlow>('idle');
  let oauthState = $state('');
  let oauthCode = $state('');
  let oauthError = $state('');
  let refreshing = $state(false);

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

  // ── Provider API keys (OpenAI / Gemini) ─────────────
  let providerKeys = $state<ProviderKeysOut | null>(null);
  let openaiInput = $state('');
  let geminiInput = $state('');
  let savingProvider = $state<ProviderName | null>(null);

  async function loadProviderKeys() {
    try {
      providerKeys = await getProviderKeys();
    } catch (e: any) {
      // Don't toast — auth tab activation shouldn't bark on transient errors
      console.warn('Failed to load provider keys:', e.message);
    }
  }

  async function saveProvider(provider: ProviderName, value: string) {
    const trimmed = value.trim();
    if (!trimmed || savingProvider) return;
    savingProvider = provider;
    try {
      const status = await setProviderKey(provider, trimmed);
      if (providerKeys) providerKeys = { ...providerKeys, [provider]: status };
      if (provider === 'openai') openaiInput = ''; else geminiInput = '';
      toastSuccess(`${provider === 'openai' ? 'OpenAI' : 'Gemini'} key saved`);
    } catch (e: any) {
      toastError(e.message);
    } finally {
      savingProvider = null;
    }
  }

  async function handleClearProvider(provider: ProviderName) {
    const label = provider === 'openai' ? 'OpenAI' : 'Gemini';
    if (!confirm(`Clear the saved ${label} API key? The teammate role using it will fall back to the default Anthropic-only path.`)) {
      return;
    }
    try {
      const status = await clearProviderKey(provider);
      if (providerKeys) providerKeys = { ...providerKeys, [provider]: status };
      toastSuccess(`${label} key cleared`);
    } catch (e: any) {
      toastError(e.message);
    }
  }

  async function handleOAuthRefresh() {
    if (refreshing) return;
    refreshing = true;
    try {
      const res = await refreshOAuthToken();
      if (res.refreshed) {
        toastSuccess('Token refreshed');
        authStatus = await getAuthStatus();
      } else {
        toastError(res.error ?? 'Refresh failed');
      }
    } catch (e: any) {
      toastError(e.message);
    } finally {
      refreshing = false;
    }
  }

  // ── Config persistence ──────────────────────────────
  async function saveConfig(field: string, value: any) {
    try {
      await updateConfig({ [field]: value });
      toastSuccess('Config saved');
    } catch (e: any) { toastError(e.message); }
  }

  async function handleServiceAction(action: string) {
    try {
      await serviceAction(action);
      toastSuccess(`Service ${action}`);
      loadAll();
    } catch (e: any) { toastError(e.message); }
  }

  // ── Prompts ─────────────────────────────────────────
  function selectPrompt(role: string) {
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

  // ── GitHub App / PAT ────────────────────────────────
  let creating = $state(false);

  async function createGitHubApp() {
    if (creating) return;
    creating = true;
    try {
      const { post_url, manifest } = await startGitHubAppManifest();
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
    } catch (e: any) {
      toastError(`Failed to start GitHub App creation: ${e.message}`);
      creating = false;
    }
  }

  function installGitHubApp() {
    if (!githubStatus?.slug) return;
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

  let patInput = $state('');
  let patSaving = $state(false);

  async function savePAT() {
    if (!patInput.trim() || patSaving) return;
    patSaving = true;
    try {
      await setGitHubPAT(patInput.trim());
      patInput = '';
      githubStatus = await getGitHubAppStatus();
      toastSuccess('PAT saved');
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

  // ── Audit (autonomy decisions) ──────────────────────
  let auditSummary = $state<AutonomySummary | null>(null);
  let auditRows = $state<AutonomyAuditRow[]>([]);
  let auditTotal = $state(0);
  let auditLoading = $state(false);
  let runFilter = $state('');
  let toolFilter = $state('');
  let decisionFilter = $state<'' | 'allow' | 'deny'>('');
  let typeFilter = $state<'' | 'auto_mode_decision' | 'auto_mode_referral'>('');

  async function loadAudit() {
    auditLoading = true;
    try {
      const params: Record<string, unknown> = { limit: 200 };
      if (runFilter) params.run_id = runFilter;
      if (toolFilter) params.tool_name = toolFilter;
      if (decisionFilter) params.decision = decisionFilter;
      if (typeFilter) params.event_type = typeFilter;
      const [sum, audit] = await Promise.all([
        getAutonomySummary(30),
        getAutonomyAudit(params),
      ]);
      auditSummary = sum;
      auditRows = audit.items;
      auditTotal = audit.total;
    } finally {
      auditLoading = false;
    }
  }

  // Only fetch audit data when the audit tab is visible — avoids extra
  // requests on every Settings visit.
  $effect(() => {
    if (activeTab !== 'audit') return;
    runFilter; toolFilter; decisionFilter; typeFilter;
    loadAudit();
  });

  // Lazy-load provider keys when the Auth tab activates. The Claude OAuth
  // status is fetched eagerly in loadAll() because it also feeds the
  // sidebar's "off / OAuth" badge; the BYO-key panels only matter inside
  // the tab so we wait until it's open.
  $effect(() => {
    if (activeTab !== 'auth') return;
    if (providerKeys === null) loadProviderKeys();
  });

  // Donut helpers (pure SVG)
  const DONUT_RADIUS = 48;
  const DONUT_CIRC = 2 * Math.PI * DONUT_RADIUS;

  interface DonutSlice { label: string; value: number; offset: number; length: number; color: string; }

  let donutSlices = $derived.by<DonutSlice[]>(() => {
    if (!auditSummary || auditSummary.total_decisions === 0) return [];
    let cum = 0;
    const slices: DonutSlice[] = [];
    const colors: Record<string, string> = {
      auto: 'var(--go)',
      assisted: 'var(--caution)',
      manual: 'var(--graphite)',
      unknown: 'var(--ash)',
    };
    const entries = Object.entries(auditSummary.by_level).sort((a, b) => b[1] - a[1]);
    for (const [level, count] of entries) {
      const fraction = count / auditSummary.total_decisions;
      const length = fraction * DONUT_CIRC;
      slices.push({
        label: level,
        value: count,
        offset: cum,
        length,
        color: colors[level] ?? colors.unknown,
      });
      cum += length;
    }
    return slices;
  });

  function inputPreview(input: Record<string, unknown>): string {
    if (typeof input.command === 'string') return input.command;
    if (typeof input.file_path === 'string') return input.file_path;
    for (const v of Object.values(input)) {
      if (typeof v === 'string') return v;
    }
    return JSON.stringify(input).slice(0, 80);
  }

  // ── Derived display helpers ─────────────────────────
  function formatUptime(seconds?: number): string {
    if (!seconds || seconds <= 0) return '—';
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (d > 0) return `${d}d ${String(h).padStart(2, '0')}h`;
    if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
    return `${m}m`;
  }

  function memoryPercent(): number | null {
    const r = systemStatus?.resources;
    if (!r?.memory_total_mb || !r.memory_used_mb) return null;
    return Math.round((r.memory_used_mb / r.memory_total_mb) * 100);
  }

  function diskPercent(): number | null {
    const r = systemStatus?.resources;
    if (!r?.disk_total_gb || !r.disk_used_gb) return null;
    return Math.round((r.disk_used_gb / r.disk_total_gb) * 100);
  }

  function memoryGB(mb?: number): string {
    if (!mb) return '—';
    return (mb / 1024).toFixed(1);
  }

  function expiresInDays(iso: string | null): string {
    if (!iso) return '—';
    const ms = new Date(iso).getTime() - Date.now();
    if (ms <= 0) return 'expired';
    const days = Math.round(ms / 86_400_000);
    return days === 1 ? '1 day' : `${days} days`;
  }

  let promptCount = $derived(prompts.length);
  let auditDecisionCount = $derived(auditSummary?.total_decisions ?? 0);

  $effect(() => { selectedPrompt; });

  // Pre-select first prompt when entering the Prompts tab.
  $effect(() => {
    if (activeTab === 'prompts' && !selectedPrompt && prompts.length > 0) {
      selectPrompt(prompts[0].role);
    }
  });
</script>

<svelte:window on:keydown={onKeydown} />

<div class="settings-pro animate-fade-in">
  <div class="page-head">
    <h1>Settings</h1>
    <div class="meta">
      Service
      {#if systemStatus?.service.active}
        <b style="color: var(--go)">ONLINE</b>
      {:else}
        <b style="color: var(--abort)">OFFLINE</b>
      {/if}
      {#if systemStatus?.resources?.uptime_seconds}
        · uptime <b>{formatUptime(systemStatus.resources.uptime_seconds)}</b>
      {/if}
      · deploy <b>{systemStatus?.deploy_mode ?? '—'}</b>
    </div>
  </div>

  <section class="settings">
    <nav class="side-tabs" aria-label="Settings sections">
      {#each TAB_ORDER as t}
        <button
          class="side-tab"
          class:active={activeTab === t}
          onclick={() => selectTab(t)}
          data-testid="settings-tab-{t}"
        >
          <span>{t}</span>
          {#if t === 'models'}<span class="meta">5 roles</span>
          {:else if t === 'auth'}<span class="meta">{authStatus?.logged_in && !authStatus?.expired ? 'OAuth' : 'off'}</span>
          {:else if t === 'github'}<span class="meta">{githubStatus?.pat_set ? 'PAT' : (githubStatus?.state === 'installed' ? 'App' : 'off')}</span>
          {:else if t === 'integration'}<span class="meta">{config?.integration?.enabled ? 'on' : 'off'}</span>
          {:else if t === 'prompts'}<span class="meta">{promptCount}</span>
          {:else if t === 'audit'}<span class="meta">{auditDecisionCount} / 30d</span>
          {/if}
        </button>
      {/each}
    </nav>

    <div class="content">

      <!-- GENERAL ─────────────────────────────────────── -->
      {#if activeTab === 'general'}
        <section class="tab-pane active">
          <div class="card-block">
            <h3>Service</h3>
            <div class="key-row">
              <span class="lbl">Status</span>
              <div class="val">
                {#if systemStatus?.service.active}
                  <span class="status-tag go">● ONLINE</span>
                {:else}
                  <span class="status-tag off">OFFLINE</span>
                {/if}
                {#if systemStatus?.resources?.uptime_seconds}
                  &nbsp;<span class="desc" style="display: inline">uptime {formatUptime(systemStatus.resources.uptime_seconds)}</span>
                {/if}
              </div>
            </div>
            <div class="key-row">
              <span class="lbl">Auto-trigger timer</span>
              <div class="val">
                {#if systemStatus?.timer?.active}
                  <span class="status-tag go">ON</span>
                  {#if systemStatus.timer.next_trigger}
                    <span class="desc">Next: {systemStatus.timer.next_trigger}</span>
                  {/if}
                {:else}
                  <span class="status-tag off">OFF</span>
                  <span class="desc">When on, fires a run on a fixed cadence. Currently disabled — runs are user-triggered or webhook-driven.</span>
                {/if}
              </div>
            </div>
            <div class="key-row">
              <span class="lbl">Deploy mode</span>
              <div class="val">{systemStatus?.deploy_mode ?? '—'}</div>
            </div>
            <div class="key-row">
              <span class="lbl">Actions</span>
              <div class="val" style="display: flex; gap: 8px; flex-wrap: wrap;">
                <button class="opbtn" onclick={() => handleServiceAction('start')}>Start</button>
                {#if systemStatus?.deploy_mode !== 'compose'}
                  <button class="opbtn" onclick={() => handleServiceAction('restart')}>Restart</button>
                {/if}
                <button class="opbtn danger" onclick={() => handleServiceAction('stop')}>Stop Service</button>
              </div>
            </div>
          </div>

          <div class="card-block">
            <h3>Resources</h3>
            <div class="key-row">
              <span class="lbl">Memory</span>
              <div class="val">
                {#if systemStatus?.resources?.memory_total_mb}
                  {memoryGB(systemStatus.resources.memory_used_mb)} / {memoryGB(systemStatus.resources.memory_total_mb)} GB
                  {#if memoryPercent() !== null}
                    · <b style="color: {(memoryPercent() ?? 0) > 80 ? 'var(--caution)' : 'var(--ink)'}">{memoryPercent()}%</b>
                  {/if}
                {:else}—{/if}
              </div>
            </div>
            <div class="key-row">
              <span class="lbl">Disk</span>
              <div class="val">
                {#if systemStatus?.resources?.disk_total_gb}
                  {systemStatus.resources.disk_free_gb?.toFixed(1) ?? '—'} G free of {systemStatus.resources.disk_total_gb?.toFixed(0) ?? '—'} G
                  {#if diskPercent() !== null}
                    · <b style="color: {(diskPercent() ?? 0) > 90 ? 'var(--abort)' : (diskPercent() ?? 0) > 75 ? 'var(--caution)' : 'var(--ink)'}">{diskPercent()}% used</b>
                  {/if}
                {:else}—{/if}
              </div>
            </div>
            <div class="key-row">
              <span class="lbl">Load avg</span>
              <div class="val">
                {#if systemStatus?.resources?.load_avg && systemStatus.resources.load_avg.length === 3}
                  {systemStatus.resources.load_avg.map(n => n.toFixed(2)).join(' · ')}
                {:else}—{/if}
              </div>
            </div>
          </div>

          <div class="card-block">
            <h3>Limits</h3>
            <div class="key-row">
              <label for="cfg-max-usage" class="lbl">Max usage %</label>
              <div class="val">
                <input id="cfg-max-usage" type="number" min="10" max="100"
                       value={config.limits?.max_usage_percent ?? 80}
                       onchange={(e) => saveConfig('limits', { ...config.limits, max_usage_percent: parseInt((e.target as HTMLInputElement).value) })} />
                <span class="desc">API budget threshold before throttling</span>
              </div>
            </div>
            <div class="key-row">
              <label for="cfg-max-conc" class="lbl">Max concurrent employees</label>
              <div class="val">
                <input id="cfg-max-conc" type="number" min="1" max="20"
                       value={config.limits?.max_concurrent_employees ?? 2}
                       onchange={(e) => saveConfig('limits', { ...config.limits, max_concurrent_employees: parseInt((e.target as HTMLInputElement).value) })} />
                <span class="desc">number of agents that can run in parallel</span>
              </div>
            </div>
            <div class="key-row">
              <label for="cfg-max-turns" class="lbl">Max employee turns</label>
              <div class="val">
                <input id="cfg-max-turns" type="number" min="10" max="500"
                       value={config.limits?.max_employee_turns ?? 200}
                       onchange={(e) => saveConfig('limits', { ...config.limits, max_employee_turns: parseInt((e.target as HTMLInputElement).value) })} />
                <span class="desc">tool calls per agent before auto-stop</span>
              </div>
            </div>
            <div class="key-row">
              <label for="cfg-schedule" class="lbl">Schedule (cron)</label>
              <div class="val">
                <input id="cfg-schedule" type="text"
                       value={config.schedule ?? ''}
                       onchange={(e) => saveConfig('schedule', (e.target as HTMLInputElement).value)}
                       placeholder="0 * * * *" />
                <span class="desc">how often to trigger autonomous agent runs</span>
              </div>
            </div>
          </div>
        </section>

      <!-- MODELS ──────────────────────────────────────── -->
      {:else if activeTab === 'models'}
        <section class="tab-pane active">
          <div class="card-block">
            <h3>Role → Model</h3>
            {#each ['employee', 'manager', 'analyst', 'planner', 'router'] as role}
              {@const current = config.models?.[role] ?? ''}
              <div class="key-row">
                <label for="model-{role}" class="lbl">{role}</label>
                <div class="val">
                  <select
                    id="model-{role}"
                    value={current}
                    onchange={(e) => saveConfig('models', { ...config.models, [role]: (e.target as HTMLSelectElement).value })}
                  >
                    <option value="">{defaultLabel(role)}</option>
                    {#each MODEL_OPTIONS as opt}
                      <option value={opt.id}>{opt.label}</option>
                    {/each}
                    {#if current && !MODEL_OPTIONS.some(o => o.id === current)}
                      <option value={current}>{current} (custom)</option>
                    {/if}
                  </select>
                  <span class="desc">{ROLE_DESCRIPTIONS[role]}</span>
                </div>
              </div>
            {/each}
          </div>
        </section>

      <!-- AUTH ────────────────────────────────────────── -->
      {:else if activeTab === 'auth'}
        <section class="tab-pane active">
          <div class="card-block">
            <h3>Claude API</h3>
            <div class="key-row">
              <span class="lbl">Auth method</span>
              <div class="val">
                {#if authStatus?.logged_in && !authStatus?.expired}
                  <span class="status-tag go">● OAUTH</span>
                  {#if authStatus.expires_at}
                    &nbsp;<span class="desc" style="display: inline">refresh token healthy · expires in {expiresInDays(authStatus.expires_at)}</span>
                  {/if}
                {:else if authStatus?.expired}
                  <span class="status-tag warn">EXPIRED</span>
                  <span class="desc">re-authenticate or refresh the token</span>
                {:else}
                  <span class="status-tag off">NOT LOGGED IN</span>
                {/if}
              </div>
            </div>
            {#if authStatus?.expires_at}
              <div class="key-row">
                <span class="lbl">Expires at</span>
                <div class="val">{new Date(authStatus.expires_at).toLocaleString()}</div>
              </div>
            {/if}
            <div class="key-row">
              <span class="lbl">Actions</span>
              <div class="val" style="display: flex; gap: 8px; flex-wrap: wrap;">
                {#if oauthFlow === 'idle'}
                  <button
                    class="opbtn primary"
                    type="button"
                    onclick={handleOAuthStart}
                    data-testid="claude-oauth-login-btn"
                  >{authStatus?.logged_in && !authStatus?.expired ? 'Re-authenticate' : 'Login with Claude'}</button>
                  {#if authStatus?.logged_in && authStatus?.auto_refresh_available}
                    <button
                      class="opbtn"
                      type="button"
                      onclick={handleOAuthRefresh}
                      disabled={refreshing}
                    >{refreshing ? 'Refreshing…' : 'Refresh token'}</button>
                  {/if}
                {:else if oauthFlow === 'waiting_for_code'}
                  <input
                    type="text"
                    bind:value={oauthCode}
                    placeholder="Paste authorization code"
                    data-testid="claude-oauth-code-input"
                    onkeydown={(e) => { if ((e as KeyboardEvent).key === 'Enter') handleOAuthSubmit(); }}
                    autocomplete="off"
                    style="min-width: 240px;"
                  />
                  <button
                    class="opbtn primary"
                    type="button"
                    onclick={handleOAuthSubmit}
                    disabled={!oauthCode.trim()}
                    data-testid="claude-oauth-submit-btn"
                  >Submit</button>
                  <button class="opbtn" type="button" onclick={handleOAuthCancel}>Cancel</button>
                {:else if oauthFlow === 'submitting'}
                  <span class="desc" style="display: inline">Exchanging code for tokens…</span>
                {:else if oauthFlow === 'done'}
                  <span class="status-tag go">DONE</span>
                  <span class="desc" style="display: inline">authentication successful</span>
                {/if}
              </div>
            </div>
            {#if oauthFlow === 'waiting_for_code'}
              <div class="key-row">
                <span class="lbl"></span>
                <div class="val">
                  <span class="desc">A new tab opened on claude.ai. Authenticate there, then paste the authorization code into the field above.</span>
                </div>
              </div>
            {/if}
            {#if oauthError}
              <div class="key-row">
                <span class="lbl">Error</span>
                <div class="val" style="color: var(--abort)">{oauthError}</div>
              </div>
            {/if}
          </div>

          <!-- OpenAI Codex API key ─────────────────────── -->
          <div class="card-block">
            <h3>OpenAI Codex</h3>
            <div class="key-row">
              <span class="lbl">Status</span>
              <div class="val">
                {#if providerKeys?.openai.configured}
                  <span class="status-tag go">● CONFIGURED</span>
                  <span class="desc">{providerKeys.openai.masked_key}{providerKeys.openai.last_updated ? ` · updated ${timeAgo(providerKeys.openai.last_updated)}` : ''}</span>
                {:else}
                  <span class="status-tag off">NOT SET</span>
                  <span class="desc">paste an API key — used by the OpenAI Codex teammate role</span>
                {/if}
              </div>
            </div>
            <div class="key-row">
              <label for="openai-key-input" class="lbl">Key</label>
              <div class="val" style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center;">
                <input
                  id="openai-key-input"
                  type="password"
                  bind:value={openaiInput}
                  placeholder="sk-…"
                  data-testid="openai-key-input"
                  autocomplete="off"
                />
                <button
                  class="opbtn primary"
                  type="button"
                  onclick={() => saveProvider('openai', openaiInput)}
                  disabled={savingProvider === 'openai' || !openaiInput.trim()}
                  data-testid="openai-key-save-btn"
                >{savingProvider === 'openai' ? 'Saving…' : 'Save'}</button>
                {#if providerKeys?.openai.configured}
                  <button
                    class="opbtn danger"
                    type="button"
                    onclick={() => handleClearProvider('openai')}
                    data-testid="openai-key-clear-btn"
                  >Clear</button>
                {/if}
              </div>
            </div>
          </div>

          <!-- Google Gemini API key ────────────────────── -->
          <div class="card-block">
            <h3>Google Gemini</h3>
            <div class="key-row">
              <span class="lbl">Status</span>
              <div class="val">
                {#if providerKeys?.gemini.configured}
                  <span class="status-tag go">● CONFIGURED</span>
                  <span class="desc">{providerKeys.gemini.masked_key}{providerKeys.gemini.last_updated ? ` · updated ${timeAgo(providerKeys.gemini.last_updated)}` : ''}</span>
                {:else}
                  <span class="status-tag off">NOT SET</span>
                  <span class="desc">paste an API key — used by the Gemini analyst role</span>
                {/if}
              </div>
            </div>
            <div class="key-row">
              <label for="gemini-key-input" class="lbl">Key</label>
              <div class="val" style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center;">
                <input
                  id="gemini-key-input"
                  type="password"
                  bind:value={geminiInput}
                  placeholder="AIza…"
                  data-testid="gemini-key-input"
                  autocomplete="off"
                />
                <button
                  class="opbtn primary"
                  type="button"
                  onclick={() => saveProvider('gemini', geminiInput)}
                  disabled={savingProvider === 'gemini' || !geminiInput.trim()}
                  data-testid="gemini-key-save-btn"
                >{savingProvider === 'gemini' ? 'Saving…' : 'Save'}</button>
                {#if providerKeys?.gemini.configured}
                  <button
                    class="opbtn danger"
                    type="button"
                    onclick={() => handleClearProvider('gemini')}
                    data-testid="gemini-key-clear-btn"
                  >Clear</button>
                {/if}
              </div>
            </div>
          </div>
        </section>

      <!-- GITHUB ──────────────────────────────────────── -->
      {:else if activeTab === 'github'}
        <section class="tab-pane active">
          <div class="card-block">
            <h3>GitHub Integration</h3>

            <div class="key-row">
              <span class="lbl">Method</span>
              <div class="val">
                {#if githubStatus?.pat_set}
                  <span class="status-tag warn">PAT</span>
                  <span class="desc">PAT set — takes precedence over the App</span>
                {:else if githubStatus?.state === 'installed'}
                  <span class="status-tag go">● APP</span>
                  <span class="desc">GitHub App installed and active</span>
                {:else if githubStatus?.state === 'created_not_installed'}
                  <span class="status-tag warn">APP / NOT INSTALLED</span>
                  <span class="desc">app created but not installed yet</span>
                {:else}
                  <span class="status-tag off">NOT CONFIGURED</span>
                  <span class="desc">install a GitHub App or set a PAT to allow agents to push branches and open PRs</span>
                {/if}
              </div>
            </div>

            {#if githubStatus?.state === 'installed' || githubStatus?.state === 'created_not_installed'}
              <div class="key-row">
                <span class="lbl">App slug</span>
                <div class="val">
                  {#if githubStatus.html_url}
                    <a href={githubStatus.html_url} target="_blank" rel="noopener" style="color: var(--data); text-decoration: none;">{githubStatus.slug}</a>
                  {:else}{githubStatus.slug}{/if}
                </div>
              </div>
              {#if githubStatus.owner}
                <div class="key-row">
                  <span class="lbl">Owner</span>
                  <div class="val">{githubStatus.owner}</div>
                </div>
              {/if}
            {/if}

            <div class="key-row">
              <span class="lbl">Actions</span>
              <div class="val" style="display: flex; gap: 8px; flex-wrap: wrap;">
                {#if !githubStatus || githubStatus.state === 'not_created'}
                  <button
                    class="opbtn primary"
                    type="button"
                    onclick={createGitHubApp}
                    disabled={creating}
                    data-testid="github-app-create-btn"
                  >{creating ? 'Redirecting…' : 'Install GitHub App'}</button>
                {:else if githubStatus.state === 'created_not_installed'}
                  <button
                    class="opbtn primary"
                    type="button"
                    onclick={installGitHubApp}
                    data-testid="github-app-install-btn"
                  >Install on your repos</button>
                  <button class="opbtn danger" type="button" onclick={handleDisconnectGitHubApp}>Disconnect</button>
                {:else if githubStatus.state === 'installed'}
                  <button class="opbtn danger" type="button" onclick={handleDisconnectGitHubApp}>Disconnect App</button>
                {/if}
              </div>
            </div>
          </div>

          <div class="card-block">
            <h3>Personal Access Token</h3>
            <div class="key-row">
              <span class="lbl">Status</span>
              <div class="val">
                {#if githubStatus?.pat_set}
                  <span class="status-tag go">● SAVED</span>
                  <span class="desc">used by the agent — overrides the App</span>
                {:else}
                  <span class="status-tag off">NOT SET</span>
                  <span class="desc">alternative to the App — useful on localhost / private VMs where GitHub can't validate App URLs. Needs <code>repo</code> + <code>workflow</code> scopes.</span>
                {/if}
              </div>
            </div>

            {#if !githubStatus?.pat_set}
              <div class="key-row">
                <label for="pat-input" class="lbl">Token</label>
                <div class="val" style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center;">
                  <input
                    id="pat-input"
                    type="password"
                    bind:value={patInput}
                    placeholder="ghp_…"
                    data-testid="github-pat-input"
                    autocomplete="off"
                  />
                  <button
                    class="opbtn primary"
                    type="button"
                    onclick={savePAT}
                    disabled={patSaving || !patInput.trim()}
                    data-testid="github-pat-save-btn"
                  >{patSaving ? 'Saving…' : 'Save PAT'}</button>
                </div>
              </div>
            {:else}
              <div class="key-row">
                <span class="lbl">Actions</span>
                <div class="val">
                  <button
                    class="opbtn danger"
                    type="button"
                    onclick={clearPAT}
                    data-testid="github-pat-clear-btn"
                  >Clear PAT</button>
                </div>
              </div>
            {/if}
          </div>
        </section>

      <!-- INTEGRATION ─────────────────────────────────── -->
      {:else if activeTab === 'integration'}
        <section class="tab-pane active" data-testid="integration-tab">
          <div class="card-block">
            <h3>Integration Branch</h3>
            <p class="desc" style="margin: 0 0 12px 0;">
              When enabled, agent work for each project lands on a long-lived integration branch
              (e.g. <code>claude-agent-station</code>). A single meta-PR consolidates that work into
              each project's promotion target (configured per-project on the Projects page).
            </p>

            <div class="key-row">
              <label for="int-enabled" class="lbl">Enabled</label>
              <div class="val">
                <input
                  id="int-enabled"
                  type="checkbox"
                  data-testid="integration-enabled"
                  checked={!!config?.integration?.enabled}
                  onchange={(e) => saveConfig('integration', { ...(config.integration ?? {}), enabled: (e.target as HTMLInputElement).checked })}
                />
                <span class="desc">turn on the integration-branch flow for all projects</span>
              </div>
            </div>

            <div class="key-row">
              <label for="int-dev-branch" class="lbl">Integration branch</label>
              <div class="val">
                <input
                  id="int-dev-branch"
                  type="text"
                  data-testid="integration-dev-branch"
                  placeholder="autonomous/dev"
                  value={config?.integration?.dev_branch ?? ''}
                  onchange={(e) => saveConfig('integration', { ...(config.integration ?? {}), dev_branch: (e.target as HTMLInputElement).value.trim() || 'autonomous/dev' })}
                />
                <span class="desc">branch name in each target repo (default <code>autonomous/dev</code>)</span>
              </div>
            </div>

            <div class="key-row">
              <label for="int-strategy" class="lbl">Promotion strategy</label>
              <div class="val">
                <select
                  id="int-strategy"
                  data-testid="integration-strategy"
                  value={config?.integration?.promotion_strategy ?? 'batch'}
                  onchange={(e) => saveConfig('integration', { ...(config.integration ?? {}), promotion_strategy: (e.target as HTMLSelectElement).value })}
                >
                  <option value="batch">batch — one PR with all features</option>
                  <option value="individual">individual — one PR per feature</option>
                </select>
              </div>
            </div>

            <div class="key-row">
              <label for="int-auto-validate" class="lbl">Auto-validate</label>
              <div class="val">
                <input
                  id="int-auto-validate"
                  type="checkbox"
                  data-testid="integration-auto-validate"
                  checked={config?.integration?.auto_validate !== false}
                  onchange={(e) => saveConfig('integration', { ...(config.integration ?? {}), auto_validate: (e.target as HTMLInputElement).checked })}
                />
                <span class="desc">run the project's test suite on the integration branch after each merge</span>
              </div>
            </div>

            <div class="key-row">
              <label for="int-auto-promote" class="lbl">Auto-promote</label>
              <div class="val">
                <input
                  id="int-auto-promote"
                  type="checkbox"
                  data-testid="integration-auto-promote"
                  checked={!!config?.integration?.auto_promote}
                  onchange={(e) => saveConfig('integration', { ...(config.integration ?? {}), auto_promote: (e.target as HTMLInputElement).checked })}
                />
                <span class="desc">open the meta-PR automatically once validation passes</span>
              </div>
            </div>

            <div class="key-row">
              <label for="int-auto-bisect" class="lbl">Auto-bisect</label>
              <div class="val">
                <input
                  id="int-auto-bisect"
                  type="checkbox"
                  data-testid="integration-auto-bisect"
                  checked={config?.integration?.auto_bisect !== false}
                  onchange={(e) => saveConfig('integration', { ...(config.integration ?? {}), auto_bisect: (e.target as HTMLInputElement).checked })}
                />
                <span class="desc">revert the last merge if validation breaks the integration branch</span>
              </div>
            </div>
          </div>
        </section>

      <!-- PROMPTS ─────────────────────────────────────── -->
      {:else if activeTab === 'prompts'}
        <section class="tab-pane active">
          <div class="card-block">
            <h3>System Prompts</h3>
            {#each prompts as p}
              <div class="key-row">
                <span class="lbl">{p.label ?? p.role}</span>
                <div class="val" style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
                  {#if p.has_override}
                    <span class="status-tag warn">CUSTOM</span>
                  {:else}
                    <span class="status-tag off">DEFAULT</span>
                  {/if}
                  <button class="opbtn" type="button" onclick={() => selectPrompt(p.role)}>
                    {selectedPrompt === p.role ? 'Editing…' : 'Edit'}
                  </button>
                </div>
              </div>
            {/each}
            {#if prompts.length === 0}
              <div class="key-row">
                <span class="lbl"></span>
                <div class="val"><span class="desc">No prompts configured.</span></div>
              </div>
            {/if}
          </div>

          {#if selectedPrompt}
            <div class="card-block">
              <h3>Editor — {selectedPrompt}</h3>
              <textarea
                bind:value={promptContent}
                rows={22}
                style="width: 100%; max-width: none; resize: vertical;"
              ></textarea>
              <div style="display: flex; gap: 8px; margin-top: 10px; justify-content: flex-end;">
                <button class="opbtn danger" type="button" onclick={handleResetPrompt}>Reset to default</button>
                <button class="opbtn primary" type="button" onclick={savePrompt}>Save</button>
              </div>
            </div>
          {/if}
        </section>

      <!-- AUDIT ───────────────────────────────────────── -->
      {:else if activeTab === 'audit'}
        <section class="tab-pane active" data-testid="autonomy-audit">
          <div class="card-block">
            <h3>Decisions · Last 30 days</h3>

            {#if auditDecisionCount === 0}
              <p style="font-family: var(--pro-mono); font-size: 12px; color: var(--graphite); margin: 0 0 12px;">
                <span class="status-tag off">0 DECISIONS</span> &nbsp;
                No autonomy decisions logged yet — once teammates run autonomously the allow/deny breakdown will populate here.
              </p>
            {/if}

            <div class="audit-grid">
              <!-- Donut -->
              <div class="donut-card">
                <svg width="120" height="120" viewBox="0 0 120 120" aria-label="Decisions by autonomy level">
                  <circle cx="60" cy="60" r="48" fill="none" stroke="var(--rule)" stroke-width="14" />
                  {#each donutSlices as slice}
                    <circle cx="60" cy="60" r="48" fill="none" stroke={slice.color} stroke-width="14"
                            stroke-dasharray="{slice.length} {DONUT_CIRC - slice.length}"
                            stroke-dashoffset={-slice.offset}
                            transform="rotate(-90 60 60)" />
                  {/each}
                  <text x="60" y="65" text-anchor="middle"
                        style="font-family: var(--pro-mono); font-size: 20px; font-weight: 600; fill: {auditDecisionCount === 0 ? 'var(--ash)' : 'var(--ink)'}">
                    {auditDecisionCount}
                  </text>
                </svg>
                <div class="donut-legend">
                  {#if donutSlices.length === 0}
                    <div class="row"><span class="swatch" style="background: var(--go)"></span><span class="name">auto</span><span class="pct">—</span></div>
                    <div class="row"><span class="swatch" style="background: var(--caution)"></span><span class="name">assisted</span><span class="pct">—</span></div>
                    <div class="row"><span class="swatch" style="background: var(--graphite)"></span><span class="name">manual</span><span class="pct">—</span></div>
                  {:else}
                    {#each donutSlices as slice}
                      <div class="row">
                        <span class="swatch" style="background: {slice.color}"></span>
                        <span class="name">{slice.label}</span>
                        <span class="pct">{slice.value}</span>
                      </div>
                    {/each}
                  {/if}
                </div>
              </div>

              <!-- Allow / Deny -->
              <div>
                <div class="key-row" style="border-bottom: none">
                  <span class="lbl">Allow</span>
                  <div class="val"><b style="color: var(--go); font-size: 18px;">{auditSummary?.by_decision?.allow ?? '—'}</b></div>
                </div>
                <div class="key-row" style="border-bottom: none">
                  <span class="lbl">Deny</span>
                  <div class="val"><b style="color: var(--abort); font-size: 18px;">{auditSummary?.by_decision?.deny ?? '—'}</b></div>
                </div>
                <div class="key-row" style="border-bottom: none">
                  <span class="lbl">Referrals</span>
                  <div class="val">{auditSummary?.by_event_type?.auto_mode_referral ?? '—'}</div>
                </div>
                <div class="key-row" style="border-bottom: none">
                  <span class="lbl">Direct</span>
                  <div class="val">{auditSummary?.by_event_type?.auto_mode_decision ?? '—'}</div>
                </div>
              </div>

              <!-- Top tools -->
              <div>
                <div style="font-family: var(--pro-sans); font-size: 9px; font-weight: 700; letter-spacing: 0.18em; text-transform: uppercase; color: var(--ash); margin-bottom: 6px;">Top tools</div>
                {#if auditSummary && Object.keys(auditSummary.by_tool).length > 0}
                  <div style="font-family: var(--pro-mono); font-size: 12px;">
                    {#each Object.entries(auditSummary.by_tool) as [tool, count]}
                      <div class="key-row" style="border-bottom: 1px dashed var(--rule); grid-template-columns: 1fr auto;">
                        <span style="color: var(--ink);">{tool}</span>
                        <span style="color: var(--graphite);">{count}</span>
                      </div>
                    {/each}
                  </div>
                {:else}
                  <div class="audit-empty">No tool decisions yet</div>
                {/if}
              </div>
            </div>
          </div>

          <div class="card-block">
            <h3>Decision Log</h3>
            <div class="audit-filters">
              <label for="audit-run">Run</label>
              <input id="audit-run" bind:value={runFilter} placeholder="run-id…" style="width: 160px;" />
              <label for="audit-tool">Tool</label>
              <input id="audit-tool" bind:value={toolFilter} placeholder="Bash, Edit…" style="width: 120px;" />
              <label for="audit-decision">Decision</label>
              <select id="audit-decision" bind:value={decisionFilter}>
                <option value="">all</option>
                <option value="allow">allow</option>
                <option value="deny">deny</option>
              </select>
              <label for="audit-type">Type</label>
              <select id="audit-type" bind:value={typeFilter}>
                <option value="">all</option>
                <option value="auto_mode_decision">direct</option>
                <option value="auto_mode_referral">referral</option>
              </select>
              <span style="margin-left: auto">{auditRows.length} of {auditTotal}</span>
            </div>

            <div class="audit-table">
              <div class="audit-row head">
                <span>Time</span>
                <span>Level</span>
                <span>Decision</span>
                <span>Tool</span>
                <span>Input</span>
                <span>Run</span>
              </div>
              {#if auditLoading}
                <div class="audit-empty">Loading…</div>
              {:else if auditRows.length === 0}
                <div class="audit-empty">No decisions match these filters.</div>
              {:else}
                {#each auditRows as row (row.event_id)}
                  <div class="audit-row">
                    <span class="t">{row.created_at ? timeAgo(row.created_at) : '—'}</span>
                    <span class="lev {row.level ?? 'manual'}">{row.level ?? '—'}</span>
                    <span class="dec {row.decision === 'allow' ? 'allow' : 'deny'}">{row.decision}</span>
                    <span class="tool">{row.tool_name}</span>
                    <span class="input" title={inputPreview(row.tool_input)}>{inputPreview(row.tool_input)}</span>
                    <span class="run">
                      {#if row.run_id}<a href="/runs/{row.run_id}">{row.run_id.slice(0, 8)}</a>{:else}—{/if}
                    </span>
                  </div>
                {/each}
              {/if}
            </div>
          </div>
        </section>

      <!-- APPEARANCE ──────────────────────────────────── -->
      {:else if activeTab === 'appearance'}
        <section class="tab-pane active">
          <div class="card-block">
            <h3>Theme</h3>
            <div class="key-row">
              <label for="theme-select" class="lbl">Mode</label>
              <div class="val">
                <select
                  id="theme-select"
                  value={appearance.theme}
                  onchange={(e) => setTheme((e.target as HTMLSelectElement).value as 'light' | 'dark')}
                >
                  <option value="light">Light</option>
                  <option value="dark">Dark</option>
                </select>
                <span class="desc">paper-and-ink in light · charcoal-and-bone in dark</span>
              </div>
            </div>
            <div class="key-row">
              <span class="lbl">Animations</span>
              <div class="val">
                <label class="toggle">
                  <input type="checkbox"
                         checked={appearance.animationsEnabled}
                         onchange={(e) => setAnimationsEnabled((e.target as HTMLInputElement).checked)} />
                  {appearance.animationsEnabled ? 'on' : 'off'}
                </label>
                <span class="desc">honors <code>prefers-reduced-motion</code> regardless</span>
              </div>
            </div>
          </div>
        </section>
      {/if}

    </div>
  </section>
</div>

<style>
  /* All Pro tokens come from lib/design/pro.css. We scope rewrites with the
     .settings-pro wrapper and mark child rules :global so unscoped class
     names like .card-block / .key-row don't get hashed. */
  .settings-pro {
    display: flex;
    flex-direction: column;
    /* Edge-to-edge: cancel <main>'s default padding by stretching to viewport */
    margin: 0;
    background: var(--paper);
    color: var(--ink);
    min-height: 100%;
  }

  .settings-pro :global(.page-head) {
    display: grid; grid-template-columns: auto 1fr;
    align-items: center; gap: 18px;
    padding: 10px 16px;
    border-bottom: 1px solid var(--rule);
    flex-shrink: 0;
  }
  .settings-pro :global(.page-head h1) {
    margin: 0;
    font-family: var(--pro-sans);
    font-size: 14px; font-weight: 700;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--ink);
  }
  .settings-pro :global(.page-head .meta) {
    font-family: var(--pro-mono); font-size: 11px; color: var(--graphite);
  }
  .settings-pro :global(.page-head .meta b) { color: var(--ink); font-weight: 500; }

  .settings-pro :global(.settings) {
    display: grid;
    grid-template-columns: 200px 1fr;
    flex: 1;
    min-height: 0;
  }

  .settings-pro :global(.side-tabs) {
    border-right: 1px solid var(--rule);
    background: var(--paper-2);
    padding: 8px 0;
    display: flex; flex-direction: column;
    overflow-y: auto;
    align-self: stretch;
  }
  .settings-pro :global(.side-tab) {
    font-family: var(--pro-sans); font-size: 11px; font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase;
    color: var(--graphite);
    background: transparent; border: none; cursor: pointer;
    padding: 8px 16px; text-align: left;
    border-left: 3px solid transparent;
    display: flex; justify-content: space-between; align-items: center;
  }
  .settings-pro :global(.side-tab:hover) { color: var(--ink); background: var(--paper-3); }
  .settings-pro :global(.side-tab.active) {
    color: var(--ink); background: var(--paper);
    border-left-color: var(--ink);
  }
  .settings-pro :global(.side-tab .meta) {
    font-family: var(--pro-mono); font-size: 10px; color: var(--ash);
    letter-spacing: 0; text-transform: none;
  }
  .settings-pro :global(.side-tab.active .meta) { color: var(--graphite); }

  .settings-pro :global(.content) { overflow-y: auto; padding: 16px; }
  .settings-pro :global(.tab-pane) { display: block; }

  /* ── Card blocks ─────────────────────────────────── */
  .settings-pro :global(.card-block) {
    background: var(--paper-2);
    border: 1px solid var(--rule);
    padding: 14px 16px;
    margin-bottom: 16px;
    border-radius: 0;
  }
  .settings-pro :global(.card-block h3) {
    margin: 0 0 8px;
    font-family: var(--pro-sans); font-size: 10px;
    font-weight: 700; letter-spacing: 0.2em; text-transform: uppercase;
    color: var(--ash);
    border-bottom: 1px solid var(--rule); padding-bottom: 6px;
  }
  .settings-pro :global(.card-block .key-row) {
    display: grid;
    grid-template-columns: 180px 1fr;
    gap: 14px;
    padding: 6px 0;
    border-bottom: 1px dashed var(--rule);
    font-family: var(--pro-mono); font-size: 12px;
    align-items: center;
  }
  .settings-pro :global(.card-block .key-row:last-child) { border-bottom: none; }
  .settings-pro :global(.card-block .key-row .lbl) {
    color: var(--ash);
    font-family: var(--pro-sans); font-size: 9px; font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase;
  }
  .settings-pro :global(.card-block .key-row .val) { color: var(--ink); }
  .settings-pro :global(.card-block .key-row .val .desc) {
    color: var(--graphite); font-size: 11px; margin-top: 2px; display: block;
  }
  .settings-pro :global(.card-block .key-row .val code) {
    font-family: var(--pro-mono); color: var(--ink);
    background: var(--paper-3); padding: 1px 4px;
  }

  .settings-pro :global(.card-block input),
  .settings-pro :global(.card-block select),
  .settings-pro :global(.card-block textarea) {
    font-family: var(--pro-mono); font-size: 12px;
    background: var(--paper); color: var(--ink);
    border: 1px solid var(--rule-2);
    padding: 4px 8px;
    max-width: 320px;
    border-radius: 0;
  }
  .settings-pro :global(.card-block textarea) { max-width: none; line-height: 1.5; }
  .settings-pro :global(.card-block input:focus),
  .settings-pro :global(.card-block select:focus),
  .settings-pro :global(.card-block textarea:focus) {
    outline: none; border-color: var(--ink);
  }
  .settings-pro :global(.toggle) {
    display: inline-flex; align-items: center; gap: 8px;
    font-family: var(--pro-mono); font-size: 12px;
  }
  .settings-pro :global(.toggle input) { width: auto; max-width: none; }

  /* ── opbtn ───────────────────────────────────────── */
  .settings-pro :global(.opbtn) {
    font-family: var(--pro-sans); font-weight: 700; font-size: 10px;
    letter-spacing: 0.14em; text-transform: uppercase;
    background: transparent; color: var(--ink);
    border: 1px solid var(--rule-2);
    padding: 5px 11px; cursor: pointer; height: 26px;
    border-radius: 0;
  }
  .settings-pro :global(.opbtn:hover) { background: var(--paper-2); }
  .settings-pro :global(.opbtn.primary) { background: var(--ink); color: var(--paper); border-color: var(--ink); }
  .settings-pro :global(.opbtn.primary:hover) { filter: brightness(1.1); background: var(--ink); }
  .settings-pro :global(.opbtn.danger) {
    color: var(--abort);
    border-color: color-mix(in oklab, var(--abort) 50%, transparent);
  }
  .settings-pro :global(.opbtn.danger:hover) {
    background: color-mix(in oklab, var(--abort) 12%, var(--paper));
  }
  .settings-pro :global(.opbtn:disabled) { opacity: 0.5; cursor: not-allowed; }

  /* ── status tags ─────────────────────────────────── */
  .settings-pro :global(.status-tag) {
    font-family: var(--pro-sans); font-size: 9px; font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase;
    padding: 2px 6px; border: 1px solid currentColor;
    display: inline-block; line-height: 1.3;
  }
  .settings-pro :global(.status-tag.go)   { color: var(--go); }
  .settings-pro :global(.status-tag.warn) { color: var(--caution); }
  .settings-pro :global(.status-tag.off)  { color: var(--ash); }

  /* ── Audit ──────────────────────────────────────── */
  .settings-pro :global(.audit-grid) {
    display: grid; grid-template-columns: 1fr 1fr 1fr;
    gap: 16px; margin-bottom: 4px;
  }
  .settings-pro :global(.donut-card) {
    display: flex; align-items: center; gap: 16px;
  }
  .settings-pro :global(.donut-legend) {
    display: flex; flex-direction: column; gap: 6px;
    font-family: var(--pro-mono); font-size: 12px;
  }
  .settings-pro :global(.donut-legend .row) {
    display: grid; grid-template-columns: 14px 80px 50px;
    gap: 6px; align-items: center;
  }
  .settings-pro :global(.donut-legend .row .swatch) { width: 10px; height: 10px; }
  .settings-pro :global(.donut-legend .row .name) { color: var(--ink); text-transform: capitalize; }
  .settings-pro :global(.donut-legend .row .pct) { color: var(--graphite); text-align: right; }

  .settings-pro :global(.audit-filters) {
    display: flex; gap: 8px; flex-wrap: wrap;
    padding: 10px 14px;
    border: 1px solid var(--rule);
    background: var(--paper-2);
    margin-bottom: 12px;
    align-items: center;
    font-family: var(--pro-mono); font-size: 11px; color: var(--graphite);
  }
  .settings-pro :global(.audit-filters label) {
    font-family: var(--pro-sans); font-size: 9px; font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase; color: var(--ash);
  }
  .settings-pro :global(.audit-filters input),
  .settings-pro :global(.audit-filters select) {
    font-family: var(--pro-mono); font-size: 11px;
    background: var(--paper); color: var(--ink);
    border: 1px solid var(--rule-2); padding: 3px 6px;
  }

  .settings-pro :global(.audit-table) {
    border: 1px solid var(--rule);
    background: var(--paper);
  }
  .settings-pro :global(.audit-row) {
    display: grid;
    grid-template-columns: 80px 70px 70px 100px 1fr 80px;
    gap: 10px; padding: 0 14px; align-items: center;
    border-bottom: 1px solid var(--rule);
    height: 32px;
    font-family: var(--pro-mono); font-size: 11px;
  }
  .settings-pro :global(.audit-row:last-child) { border-bottom: none; }
  .settings-pro :global(.audit-row.head) {
    background: var(--paper-2);
    font-family: var(--pro-sans); font-size: 9px;
    font-weight: 700; letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--ash); height: 28px;
  }
  .settings-pro :global(.audit-row .t) { color: var(--ash); font-size: 10px; }
  .settings-pro :global(.audit-row .lev) {
    font-family: var(--pro-sans); font-size: 9px; font-weight: 700;
    letter-spacing: 0.14em; text-transform: uppercase;
    padding: 2px 5px; border: 1px solid currentColor;
    justify-self: start;
  }
  .settings-pro :global(.audit-row .lev.auto)     { color: var(--go); }
  .settings-pro :global(.audit-row .lev.assisted) { color: var(--caution); }
  .settings-pro :global(.audit-row .lev.manual)   { color: var(--graphite); }
  .settings-pro :global(.audit-row .dec) {
    font-family: var(--pro-sans); font-size: 9px; font-weight: 700;
    letter-spacing: 0.14em; text-transform: uppercase;
  }
  .settings-pro :global(.audit-row .dec.allow) { color: var(--go); }
  .settings-pro :global(.audit-row .dec.deny)  { color: var(--abort); }
  .settings-pro :global(.audit-row .tool) { color: var(--ink); }
  .settings-pro :global(.audit-row .input) {
    color: var(--graphite);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .settings-pro :global(.audit-row .run a) {
    color: var(--data); text-decoration: none;
  }

  .settings-pro :global(.audit-empty) {
    border: 1px dashed var(--rule);
    padding: 20px 14px; text-align: center;
    color: var(--ash); font-family: var(--pro-mono); font-size: 11px;
  }

  /* ── Responsive ─────────────────────────────────── */
  @media (max-width: 1180px) {
    .settings-pro :global(.settings) { grid-template-columns: 1fr; }
    .settings-pro :global(.side-tabs) {
      flex-direction: row;
      border-right: none;
      border-bottom: 1px solid var(--rule);
      overflow-x: auto;
      padding: 0;
    }
    .settings-pro :global(.side-tab) {
      border-left: none;
      border-bottom: 2px solid transparent;
      padding: 10px 14px;
    }
    .settings-pro :global(.side-tab.active) {
      border-bottom-color: var(--ink);
      border-left-color: transparent;
    }
    .settings-pro :global(.audit-grid) { grid-template-columns: 1fr; }
  }
  @media (max-width: 720px) {
    .settings-pro :global(.card-block .key-row) {
      grid-template-columns: 1fr;
      gap: 4px;
    }
    .settings-pro :global(.audit-row) {
      grid-template-columns: 1fr;
      gap: 2px;
      height: auto;
      padding: 8px 14px;
    }
  }
</style>
