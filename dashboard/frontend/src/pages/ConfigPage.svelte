<script lang="ts">
  /**
   * ConfigPage — unified configuration zone with tabs:
   * Projects | Settings | Prompts | System
   *
   * Re-uses existing page component internals.
   */
  import type { Project, ProjectCreate, ProjectUpdate, SystemStatus, AuthStatus, StationConfig } from '../lib/types';
  import {
    listProjects, createProject, updateProject, deleteProject,
    getConfig, updateConfig, testNotification,
    getSystemStatus, getAuthStatus, serviceAction, triggerRun,
    startOAuthLogin, submitOAuthCode,
    listPrompts, updatePrompt, resetPrompt, type PromptData,
    searchLogs,
  } from '../lib/api';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import GlassCard from '../components/GlassCard.svelte';
  import Modal from '../components/Modal.svelte';
  import ProjectForm from '../components/ProjectForm.svelte';
  import StatusBadge from '../components/StatusBadge.svelte';
  import StatusOrb from '../components/StatusOrb.svelte';
  import UsageSliders from '../components/UsageSliders.svelte';
  import PlanUsageDisplay from '../components/PlanUsageDisplay.svelte';
  import ResourceMeter from '../components/ResourceMeter.svelte';

  interface Props {
    tab?: string | null;
  }

  let { tab = null }: Props = $props();

  type TabKey = 'projects' | 'settings' | 'prompts' | 'system';
  let activeTab = $state<TabKey>('projects');

  const tabs: { key: TabKey; label: string }[] = [
    { key: 'projects', label: 'Projects' },
    { key: 'settings', label: 'Settings' },
    { key: 'prompts', label: 'Prompts' },
    { key: 'system', label: 'System' },
  ];

  // ── Projects state ──────────────────────────────────
  let projects = $state<Project[]>([]);
  let projectsLoading = $state(true);
  let showProjectModal = $state(false);
  let editingProject = $state<Project | null>(null);

  async function loadProjects() {
    try { projects = await listProjects(); }
    catch (e: any) { toastError(e.message); }
    finally { projectsLoading = false; }
  }

  function openCreateProject() { editingProject = null; showProjectModal = true; }
  function openEditProject(p: Project) { editingProject = p; showProjectModal = true; }

  async function handleProjectSubmit(data: ProjectCreate | ProjectUpdate) {
    try {
      if (editingProject) { await updateProject(editingProject.id, data as ProjectUpdate); toastSuccess('Project updated'); }
      else { await createProject(data as ProjectCreate); toastSuccess('Project created'); }
      showProjectModal = false;
      await loadProjects();
    } catch (e: any) { toastError(e.message); }
  }

  async function handleDeleteProject(p: Project) {
    if (!confirm(`Delete ${p.repo}?`)) return;
    try { await deleteProject(p.id); toastSuccess('Project deleted'); await loadProjects(); }
    catch (e: any) { toastError(e.message); }
  }

  // ── Settings state ──────────────────────────────────
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
  // Intelligence feature flags
  let intelAutoMode = $state(false);
  let intelProgressiveDeepening = $state(false);
  let intelConfidenceGating = $state(false);
  let intelIndependentVerification = $state(false);
  let intelAdaptiveScheduling = $state(false);

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
    const notifyOn = cfg.notifications?.notify_on ?? ['approve', 'reject', 'pr', 'error'];
    notifyOnApprove = notifyOn.includes('approve');
    notifyOnReject = notifyOn.includes('reject');
    notifyOnPr = notifyOn.includes('pr');
    notifyOnError = notifyOn.includes('error');
    dashboardUrl = cfg.notifications?.dashboard_url ?? '';
    telegramChatId = cfg.notifications?.telegram_chat_id ?? '';
    logDir = cfg.logging?.log_dir ?? '';
    digestDir = cfg.logging?.digest_dir ?? '';
    intelAutoMode = cfg.intelligence?.auto_mode_selection ?? false;
    intelProgressiveDeepening = cfg.intelligence?.progressive_deepening ?? false;
    intelConfidenceGating = cfg.intelligence?.confidence_gating ?? false;
    intelIndependentVerification = cfg.intelligence?.independent_verification ?? false;
    intelAdaptiveScheduling = cfg.intelligence?.adaptive_scheduling ?? false;
  }

  async function loadConfig() {
    configLoading = true;
    try {
      const raw = await getConfig();
      const cfg = raw as unknown as StationConfig;
      snapshot = structuredClone(cfg);
      applyConfig(cfg);
    } catch (e: any) { toastError(e.message); }
    finally { configLoading = false; }
  }

  function resetConfig() { applyConfig(snapshot); }

  function buildPayload(): Record<string, unknown> {
    const notifyOn: string[] = [];
    if (notifyOnApprove) notifyOn.push('approve');
    if (notifyOnReject) notifyOn.push('reject');
    if (notifyOnPr) notifyOn.push('pr');
    if (notifyOnError) notifyOn.push('error');
    return {
      models: { employee: employeeModel || undefined, manager: managerModel || undefined },
      limits: {
        max_usage_percent: maxUsagePercent, reserve_percent: reservePercent,
        max_employee_turns: maxEmployeeTurns, max_analyst_turns: maxAnalystTurns,
        max_manager_turns: maxManagerTurns, max_concurrent_employees: maxConcurrentEmployees,
        max_employees_per_project: maxEmployeesPerProject,
        token_budget_strategy: tokenBudgetStrategy || undefined,
      },
      intelligence: {
        auto_mode_selection: intelAutoMode,
        progressive_deepening: intelProgressiveDeepening,
        confidence_gating: intelConfidenceGating,
        independent_verification: intelIndependentVerification,
        adaptive_scheduling: intelAdaptiveScheduling,
      },
      schedule: schedule || undefined,
      notifications: {
        enabled: notificationsEnabled, method: notificationsMethod || undefined,
        notification_file: notificationFile || undefined, webhook_url: webhookUrl || undefined,
        webhook_type: webhookType || undefined, notify_on: notifyOn,
        dashboard_url: dashboardUrl || undefined, telegram_chat_id: telegramChatId || undefined,
      },
      logging: { log_dir: logDir || undefined, digest_dir: digestDir || undefined },
    };
  }

  async function saveConfig() {
    saving = true;
    try {
      const result = await updateConfig(buildPayload());
      const cfg = result as unknown as StationConfig;
      snapshot = structuredClone(cfg);
      applyConfig(cfg);
      toastSuccess('Configuration saved');
    } catch (e: any) { toastError(`Failed: ${e.message}`); }
    finally { saving = false; }
  }

  async function sendTestNotification() {
    testingSending = true;
    try {
      await saveConfig();
      const result = await testNotification();
      if (result.success) toastSuccess(result.message || 'Test notification sent!');
    } catch (e: any) { toastError(`Test failed: ${e.message}`); }
    finally { testingSending = false; }
  }

  // ── System state ──────────────────────────────────
  let system = $state<SystemStatus | null>(null);
  let auth = $state<AuthStatus | null>(null);
  let systemLoading = $state(true);

  type OAuthFlowState = 'idle' | 'waiting_for_code' | 'submitting' | 'done';
  let oauthFlow = $state<OAuthFlowState>('idle');
  let oauthState = $state('');
  let oauthCode = $state('');
  let oauthError = $state('');

  async function loadSystem() {
    try {
      const [sysRes, authRes] = await Promise.all([getSystemStatus(), getAuthStatus()]);
      system = sysRes; auth = authRes;
    } catch (e: any) { toastError(e.message); }
    finally { systemLoading = false; }
  }

  async function doServiceAction(action: string, unit: string) {
    try { await serviceAction(action, unit); toastSuccess(`${action} ${unit}`); await loadSystem(); }
    catch (e: any) { toastError(e.message); }
  }

  async function handleTrigger() {
    try { await triggerRun(); toastSuccess('Run triggered'); await loadSystem(); }
    catch (e: any) { toastError(e.message); }
  }

  async function handleOAuthStart() {
    oauthError = '';
    try {
      const res = await startOAuthLogin();
      oauthState = res.state; oauthFlow = 'waiting_for_code'; oauthCode = '';
      window.open(res.auth_url, '_blank');
    } catch (e: any) { oauthError = e.message; }
  }

  async function handleOAuthSubmit() {
    if (!oauthCode.trim()) return;
    oauthError = ''; oauthFlow = 'submitting';
    try {
      const res = await submitOAuthCode(oauthCode.trim(), oauthState);
      if (res.success) { oauthFlow = 'done'; toastSuccess('Authentication successful'); await loadSystem(); setTimeout(() => { oauthFlow = 'idle'; }, 2000); }
      else { oauthError = res.error || 'Token exchange failed'; oauthFlow = 'waiting_for_code'; }
    } catch (e: any) { oauthError = e.message; oauthFlow = 'waiting_for_code'; }
  }

  function formatUptime(s: number | null | undefined): string {
    if (s == null) return '-';
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    return `${d}d ${h}h ${m}m`;
  }

  // ── Prompts state ──────────────────────────────────
  let promptsLoading = $state(true);
  let promptsList = $state<PromptData[]>([]);
  let activeRole = $state('');
  let editorContent = $state('');
  let promptOriginal = $state('');
  let promptSaving = $state(false);
  let promptResetting = $state(false);
  let hasPromptChanges = $derived(editorContent !== promptOriginal);

  function activePrompt(): PromptData | undefined { return promptsList.find(p => p.role === activeRole); }

  function selectRole(role: string) {
    activeRole = role;
    const p = promptsList.find(pr => pr.role === role);
    if (p) { editorContent = p.custom_content ?? p.default_content; promptOriginal = editorContent; }
  }

  async function loadPrompts() {
    promptsLoading = true;
    try {
      promptsList = await listPrompts();
      if (promptsList.length > 0 && !activeRole) selectRole(promptsList[0].role);
      else if (activeRole) selectRole(activeRole);
    } catch (e: any) { toastError(e.message); }
    finally { promptsLoading = false; }
  }

  async function savePrompt() {
    if (!activeRole || !editorContent.trim()) return;
    promptSaving = true;
    try {
      const updated = await updatePrompt(activeRole, editorContent);
      promptsList = promptsList.map(p => p.role === activeRole ? updated : p);
      promptOriginal = editorContent;
      toastSuccess(`${updated.label} prompt saved`);
    } catch (e: any) { toastError(`Failed: ${e.message}`); }
    finally { promptSaving = false; }
  }

  async function handlePromptReset() {
    if (!activeRole) return;
    promptResetting = true;
    try {
      const updated = await resetPrompt(activeRole);
      promptsList = promptsList.map(p => p.role === activeRole ? updated : p);
      editorContent = updated.default_content; promptOriginal = editorContent;
      toastSuccess(`${updated.label} prompt reset to default`);
    } catch (e: any) { toastError(`Failed: ${e.message}`); }
    finally { promptResetting = false; }
  }

  // ── Lifecycle ──────────────────────────────────
  $effect(() => {
    loadProjects();
    loadConfig();
    loadSystem();
    loadPrompts();
    const sysInterval = setInterval(loadSystem, 10000);
    return () => clearInterval(sysInterval);
  });

  // Sync tab from route param
  $effect(() => {
    if (tab && ['projects', 'settings', 'prompts', 'system'].includes(tab)) {
      activeTab = tab as TabKey;
    }
  });
</script>

<div class="space-y-4 animate-fade-in-up">
  <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
    <h1 class="text-lg font-semibold text-text">Configuration</h1>
    <div class="flex items-center gap-1 glass rounded-lg p-0.5">
      {#each tabs as t}
        <button
          onclick={() => { activeTab = t.key; window.location.hash = `/config/${t.key}`; }}
          class="px-3 py-1.5 text-xs font-medium rounded-md transition-all cursor-pointer
            {activeTab === t.key ? 'bg-info/15 text-info' : 'text-text-dim hover:text-text hover:bg-white/[0.03]'}"
        >
          {t.label}
        </button>
      {/each}
    </div>
  </div>

  <!-- ═══════ PROJECTS TAB ═══════ -->
  {#if activeTab === 'projects'}
    <div class="flex items-center justify-end">
      <button onclick={openCreateProject} class="px-3 py-1.5 text-xs font-medium bg-info text-white rounded-md cursor-pointer hover:opacity-90 transition-opacity">
        Add Project
      </button>
    </div>

    {#if projectsLoading}
      <div class="flex justify-center py-12"><LoadingSpinner /></div>
    {:else if projects.length === 0}
      <EmptyState message="No projects configured" />
    {:else}
      <GlassCard class="overflow-hidden overflow-x-auto">
        <table class="w-full text-sm min-w-[500px]">
          <thead>
            <tr class="border-b border-border/50 text-left text-text-dim">
              <th class="px-3 py-2.5 font-medium text-xs">Repository</th>
              <th class="px-3 py-2.5 font-medium text-xs">Mode</th>
              <th class="px-3 py-2.5 font-medium text-xs hidden sm:table-cell">Priority</th>
              <th class="px-3 py-2.5 font-medium text-xs hidden sm:table-cell">Branch</th>
              <th class="px-3 py-2.5 font-medium text-xs">Status</th>
              <th class="px-3 py-2.5 font-medium text-xs text-right">Actions</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-border/30">
            {#each projects as p}
              <tr class="hover:bg-white/[0.02] transition-colors">
                <td class="px-3 py-2.5 font-data text-xs truncate max-w-[180px]">{p.repo}</td>
                <td class="px-3 py-2.5"><StatusBadge value={p.mode} variant="mode" /></td>
                <td class="px-3 py-2.5 hidden sm:table-cell"><StatusBadge value={p.priority} variant="status" /></td>
                <td class="px-3 py-2.5 text-text-dim hidden sm:table-cell text-xs">{p.branch}</td>
                <td class="px-3 py-2.5">
                  <div class="flex items-center gap-1"><StatusOrb active={p.enabled} /><span class="text-text-dim hidden md:inline text-xs">{p.enabled ? 'On' : 'Off'}</span></div>
                </td>
                <td class="px-3 py-2.5 text-right space-x-2">
                  <button onclick={() => openEditProject(p)} class="text-info hover:underline cursor-pointer text-xs">Edit</button>
                  <button onclick={() => handleDeleteProject(p)} class="text-reject hover:underline cursor-pointer text-xs">Delete</button>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </GlassCard>
    {/if}

    <Modal open={showProjectModal} title={editingProject ? 'Edit Project' : 'Add Project'} onclose={() => showProjectModal = false}>
      <ProjectForm project={editingProject} onsubmit={handleProjectSubmit} oncancel={() => showProjectModal = false} />
    </Modal>

  <!-- ═══════ SETTINGS TAB ═══════ -->
  {:else if activeTab === 'settings'}
    {#if configLoading}
      <div class="flex justify-center py-12"><LoadingSpinner /></div>
    {:else}
      <GlassCard glow="blue" class="p-4">
        <h3 class="text-sm font-semibold mb-3">Plan Usage</h3>
        <PlanUsageDisplay />
      </GlassCard>

      <GlassCard glow="blue" class="p-4">
        <h3 class="text-sm font-semibold mb-1">Usage Budget</h3>
        <p class="text-xs text-text-dim mb-4">Control how much of your Claude plan the agent can consume.</p>
        <UsageSliders {maxUsagePercent} {reservePercent} onMaxUsageChange={(v) => maxUsagePercent = v} onReserveChange={(v) => reservePercent = v} />
      </GlassCard>

      <GlassCard class="p-4">
        <h3 class="text-sm font-semibold mb-3">Models</h3>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <label for="employee-model" class="block text-xs text-text-dim mb-1">Employee</label>
            <input id="employee-model" type="text" bind:value={employeeModel} placeholder="claude-sonnet-4-20250514" class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-1.5 text-sm text-text focus:outline-none focus:border-info/50 transition-colors" />
          </div>
          <div>
            <label for="manager-model" class="block text-xs text-text-dim mb-1">Manager</label>
            <input id="manager-model" type="text" bind:value={managerModel} placeholder="claude-sonnet-4-20250514" class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-1.5 text-sm text-text focus:outline-none focus:border-info/50 transition-colors" />
          </div>
        </div>
      </GlassCard>

      <GlassCard class="p-4">
        <h3 class="text-sm font-semibold mb-3">Parallel Execution</h3>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <label for="max-concurrent" class="block text-xs text-text-dim mb-1">Max Concurrent</label>
            <input id="max-concurrent" type="number" min="1" max="10" bind:value={maxConcurrentEmployees} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-1.5 text-sm text-text focus:outline-none focus:border-info/50 transition-colors" />
          </div>
          <div>
            <label for="max-per-project" class="block text-xs text-text-dim mb-1">Per Project</label>
            <input id="max-per-project" type="number" min="1" max="5" bind:value={maxEmployeesPerProject} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-1.5 text-sm text-text focus:outline-none focus:border-info/50 transition-colors" />
          </div>
          <div>
            <label for="budget-strategy" class="block text-xs text-text-dim mb-1">Budget Strategy</label>
            <select id="budget-strategy" bind:value={tokenBudgetStrategy} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-1.5 text-sm text-text focus:outline-none focus:border-info/50 transition-colors">
              <option value="equal_split">Equal Split</option>
              <option value="priority_weighted">Priority Weighted</option>
            </select>
          </div>
        </div>
      </GlassCard>

      <GlassCard glow="purple" class="p-4">
        <h3 class="text-sm font-semibold mb-1">Intelligence Features</h3>
        <p class="text-xs text-text-dim mb-3">Adaptive intelligence that improves with every run. All features default to off.</p>
        <div class="space-y-2">
          <label class="flex items-center justify-between text-sm text-text cursor-pointer">
            <div>
              <span class="text-xs">Auto Mode Selection</span>
              <p class="text-[10px] text-text-muted">Route issues to optimal mode/model via labels + Haiku scoring</p>
            </div>
            <input type="checkbox" bind:checked={intelAutoMode} class="w-4 h-4 accent-info cursor-pointer" />
          </label>
          <label class="flex items-center justify-between text-sm text-text cursor-pointer">
            <div>
              <span class="text-xs">Progressive Deepening</span>
              <p class="text-[10px] text-text-muted">Escalate to stronger models when initial attempt fails</p>
            </div>
            <input type="checkbox" bind:checked={intelProgressiveDeepening} class="w-4 h-4 accent-info cursor-pointer" />
          </label>
          <label class="flex items-center justify-between text-sm text-text cursor-pointer">
            <div>
              <span class="text-xs">Confidence Gating</span>
              <p class="text-[10px] text-text-muted">Auto-create PRs for high-confidence, test-passing work (skip manager review)</p>
            </div>
            <input type="checkbox" bind:checked={intelConfidenceGating} class="w-4 h-4 accent-info cursor-pointer" />
          </label>
          <label class="flex items-center justify-between text-sm text-text cursor-pointer">
            <div>
              <span class="text-xs">Adaptive Scheduling</span>
              <p class="text-[10px] text-text-muted">Learn from outcomes to optimize future mode/model selection</p>
            </div>
            <input type="checkbox" bind:checked={intelAdaptiveScheduling} class="w-4 h-4 accent-info cursor-pointer" />
          </label>
          <label class="flex items-center justify-between text-sm text-text cursor-pointer">
            <div>
              <span class="text-xs">Independent Verification</span>
              <p class="text-[10px] text-text-muted">Automated code review for high-risk changes (cost-intensive)</p>
            </div>
            <input type="checkbox" bind:checked={intelIndependentVerification} class="w-4 h-4 accent-info cursor-pointer" />
          </label>
        </div>
      </GlassCard>

      <GlassCard class="p-4">
        <h3 class="text-sm font-semibold mb-3">Schedule</h3>
        <input id="schedule-expr" type="text" bind:value={schedule} placeholder="*-*-* 08,12,18:00:00" class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-1.5 text-sm text-text focus:outline-none focus:border-info/50 transition-colors" />
      </GlassCard>

      <GlassCard glow="amber" class="p-4">
        <h3 class="text-sm font-semibold mb-1">Notifications</h3>
        <div class="space-y-3 mt-3">
          <label class="flex items-center gap-2 text-sm text-text cursor-pointer">
            <input type="checkbox" bind:checked={notificationsEnabled} class="w-4 h-4 accent-warning cursor-pointer" /> Enable
          </label>
          {#if notificationsEnabled}
            <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label for="notif-method" class="block text-xs text-text-dim mb-1">Method</label>
                <select id="notif-method" bind:value={notificationsMethod} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-1.5 text-sm text-text focus:outline-none transition-colors">
                  <option value="file">File</option>
                  <option value="webhook">Webhook</option>
                </select>
              </div>
              {#if notificationsMethod === 'webhook'}
                <div>
                  <label for="webhook-url" class="block text-xs text-text-dim mb-1">Webhook URL</label>
                  <input id="webhook-url" type="url" bind:value={webhookUrl} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-1.5 text-sm text-text focus:outline-none transition-colors" />
                </div>
                <div>
                  <label for="webhook-type" class="block text-xs text-text-dim mb-1">Webhook Type</label>
                  <select id="webhook-type" bind:value={webhookType} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-1.5 text-sm text-text focus:outline-none transition-colors">
                    <option value="slack">Slack</option>
                    <option value="discord">Discord</option>
                    <option value="telegram">Telegram</option>
                    <option value="generic">Generic</option>
                  </select>
                </div>
              {/if}
            </div>
            {#if notificationsMethod === 'webhook' && webhookType === 'telegram'}
              <div>
                <label for="telegram-chat-id" class="block text-xs text-text-dim mb-1">Telegram Chat ID</label>
                <input id="telegram-chat-id" type="text" bind:value={telegramChatId} placeholder="-1001234567890" class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-1.5 text-sm text-text focus:outline-none transition-colors" />
              </div>
            {/if}
            {#if notificationsMethod === 'webhook'}
              <button onclick={sendTestNotification} disabled={testingSending || !webhookUrl}
                class="px-3 py-1.5 text-xs font-medium bg-warning/20 text-warning rounded cursor-pointer disabled:opacity-40">
                {testingSending ? 'Sending...' : 'Test Notification'}
              </button>
            {/if}
          {/if}
        </div>
      </GlassCard>

      <button onclick={() => showAdvanced = !showAdvanced} class="text-xs text-text-dim hover:text-text cursor-pointer flex items-center gap-1">
        <span>{showAdvanced ? '▾' : '▸'}</span> Advanced (turn limits, logging)
      </button>

      {#if showAdvanced}
        <GlassCard class="p-4 animate-fade-in-up">
          <h3 class="text-sm font-semibold mb-3">Turn Limits</h3>
          <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div>
              <label for="max-employee-turns" class="block text-xs text-text-dim mb-1">Employee</label>
              <input id="max-employee-turns" type="number" bind:value={maxEmployeeTurns} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-1.5 text-sm text-text focus:outline-none transition-colors" />
            </div>
            <div>
              <label for="max-analyst-turns" class="block text-xs text-text-dim mb-1">Analyst</label>
              <input id="max-analyst-turns" type="number" bind:value={maxAnalystTurns} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-1.5 text-sm text-text focus:outline-none transition-colors" />
            </div>
            <div>
              <label for="max-manager-turns" class="block text-xs text-text-dim mb-1">Manager</label>
              <input id="max-manager-turns" type="number" bind:value={maxManagerTurns} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-1.5 text-sm text-text focus:outline-none transition-colors" />
            </div>
          </div>
        </GlassCard>
        <GlassCard class="p-4">
          <h3 class="text-sm font-semibold mb-3">Logging</h3>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label for="log-dir" class="block text-xs text-text-dim mb-1">Log Dir</label>
              <input id="log-dir" type="text" bind:value={logDir} placeholder="/var/log/claude-agent/" class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-1.5 text-sm text-text focus:outline-none transition-colors" />
            </div>
            <div>
              <label for="digest-dir" class="block text-xs text-text-dim mb-1">Digest Dir</label>
              <input id="digest-dir" type="text" bind:value={digestDir} placeholder="/var/log/claude-agent/digests/" class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-1.5 text-sm text-text focus:outline-none transition-colors" />
            </div>
          </div>
        </GlassCard>
      {/if}

      <div class="flex items-center gap-3">
        <button onclick={saveConfig} disabled={saving} class="px-4 py-1.5 text-xs font-medium bg-info text-white rounded-md cursor-pointer hover:opacity-90 disabled:opacity-50 transition-opacity">
          {saving ? 'Saving...' : 'Save'}
        </button>
        <button onclick={resetConfig} disabled={saving} class="px-4 py-1.5 text-xs font-medium glass text-text-dim rounded-md cursor-pointer hover:bg-white/[0.03] disabled:opacity-50 transition-colors">
          Reset
        </button>
      </div>
    {/if}

  <!-- ═══════ PROMPTS TAB ═══════ -->
  {:else if activeTab === 'prompts'}
    {#if promptsLoading}
      <div class="flex justify-center py-12"><LoadingSpinner /></div>
    {:else}
      <div class="flex gap-2 overflow-x-auto pb-1">
        {#each promptsList as p}
          <button
            onclick={() => selectRole(p.role)}
            class="relative px-3 py-1.5 rounded-md text-xs font-medium whitespace-nowrap cursor-pointer transition-all
              {activeRole === p.role ? 'bg-info/15 text-info border border-info/30' : 'glass text-text-dim hover:text-text border border-transparent'}"
          >
            {p.label}
            {#if p.has_override}
              <span class="absolute -top-0.5 -right-0.5 w-2 h-2 bg-approve rounded-full border border-bg"></span>
            {/if}
          </button>
        {/each}
      </div>

      {#if activeRole}
        {@const current = activePrompt()}
        {#if current}
          <GlassCard class="p-4">
            <div class="flex items-center justify-between">
              <div>
                <h3 class="text-sm font-semibold">{current.label} Agent</h3>
                <p class="text-xs text-text-dim mt-0.5">{current.description}</p>
              </div>
              <span class="px-2 py-0.5 text-[10px] rounded-full {current.has_override ? 'bg-approve/15 text-approve' : 'bg-white/5 text-text-dim'}">
                {current.has_override ? 'Custom' : 'Default'}
              </span>
            </div>
          </GlassCard>

          <GlassCard glow={hasPromptChanges ? 'amber' : current.has_override ? 'emerald' : undefined} class="p-4">
            <textarea
              bind:value={editorContent}
              rows="20"
              spellcheck="false"
              class="w-full bg-black/30 border border-border/50 rounded-lg px-4 py-3 text-sm text-text font-mono leading-relaxed resize-y focus:outline-none focus:border-info/40 transition-colors"
            ></textarea>
            <p class="text-[10px] text-text-muted mt-1">{editorContent.length.toLocaleString()} chars{hasPromptChanges ? ' (unsaved)' : ''}</p>
          </GlassCard>

          <div class="flex items-center gap-3">
            <button onclick={savePrompt} disabled={promptSaving || !hasPromptChanges}
              class="px-4 py-1.5 text-xs font-medium bg-info text-white rounded-md cursor-pointer hover:opacity-90 disabled:opacity-50 transition-opacity">
              {promptSaving ? 'Saving...' : 'Save Prompt'}
            </button>
            {#if current.has_override}
              <button onclick={handlePromptReset} disabled={promptResetting}
                class="px-4 py-1.5 text-xs font-medium glass text-reject rounded-md cursor-pointer hover:bg-reject/10 disabled:opacity-50 transition-colors border border-reject/20">
                {promptResetting ? 'Resetting...' : 'Reset to Default'}
              </button>
            {/if}
          </div>
        {/if}
      {/if}
    {/if}

  <!-- ═══════ SYSTEM TAB ═══════ -->
  {:else if activeTab === 'system'}
    {#if systemLoading}
      <div class="flex justify-center py-12"><LoadingSpinner /></div>
    {:else}
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
        <GlassCard glow={system?.service.active ? 'blue' : 'none'} class="p-4 space-y-3">
          <div class="flex items-center justify-between">
            <h3 class="text-sm font-semibold">Agent Service</h3>
            <div class="flex items-center gap-1.5">
              <StatusOrb active={system?.service.active ?? false} />
              <span class="text-xs">{system?.service.active ? 'Active' : 'Inactive'}</span>
            </div>
          </div>
          <div class="flex flex-wrap gap-2">
            <button onclick={handleTrigger} class="px-3 py-1 text-xs bg-info text-white rounded-md cursor-pointer hover:opacity-90 transition-opacity">Trigger Run</button>
            <button onclick={() => doServiceAction('stop', 'claude-agent.service')} class="px-3 py-1 text-xs glass rounded-md text-text-dim hover:text-text cursor-pointer transition-colors">Stop</button>
          </div>
        </GlassCard>

        <GlassCard glow={system?.timer.active ? 'emerald' : 'none'} class="p-4 space-y-3">
          <div class="flex items-center justify-between">
            <h3 class="text-sm font-semibold">Timer</h3>
            <div class="flex items-center gap-1.5">
              <StatusOrb active={system?.timer.active ?? false} />
              <span class="text-xs">{system?.timer.active ? 'Active' : 'Off'}</span>
            </div>
          </div>
          {#if system?.timer.next_trigger}
            <p class="text-xs text-text-dim">Next: {system.timer.next_trigger}</p>
          {/if}
          <div class="flex flex-wrap gap-2">
            <button onclick={() => doServiceAction('start', 'claude-agent.timer')} class="px-3 py-1 text-xs glass rounded-md text-text-dim hover:text-text cursor-pointer transition-colors">Enable</button>
            <button onclick={() => doServiceAction('stop', 'claude-agent.timer')} class="px-3 py-1 text-xs glass rounded-md text-text-dim hover:text-text cursor-pointer transition-colors">Disable</button>
          </div>
        </GlassCard>
      </div>

      <GlassCard class="p-4 space-y-3">
        <h3 class="text-sm font-semibold">Resources</h3>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
          <ResourceMeter label="Memory" value={system?.resources.memory_used_mb ?? null} max={system?.resources.memory_total_mb ?? 4096} unit="MB" />
          <ResourceMeter label="Disk" value={system?.resources.disk_used_gb ?? null} max={system?.resources.disk_total_gb ?? 100} unit="GB" />
        </div>
        <div class="flex gap-4 text-xs text-text-dim font-data">
          <span>Load: {system?.resources.load_avg?.map(v => v.toFixed(2)).join(', ') ?? '-'}</span>
          <span>Uptime: {formatUptime(system?.resources.uptime_seconds)}</span>
        </div>
      </GlassCard>

      <GlassCard glow={auth?.logged_in && !auth.expired ? 'emerald' : 'red'} class="p-4 space-y-3">
        <h3 class="text-sm font-semibold">Authentication</h3>
        <div class="flex items-center gap-2">
          <StatusOrb active={auth?.logged_in === true && !auth.expired} />
          <span class="text-xs">
            {auth?.logged_in && !auth.expired ? 'Authenticated' : auth?.expired ? 'Expired' : 'Not logged in'}
          </span>
        </div>
        {#if auth?.expires_at}
          <p class="text-[10px] text-text-dim">Expires: {new Date(auth.expires_at).toLocaleString()}</p>
        {/if}
        {#if oauthFlow === 'idle'}
          <button onclick={handleOAuthStart} class="px-3 py-1 text-xs bg-info text-white rounded-md cursor-pointer hover:opacity-90 transition-opacity">
            {auth?.logged_in && !auth.expired ? 'Re-auth' : 'Login'}
          </button>
        {:else if oauthFlow === 'waiting_for_code'}
          <div class="space-y-2">
            <p class="text-xs text-text-dim">Paste the authorization code from the new tab:</p>
            <div class="flex gap-2">
              <input type="text" bind:value={oauthCode} placeholder="Auth code"
                class="flex-1 px-3 py-1 text-sm bg-white/[0.04] border border-border/50 rounded-lg focus:outline-none transition-colors"
                onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter') handleOAuthSubmit(); }} />
              <button onclick={handleOAuthSubmit} disabled={!oauthCode.trim()} class="px-3 py-1 text-xs bg-info text-white rounded-md cursor-pointer disabled:opacity-50">Submit</button>
              <button onclick={() => { oauthFlow = 'idle'; oauthCode = ''; oauthError = ''; }} class="px-3 py-1 text-xs glass rounded-md text-text-dim cursor-pointer">Cancel</button>
            </div>
          </div>
        {:else if oauthFlow === 'submitting'}
          <div class="flex items-center gap-2 text-xs text-text-dim"><LoadingSpinner /> Exchanging...</div>
        {:else if oauthFlow === 'done'}
          <p class="text-xs text-approve">Success!</p>
        {/if}
        {#if oauthError}
          <p class="text-xs text-reject">{oauthError}</p>
        {/if}
      </GlassCard>
    {/if}
  {/if}
</div>
