<script lang="ts">
  /**
   * SettingsPage — merges ConfigPage + SystemPage into one (AC4).
   * Tabs: Configuration | System & Auth | Logs
   */
  import type { SystemStatus, AuthStatus, StationConfig } from '../lib/types';
  import {
    getConfig, updateConfig, testNotification, getSystemStatus, getAuthStatus,
    serviceAction, triggerRun, startOAuthLogin, submitOAuthCode,
  } from '../lib/api';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';
  import GlassCard from '../components/GlassCard.svelte';
  import UsageSliders from '../components/UsageSliders.svelte';
  import PlanUsageDisplay from '../components/PlanUsageDisplay.svelte';
  import ResourceMeter from '../components/ResourceMeter.svelte';
  import StatusOrb from '../components/StatusOrb.svelte';

  type Tab = 'config' | 'system' | 'logs';
  let activeTab = $state<Tab>('config');

  // --- Config state ---
  let configLoading = $state(true);
  let saving = $state(false);
  let showAdvanced = $state(false);
  let testingSending = $state(false);

  let employeeModel = $state('');
  let managerModel = $state('');
  let maxUsagePercent = $state(60);
  let reservePercent = $state(40);
  let maxEmployeeTurns = $state<number | undefined>(undefined);
  let maxAnalystTurns = $state<number | undefined>(undefined);
  let maxManagerTurns = $state<number | undefined>(undefined);
  let maxConcurrentEmployees = $state<number | undefined>(undefined);
  let maxEmployeesPerProject = $state<number | undefined>(undefined);
  let tokenBudgetStrategy = $state('');
  let schedule = $state('');
  let notificationsEnabled = $state(false);
  let notificationsMethod = $state('');
  let notificationFile = $state('');
  let webhookUrl = $state('');
  let webhookType = $state('slack');
  let notifyOnApprove = $state(true);
  let notifyOnReject = $state(true);
  let notifyOnPr = $state(true);
  let notifyOnError = $state(true);
  let dashboardUrl = $state('');
  let telegramChatId = $state('');
  let logDir = $state('');
  let digestDir = $state('');
  let snapshot = $state<StationConfig>({});

  // --- System state ---
  let system = $state<SystemStatus | null>(null);
  let auth = $state<AuthStatus | null>(null);
  let systemLoading = $state(true);

  // OAuth flow state
  type OAuthFlowState = 'idle' | 'waiting_for_code' | 'submitting' | 'done';
  let oauthFlow = $state<OAuthFlowState>('idle');
  let oauthState = $state('');
  let oauthCode = $state('');
  let oauthError = $state('');

  function applyConfig(cfg: StationConfig) {
    employeeModel = cfg.models?.employee ?? '';
    managerModel = cfg.models?.manager ?? '';
    maxUsagePercent = cfg.limits?.max_usage_percent ?? 60;
    reservePercent = cfg.limits?.reserve_percent ?? 40;
    maxEmployeeTurns = cfg.limits?.max_employee_turns;
    maxAnalystTurns = cfg.limits?.max_analyst_turns;
    maxManagerTurns = cfg.limits?.max_manager_turns;
    maxConcurrentEmployees = cfg.limits?.max_concurrent_employees;
    maxEmployeesPerProject = cfg.limits?.max_employees_per_project;
    tokenBudgetStrategy = cfg.limits?.token_budget_strategy ?? '';
    schedule = cfg.schedule ?? '';
    notificationsEnabled = cfg.notifications?.enabled ?? false;
    notificationsMethod = cfg.notifications?.method ?? '';
    notificationFile = cfg.notifications?.notification_file ?? '';
    webhookUrl = cfg.notifications?.webhook_url ?? '';
    webhookType = cfg.notifications?.webhook_type ?? 'slack';
    dashboardUrl = cfg.notifications?.dashboard_url ?? '';
    telegramChatId = cfg.notifications?.telegram_chat_id ?? '';
    const notifyOn = cfg.notifications?.notify_on ?? ['approve', 'reject', 'pr', 'error'];
    notifyOnApprove = notifyOn.includes('approve');
    notifyOnReject = notifyOn.includes('reject');
    notifyOnPr = notifyOn.includes('pr');
    notifyOnError = notifyOn.includes('error');
    logDir = cfg.logging?.log_dir ?? '';
    digestDir = cfg.logging?.digest_dir ?? '';
  }

  async function loadConfig() {
    configLoading = true;
    try {
      const raw = await getConfig();
      const cfg = raw as unknown as StationConfig;
      snapshot = structuredClone(cfg);
      applyConfig(cfg);
    } catch (e: any) {
      toastError(e.message);
    } finally {
      configLoading = false;
    }
  }

  async function loadSystem() {
    try {
      const [sysRes, authRes] = await Promise.all([getSystemStatus(), getAuthStatus()]);
      system = sysRes;
      auth = authRes;
    } catch (e: any) {
      toastError(e.message);
    } finally {
      systemLoading = false;
    }
  }

  function reset() { applyConfig(snapshot); }

  function buildNotifyOn(): string[] {
    const list: string[] = [];
    if (notifyOnApprove) list.push('approve');
    if (notifyOnReject) list.push('reject');
    if (notifyOnPr) list.push('pr');
    if (notifyOnError) list.push('error');
    return list;
  }

  function buildPayload(): Record<string, unknown> {
    return {
      models: { employee: employeeModel || undefined, manager: managerModel || undefined },
      limits: {
        max_usage_percent: maxUsagePercent, reserve_percent: reservePercent,
        max_employee_turns: maxEmployeeTurns, max_analyst_turns: maxAnalystTurns,
        max_manager_turns: maxManagerTurns, max_concurrent_employees: maxConcurrentEmployees,
        max_employees_per_project: maxEmployeesPerProject,
        token_budget_strategy: tokenBudgetStrategy || undefined,
      },
      schedule: schedule || undefined,
      notifications: {
        enabled: notificationsEnabled, method: notificationsMethod || undefined,
        notification_file: notificationFile || undefined, webhook_url: webhookUrl || undefined,
        webhook_type: webhookType || undefined, notify_on: buildNotifyOn(),
        dashboard_url: dashboardUrl || undefined, telegram_chat_id: telegramChatId || undefined,
      },
      logging: { log_dir: logDir || undefined, digest_dir: digestDir || undefined },
    };
  }

  async function save() {
    saving = true;
    try {
      const result = await updateConfig(buildPayload());
      const cfg = result as unknown as StationConfig;
      snapshot = structuredClone(cfg);
      applyConfig(cfg);
      toastSuccess('Configuration saved successfully');
    } catch (e: any) {
      toastError(`Failed to save: ${e.message}`);
    } finally {
      saving = false;
    }
  }

  async function sendTestNotification() {
    testingSending = true;
    try {
      await save();
      const result = await testNotification();
      if (result.success) toastSuccess(result.message || 'Test notification sent!');
    } catch (e: any) {
      toastError(`Test failed: ${e.message}`);
    } finally {
      testingSending = false;
    }
  }

  async function handleOAuthStart() {
    oauthError = '';
    try {
      const res = await startOAuthLogin();
      oauthState = res.state;
      oauthFlow = 'waiting_for_code';
      oauthCode = '';
      window.open(res.auth_url, '_blank');
    } catch (e: any) { oauthError = e.message; }
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
        await loadSystem();
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
    oauthFlow = 'idle'; oauthState = ''; oauthCode = ''; oauthError = '';
  }

  async function doServiceAction(action: string, unit: string) {
    try {
      await serviceAction(action, unit);
      toastSuccess(`${action} ${unit}`);
      await loadSystem();
    } catch (e: any) { toastError(e.message); }
  }

  async function handleTrigger() {
    try {
      await triggerRun();
      toastSuccess('Run triggered');
      await loadSystem();
    } catch (e: any) { toastError(e.message); }
  }

  function formatUptime(s: number | null | undefined): string {
    if (s == null) return '-';
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    return `${d}d ${h}h ${m}m`;
  }

  const tabs: { key: Tab; label: string }[] = [
    { key: 'config', label: 'Configuration' },
    { key: 'system', label: 'System & Auth' },
    { key: 'logs', label: 'Logging' },
  ];

  $effect(() => {
    loadConfig();
    loadSystem();
    const sysInterval = setInterval(loadSystem, 10000);
    return () => clearInterval(sysInterval);
  });
</script>

<div class="space-y-6 animate-fade-in-up">
  <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
    <h1 class="text-2xl font-bold">Settings</h1>
    <!-- Tab selector -->
    <div class="flex items-center gap-1 glass rounded-lg p-1">
      {#each tabs as tab}
        <button
          onclick={() => activeTab = tab.key}
          class="px-3 py-1.5 text-xs font-medium rounded-md transition-all duration-200 cursor-pointer
            {activeTab === tab.key
              ? 'bg-accent-blue/20 text-accent-blue'
              : 'text-text-dim hover:text-text hover:bg-white/[0.04]'}"
        >
          {tab.label}
        </button>
      {/each}
    </div>
  </div>

  <!-- ========== CONFIG TAB ========== -->
  {#if activeTab === 'config'}
    {#if configLoading}
      <div class="flex justify-center py-12"><LoadingSpinner /></div>
    {:else}
      <!-- Plan Usage Display -->
      <GlassCard glow="blue" class="p-5">
        <h3 class="font-semibold mb-4 flex items-center gap-2">
          <svg class="w-4 h-4 text-accent-blue" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          Plan Usage
        </h3>
        <PlanUsageDisplay />
      </GlassCard>

      <!-- Usage Budget -->
      <GlassCard glow="blue" class="p-5">
        <h3 class="font-semibold mb-1 flex items-center gap-2">
          <svg class="w-4 h-4 text-accent-blue" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>
          </svg>
          Usage Budget
        </h3>
        <p class="text-xs text-text-dim mb-5">Control how much of your Claude plan the agent can consume.</p>
        <UsageSliders {maxUsagePercent} {reservePercent}
          onMaxUsageChange={(v) => maxUsagePercent = v}
          onReserveChange={(v) => reservePercent = v}
        />
      </GlassCard>

      <!-- Models -->
      <GlassCard class="p-5">
        <h3 class="font-semibold mb-4">Models</h3>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label for="employee-model" class="block text-sm text-text-dim mb-1">Employee Model</label>
            <input id="employee-model" type="text" bind:value={employeeModel} placeholder="claude-sonnet-4-20250514"
              class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-blue/50 transition-colors" />
          </div>
          <div>
            <label for="manager-model" class="block text-sm text-text-dim mb-1">Manager Model</label>
            <input id="manager-model" type="text" bind:value={managerModel} placeholder="claude-sonnet-4-20250514"
              class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-blue/50 transition-colors" />
          </div>
        </div>
      </GlassCard>

      <!-- Parallel Execution -->
      <GlassCard class="p-5">
        <h3 class="font-semibold mb-4">Parallel Execution</h3>
        <p class="text-xs text-text-dim mb-4">Control how many employees run concurrently.</p>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div>
            <label for="max-concurrent" class="block text-sm text-text-dim mb-1">Max Concurrent Employees</label>
            <input id="max-concurrent" type="number" min="1" max="10" bind:value={maxConcurrentEmployees} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-blue/50 transition-colors" />
          </div>
          <div>
            <label for="max-per-project" class="block text-sm text-text-dim mb-1">Max Employees per Project</label>
            <input id="max-per-project" type="number" min="1" max="5" bind:value={maxEmployeesPerProject} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-blue/50 transition-colors" />
          </div>
          <div>
            <label for="budget-strategy" class="block text-sm text-text-dim mb-1">Budget Strategy</label>
            <select id="budget-strategy" bind:value={tokenBudgetStrategy} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-blue/50 transition-colors">
              <option value="equal_split">Equal Split</option>
              <option value="priority_weighted">Priority Weighted</option>
            </select>
          </div>
        </div>
      </GlassCard>

      <!-- Schedule -->
      <GlassCard class="p-5">
        <h3 class="font-semibold mb-4">Schedule</h3>
        <div>
          <label for="schedule-expr" class="block text-sm text-text-dim mb-1">Schedule Expression</label>
          <input id="schedule-expr" type="text" bind:value={schedule} placeholder="*-*-* 08,12,18:00:00" class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-blue/50 transition-colors" />
        </div>
      </GlassCard>

      <!-- Webhook Notifications -->
      <GlassCard glow="amber" class="p-5">
        <h3 class="font-semibold mb-1 flex items-center gap-2">
          <svg class="w-4 h-4 text-accent-amber" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M13.73 21a2 2 0 0 1-3.46 0" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          Webhook Notifications
        </h3>
        <p class="text-xs text-text-dim mb-5">Get real-time alerts via Slack, Discord, Telegram, or any webhook.</p>
        <div class="space-y-4">
          <div class="flex items-center gap-3">
            <input id="notif-enabled" type="checkbox" bind:checked={notificationsEnabled} class="w-4 h-4 accent-accent-amber cursor-pointer" />
            <label for="notif-enabled" class="text-sm text-text cursor-pointer">Enable Notifications</label>
          </div>

          {#if notificationsEnabled}
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label for="notif-method" class="block text-sm text-text-dim mb-1">Method</label>
                <select id="notif-method" bind:value={notificationsMethod} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-amber/50 transition-colors">
                  <option value="file">File</option>
                  <option value="webhook">Webhook</option>
                </select>
              </div>
              {#if notificationsMethod === 'file'}
                <div>
                  <label for="notif-file" class="block text-sm text-text-dim mb-1">Notification File</label>
                  <input id="notif-file" type="text" bind:value={notificationFile} placeholder="/var/lib/claude-agent-station/notifications.json" class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-amber/50 transition-colors" />
                </div>
              {/if}
            </div>

            {#if notificationsMethod === 'webhook'}
              <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label for="webhook-type" class="block text-sm text-text-dim mb-1">Webhook Type</label>
                  <select id="webhook-type" bind:value={webhookType} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-amber/50 transition-colors">
                    <option value="slack">Slack</option>
                    <option value="discord">Discord</option>
                    <option value="telegram">Telegram</option>
                    <option value="generic">Generic (JSON)</option>
                  </select>
                </div>
                <div>
                  <label for="webhook-url" class="block text-sm text-text-dim mb-1">
                    {webhookType === 'telegram' ? 'Bot API URL' : 'Webhook URL'}
                  </label>
                  <input id="webhook-url" type="url" bind:value={webhookUrl}
                    placeholder={webhookType === 'slack' ? 'https://hooks.slack.com/services/...' : webhookType === 'discord' ? 'https://discord.com/api/webhooks/...' : webhookType === 'telegram' ? 'https://api.telegram.org/bot<TOKEN>/sendMessage' : 'https://example.com/webhook'}
                    class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-amber/50 transition-colors" />
                </div>
              </div>

              {#if webhookType === 'telegram'}
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label for="telegram-chat-id" class="block text-sm text-text-dim mb-1">Telegram Chat ID</label>
                    <input id="telegram-chat-id" type="text" bind:value={telegramChatId} placeholder="-1001234567890" class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-amber/50 transition-colors" />
                  </div>
                </div>
              {/if}

              <div>
                <label for="dashboard-url-s" class="block text-sm text-text-dim mb-1">Dashboard URL (for links in notifications)</label>
                <input id="dashboard-url-s" type="url" bind:value={dashboardUrl} placeholder="https://your-server:8420" class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-amber/50 transition-colors" />
              </div>

              <div>
                <p class="text-sm text-text-dim mb-2">Notify on:</p>
                <div class="flex flex-wrap gap-4">
                  <label class="flex items-center gap-2 text-sm text-text cursor-pointer">
                    <input type="checkbox" bind:checked={notifyOnApprove} class="w-4 h-4 accent-accent-emerald cursor-pointer" /> Approve
                  </label>
                  <label class="flex items-center gap-2 text-sm text-text cursor-pointer">
                    <input type="checkbox" bind:checked={notifyOnReject} class="w-4 h-4 accent-accent-red cursor-pointer" /> Reject
                  </label>
                  <label class="flex items-center gap-2 text-sm text-text cursor-pointer">
                    <input type="checkbox" bind:checked={notifyOnPr} class="w-4 h-4 accent-accent-blue cursor-pointer" /> PR Created
                  </label>
                  <label class="flex items-center gap-2 text-sm text-text cursor-pointer">
                    <input type="checkbox" bind:checked={notifyOnError} class="w-4 h-4 accent-accent-amber cursor-pointer" /> Error / Stale Run
                  </label>
                </div>
              </div>

              <div>
                <button onclick={sendTestNotification} disabled={testingSending || !webhookUrl}
                  class="px-4 py-2 bg-gradient-to-r from-accent-amber/80 to-accent-amber text-white rounded-lg text-sm font-medium hover:shadow-lg disabled:opacity-40 cursor-pointer transition-all">
                  {testingSending ? 'Sending...' : 'Send Test Notification'}
                </button>
              </div>
            {/if}
          {/if}
        </div>
      </GlassCard>

      <!-- Advanced Settings -->
      <GlassCard class="p-5">
        <button onclick={() => showAdvanced = !showAdvanced} class="flex items-center gap-2 w-full text-left cursor-pointer">
          <svg class="w-4 h-4 text-text-dim transition-transform duration-200" class:rotate-90={showAdvanced} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18l6-6-6-6"/></svg>
          <h3 class="font-semibold">Advanced Settings</h3>
          <span class="text-xs text-text-dim ml-auto">Turn limits</span>
        </button>
        {#if showAdvanced}
          <div class="mt-5 space-y-6 animate-fade-in-up">
            <div>
              <h4 class="text-sm font-medium text-text-dim mb-3">Turn Limits</h4>
              <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div>
                  <label for="max-employee-turns" class="block text-sm text-text-dim mb-1">Max Employee Turns</label>
                  <input id="max-employee-turns" type="number" bind:value={maxEmployeeTurns} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-blue/50 transition-colors" />
                </div>
                <div>
                  <label for="max-analyst-turns" class="block text-sm text-text-dim mb-1">Max Analyst Turns</label>
                  <input id="max-analyst-turns" type="number" bind:value={maxAnalystTurns} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-blue/50 transition-colors" />
                </div>
                <div>
                  <label for="max-manager-turns" class="block text-sm text-text-dim mb-1">Max Manager Turns</label>
                  <input id="max-manager-turns" type="number" bind:value={maxManagerTurns} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-blue/50 transition-colors" />
                </div>
              </div>
            </div>
          </div>
        {/if}
      </GlassCard>

      <!-- Save / Reset -->
      <div class="flex items-center gap-3">
        <button onclick={save} disabled={saving}
          class="px-5 py-2 bg-gradient-to-r from-accent-blue to-accent-emerald text-white rounded-lg text-sm font-medium hover:shadow-lg disabled:opacity-50 cursor-pointer transition-all">
          {saving ? 'Saving...' : 'Save Changes'}
        </button>
        <button onclick={reset} disabled={saving}
          class="px-5 py-2 glass text-text rounded-lg text-sm font-medium hover:bg-white/[0.03] disabled:opacity-50 cursor-pointer transition-colors">
          Reset
        </button>
      </div>
    {/if}

  <!-- ========== SYSTEM TAB ========== -->
  {:else if activeTab === 'system'}
    {#if systemLoading}
      <div class="flex justify-center py-12"><LoadingSpinner /></div>
    {:else}
      <!-- Service Controls -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <GlassCard glow={system?.service.active ? 'blue' : 'none'} class="p-5 space-y-4">
          <div class="flex items-center justify-between">
            <h3 class="font-semibold">Agent Service</h3>
            <div class="flex items-center gap-2">
              <StatusOrb active={system?.service.active ?? false} />
              <span class="text-sm">{system?.service.active ? 'Active' : 'Inactive'}</span>
            </div>
          </div>
          <div class="flex flex-wrap gap-2">
            <button onclick={handleTrigger} class="px-3 py-1.5 text-sm bg-gradient-to-r from-accent-blue to-accent-emerald text-white rounded-lg hover:shadow-lg cursor-pointer transition-all">Trigger Run</button>
            <button onclick={() => doServiceAction('stop', 'claude-agent.service')} class="px-3 py-1.5 text-sm glass rounded-lg text-text-dim hover:text-text cursor-pointer transition-colors">Stop</button>
          </div>
        </GlassCard>

        <GlassCard glow={system?.timer.active ? 'emerald' : 'none'} class="p-5 space-y-4">
          <div class="flex items-center justify-between">
            <h3 class="font-semibold">Timer</h3>
            <div class="flex items-center gap-2">
              <StatusOrb active={system?.timer.active ?? false} color="#10b981" />
              <span class="text-sm">{system?.timer.active ? 'Active' : 'Inactive'}</span>
            </div>
          </div>
          {#if system?.timer.next_trigger}
            <p class="text-sm text-text-dim">Next: {system.timer.next_trigger}</p>
          {/if}
          <div class="flex flex-wrap gap-2">
            <button onclick={() => doServiceAction('start', 'claude-agent.timer')} class="px-3 py-1.5 text-sm glass rounded-lg text-text-dim hover:text-text cursor-pointer transition-colors">Enable</button>
            <button onclick={() => doServiceAction('stop', 'claude-agent.timer')} class="px-3 py-1.5 text-sm glass rounded-lg text-text-dim hover:text-text cursor-pointer transition-colors">Disable</button>
          </div>
        </GlassCard>
      </div>

      <!-- Resources -->
      <GlassCard class="p-5 space-y-4">
        <h3 class="font-semibold">Resources</h3>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <ResourceMeter label="Memory" value={system?.resources.memory_used_mb ?? null} max={system?.resources.memory_total_mb ?? 4096} unit="MB" />
          <ResourceMeter label="Disk Used" value={system?.resources.disk_used_gb ?? null} max={system?.resources.disk_total_gb ?? 100} unit="GB" />
        </div>
        <div class="flex gap-6 text-sm text-text-dim font-data">
          <span>Load: {system?.resources.load_avg?.map(v => v.toFixed(2)).join(', ') ?? '-'}</span>
          <span>Uptime: {formatUptime(system?.resources.uptime_seconds)}</span>
        </div>
      </GlassCard>

      <!-- Auth Status -->
      <GlassCard glow={auth?.logged_in && !auth.expired ? 'emerald' : 'red'} class="p-5 space-y-3">
        <h3 class="font-semibold">Auth Status</h3>
        <div class="flex items-center gap-3">
          <StatusOrb active={auth?.logged_in === true && !auth.expired} />
          <span class="text-sm">
            {#if auth?.logged_in && !auth.expired}
              Authenticated
            {:else if auth?.logged_in && auth.expired}
              Token Expired
            {:else}
              Not Logged In
            {/if}
          </span>
        </div>
        {#if auth?.expires_at}
          <p class="text-xs text-text-dim">Expires: {new Date(auth.expires_at).toLocaleString()}</p>
        {/if}
        {#if auth?.error}
          <p class="text-xs text-reject">{auth.error}</p>
        {/if}

        {#if oauthFlow === 'idle'}
          <button onclick={handleOAuthStart}
            class="px-3 py-1.5 text-sm bg-gradient-to-r from-accent-blue to-accent-emerald text-white rounded-lg hover:shadow-lg cursor-pointer transition-all">
            {auth?.logged_in && !auth.expired ? 'Re-authenticate' : 'Login with Claude'}
          </button>
        {:else if oauthFlow === 'waiting_for_code'}
          <div class="space-y-2">
            <p class="text-sm text-text-dim">A new tab has opened. Authenticate on claude.ai, then paste the code below.</p>
            <div class="flex gap-2">
              <input type="text" bind:value={oauthCode} placeholder="Paste authorization code"
                class="flex-1 px-3 py-1.5 text-sm bg-white/[0.04] border border-border/50 rounded-lg focus:outline-none focus:border-accent-blue/50 transition-colors"
                onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter') handleOAuthSubmit(); }} />
              <button onclick={handleOAuthSubmit} disabled={!oauthCode.trim()}
                class="px-3 py-1.5 text-sm bg-gradient-to-r from-accent-blue to-accent-emerald text-white rounded-lg cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed">Submit</button>
              <button onclick={handleOAuthCancel}
                class="px-3 py-1.5 text-sm glass rounded-lg text-text-dim hover:text-text cursor-pointer transition-colors">Cancel</button>
            </div>
          </div>
        {:else if oauthFlow === 'submitting'}
          <div class="flex items-center gap-2 text-sm text-text-dim"><LoadingSpinner /><span>Exchanging code for tokens...</span></div>
        {:else if oauthFlow === 'done'}
          <p class="text-sm text-approve">Authentication successful!</p>
        {/if}

        {#if oauthError}
          <p class="text-xs text-reject">{oauthError}</p>
        {/if}
      </GlassCard>
    {/if}

  <!-- ========== LOGS TAB ========== -->
  {:else if activeTab === 'logs'}
    <GlassCard class="p-5">
      <h3 class="font-semibold mb-4">Logging Directories</h3>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label for="log-dir" class="block text-sm text-text-dim mb-1">Log Directory</label>
          <input id="log-dir" type="text" bind:value={logDir} placeholder="/var/log/claude-agent/" class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-blue/50 transition-colors" />
        </div>
        <div>
          <label for="digest-dir" class="block text-sm text-text-dim mb-1">Digest Directory</label>
          <input id="digest-dir" type="text" bind:value={digestDir} placeholder="/var/log/claude-agent/digests/" class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-blue/50 transition-colors" />
        </div>
      </div>
    </GlassCard>

    <GlassCard class="p-5">
      <h3 class="font-semibold mb-3">Log Viewer</h3>
      <p class="text-sm text-text-dim mb-4">For real-time log streaming, use the dedicated log viewer.</p>
      <a href="#/logs" class="px-4 py-2 glass rounded-lg text-sm hover:bg-white/[0.03] transition-colors inline-block">
        Open Log Viewer
      </a>
    </GlassCard>

    <div class="flex items-center gap-3">
      <button onclick={save} disabled={saving}
        class="px-5 py-2 bg-gradient-to-r from-accent-blue to-accent-emerald text-white rounded-lg text-sm font-medium hover:shadow-lg disabled:opacity-50 cursor-pointer transition-all">
        {saving ? 'Saving...' : 'Save Changes'}
      </button>
    </div>
  {/if}
</div>
