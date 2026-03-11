<script lang="ts">
  import { getConfig, updateConfig } from '../lib/api';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';
  import GlassCard from '../components/GlassCard.svelte';
  import UsageSliders from '../components/UsageSliders.svelte';
  import PlanUsageDisplay from '../components/PlanUsageDisplay.svelte';
  import type { StationConfig } from '../lib/types';

  let loading = $state(true);
  let saving = $state(false);
  let showAdvanced = $state(false);

  // Form state — new simplified fields
  let employeeModel = $state('');
  let managerModel = $state('');

  let maxUsagePercent = $state(60);
  let reservePercent = $state(40);

  // Advanced (kept but hidden by default)
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

  let logDir = $state('');
  let digestDir = $state('');

  // Snapshot for reset
  let snapshot = $state<StationConfig>({});

  function applyConfig(cfg: StationConfig) {
    employeeModel = cfg.models?.employee ?? '';
    managerModel = cfg.models?.manager ?? '';

    // New simplified fields
    maxUsagePercent = cfg.limits?.max_usage_percent ?? 60;
    reservePercent = cfg.limits?.reserve_percent ?? 40;

    // Advanced turn limits (kept)
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

    logDir = cfg.logging?.log_dir ?? '';
    digestDir = cfg.logging?.digest_dir ?? '';
  }

  async function load() {
    loading = true;
    try {
      const raw = await getConfig();
      const cfg = raw as unknown as StationConfig;
      snapshot = structuredClone(cfg);
      applyConfig(cfg);
    } catch (e: any) {
      toastError(e.message);
    } finally {
      loading = false;
    }
  }

  function reset() {
    applyConfig(snapshot);
  }

  function buildPayload(): Record<string, unknown> {
    return {
      models: {
        employee: employeeModel || undefined,
        manager: managerModel || undefined,
      },
      limits: {
        max_usage_percent: maxUsagePercent,
        reserve_percent: reservePercent,
        max_employee_turns: maxEmployeeTurns,
        max_analyst_turns: maxAnalystTurns,
        max_manager_turns: maxManagerTurns,
        max_concurrent_employees: maxConcurrentEmployees,
        max_employees_per_project: maxEmployeesPerProject,
        token_budget_strategy: tokenBudgetStrategy || undefined,
      },
      schedule: schedule || undefined,
      notifications: {
        enabled: notificationsEnabled,
        method: notificationsMethod || undefined,
        notification_file: notificationFile || undefined,
      },
      logging: {
        log_dir: logDir || undefined,
        digest_dir: digestDir || undefined,
      },
    };
  }

  async function save() {
    saving = true;
    try {
      const payload = buildPayload();
      const result = await updateConfig(payload);
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

  $effect(() => { load(); });
</script>

<div class="space-y-6 animate-fade-in-up">
  <div class="flex items-center justify-between">
    <h1 class="text-2xl font-bold">Configuration</h1>
    <button onclick={load} class="px-3 py-1.5 text-sm glass rounded-lg text-text-dim hover:text-text cursor-pointer transition-colors">
      Refresh
    </button>
  </div>

  {#if loading}
    <div class="flex justify-center py-12"><LoadingSpinner /></div>
  {:else}
    <!-- Plan Usage Display -->
    <GlassCard glow="cyan" class="p-5">
      <h3 class="font-semibold mb-4 flex items-center gap-2">
        <svg class="w-4 h-4 text-accent-cyan" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        Plan Usage
      </h3>
      <PlanUsageDisplay />
    </GlassCard>

    <!-- Usage Budget (the main new UI) -->
    <GlassCard glow="blue" class="p-5">
      <h3 class="font-semibold mb-1 flex items-center gap-2">
        <svg class="w-4 h-4 text-accent-blue" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="10"/>
          <path d="M12 6v6l4 2"/>
        </svg>
        Usage Budget
      </h3>
      <p class="text-xs text-text-dim mb-5">
        Control how much of your Claude plan the agent can consume. One simple concept: set a cap and a reserve.
      </p>
      <UsageSliders
        {maxUsagePercent}
        {reservePercent}
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
          <input
            id="employee-model"
            type="text"
            bind:value={employeeModel}
            placeholder="claude-sonnet-4-20250514"
            class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-blue/50 transition-colors"
          />
        </div>
        <div>
          <label for="manager-model" class="block text-sm text-text-dim mb-1">Manager Model</label>
          <input
            id="manager-model"
            type="text"
            bind:value={managerModel}
            placeholder="claude-sonnet-4-20250514"
            class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-blue/50 transition-colors"
          />
        </div>
      </div>
    </GlassCard>

    <!-- Parallel Execution -->
    <GlassCard class="p-5">
      <h3 class="font-semibold mb-4">Parallel Execution</h3>
      <p class="text-xs text-text-dim mb-4">Control how many employees run concurrently. Budget strategy determines how turn limits are divided among parallel employees.</p>
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

    <!-- Advanced Settings (collapsible) -->
    <GlassCard class="p-5">
      <button
        onclick={() => showAdvanced = !showAdvanced}
        class="flex items-center gap-2 w-full text-left cursor-pointer"
      >
        <svg
          class="w-4 h-4 text-text-dim transition-transform duration-200"
          class:rotate-90={showAdvanced}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        >
          <path d="M9 18l6-6-6-6"/>
        </svg>
        <h3 class="font-semibold">Advanced Settings</h3>
        <span class="text-xs text-text-dim ml-auto">Turn limits, notifications, logging</span>
      </button>

      {#if showAdvanced}
        <div class="mt-5 space-y-6 animate-fade-in-up">
          <!-- Turn Limits -->
          <div>
            <h4 class="text-sm font-medium text-text-dim mb-3">Turn Limits</h4>
            <p class="text-xs text-text-dim mb-3">Controls quality/depth per agent role. Higher = more thorough but slower.</p>
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

          <!-- Notifications -->
          <div>
            <h4 class="text-sm font-medium text-text-dim mb-3">Notifications</h4>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div class="flex items-center gap-3 md:col-span-2">
                <input id="notif-enabled" type="checkbox" bind:checked={notificationsEnabled} class="w-4 h-4 accent-accent-blue cursor-pointer" />
                <label for="notif-enabled" class="text-sm text-text cursor-pointer">Enabled</label>
              </div>
              <div>
                <label for="notif-method" class="block text-sm text-text-dim mb-1">Method</label>
                <input id="notif-method" type="text" bind:value={notificationsMethod} placeholder="file" class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-blue/50 transition-colors" />
              </div>
              <div>
                <label for="notif-file" class="block text-sm text-text-dim mb-1">Notification File</label>
                <input id="notif-file" type="text" bind:value={notificationFile} placeholder="/var/lib/claude-agent-station/notifications.json" class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-blue/50 transition-colors" />
              </div>
            </div>
          </div>

          <!-- Logging -->
          <div>
            <h4 class="text-sm font-medium text-text-dim mb-3">Logging</h4>
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
          </div>
        </div>
      {/if}
    </GlassCard>

    <!-- Actions -->
    <div class="flex items-center gap-3">
      <button
        onclick={save}
        disabled={saving}
        class="px-5 py-2 bg-gradient-to-r from-accent-blue to-accent-emerald text-white rounded-lg text-sm font-medium hover:shadow-[0_0_16px_rgba(59,130,246,0.3)] disabled:opacity-50 cursor-pointer transition-all"
      >
        {saving ? 'Saving...' : 'Save Changes'}
      </button>
      <button
        onclick={reset}
        disabled={saving}
        class="px-5 py-2 glass text-text rounded-lg text-sm font-medium hover:bg-white/[0.03] disabled:opacity-50 cursor-pointer transition-colors"
      >
        Reset
      </button>
    </div>

    <p class="text-xs text-text-dim">
      Source: <code class="font-data">manager-config.json</code>. Projects are managed separately on the Projects page.
    </p>
  {/if}
</div>
