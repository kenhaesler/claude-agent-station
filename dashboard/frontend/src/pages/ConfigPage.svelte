<script lang="ts">
  import { getConfig, updateConfig } from '../lib/api';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';
  import GlassCard from '../components/GlassCard.svelte';
  import type { StationConfig } from '../lib/types';

  let loading = $state(true);
  let saving = $state(false);

  // Form state
  let employeeModel = $state('');
  let managerModel = $state('');

  let maxEmployeeTurns = $state<number | undefined>(undefined);
  let maxEmployeeBudget = $state<number | undefined>(undefined);
  let maxManagerTurns = $state<number | undefined>(undefined);
  let maxManagerBudget = $state<number | undefined>(undefined);
  let sessionLimit24h = $state<number | undefined>(undefined);
  let maxSessionPercent = $state<number | undefined>(undefined);

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

    maxEmployeeTurns = cfg.limits?.max_employee_turns;
    maxEmployeeBudget = cfg.limits?.max_employee_budget_usd;
    maxManagerTurns = cfg.limits?.max_manager_turns;
    maxManagerBudget = cfg.limits?.max_manager_budget_usd;
    sessionLimit24h = cfg.limits?.session_limit_24h;
    maxSessionPercent = cfg.limits?.max_session_percent;

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
        max_employee_turns: maxEmployeeTurns,
        max_employee_budget_usd: maxEmployeeBudget,
        max_manager_turns: maxManagerTurns,
        max_manager_budget_usd: maxManagerBudget,
        session_limit_24h: sessionLimit24h,
        max_session_percent: maxSessionPercent,
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

    <!-- Limits -->
    <GlassCard class="p-5">
      <h3 class="font-semibold mb-4">Limits</h3>
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <div>
          <label for="max-employee-turns" class="block text-sm text-text-dim mb-1">Max Employee Turns</label>
          <input id="max-employee-turns" type="number" bind:value={maxEmployeeTurns} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-blue/50 transition-colors" />
        </div>
        <div>
          <label for="max-employee-budget" class="block text-sm text-text-dim mb-1">Max Employee Budget (USD)</label>
          <input id="max-employee-budget" type="number" step="0.01" bind:value={maxEmployeeBudget} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-blue/50 transition-colors" />
        </div>
        <div>
          <label for="max-manager-turns" class="block text-sm text-text-dim mb-1">Max Manager Turns</label>
          <input id="max-manager-turns" type="number" bind:value={maxManagerTurns} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-blue/50 transition-colors" />
        </div>
        <div>
          <label for="max-manager-budget" class="block text-sm text-text-dim mb-1">Max Manager Budget (USD)</label>
          <input id="max-manager-budget" type="number" step="0.01" bind:value={maxManagerBudget} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-blue/50 transition-colors" />
        </div>
        <div>
          <label for="session-limit" class="block text-sm text-text-dim mb-1">Session Limit (24h)</label>
          <input id="session-limit" type="number" bind:value={sessionLimit24h} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-blue/50 transition-colors" />
        </div>
        <div>
          <label for="max-session-pct" class="block text-sm text-text-dim mb-1">Max Session Percent</label>
          <input id="max-session-pct" type="number" bind:value={maxSessionPercent} class="w-full bg-white/[0.04] border border-border/50 rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-accent-blue/50 transition-colors" />
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

    <!-- Notifications -->
    <GlassCard class="p-5">
      <h3 class="font-semibold mb-4">Notifications</h3>
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
    </GlassCard>

    <!-- Logging -->
    <GlassCard class="p-5">
      <h3 class="font-semibold mb-4">Logging</h3>
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
