<script lang="ts">
  import type { SystemStatus, AuthStatus } from '../lib/types';
  import { getSystemStatus, getAuthStatus, serviceAction, triggerRun } from '../lib/api';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';
  import ResourceMeter from '../components/ResourceMeter.svelte';

  let system = $state<SystemStatus | null>(null);
  let auth = $state<AuthStatus | null>(null);
  let loading = $state(true);

  async function load() {
    try {
      const [sysRes, authRes] = await Promise.all([getSystemStatus(), getAuthStatus()]);
      system = sysRes;
      auth = authRes;
    } catch (e: any) {
      toastError(e.message);
    } finally {
      loading = false;
    }
  }

  async function doServiceAction(action: string, unit: string) {
    try {
      await serviceAction(action, unit);
      toastSuccess(`${action} ${unit}`);
      await load();
    } catch (e: any) {
      toastError(e.message);
    }
  }

  async function handleTrigger() {
    try {
      await triggerRun();
      toastSuccess('Run triggered');
      await load();
    } catch (e: any) {
      toastError(e.message);
    }
  }

  $effect(() => {
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  });

  function formatUptime(s: number | null | undefined): string {
    if (s == null) return '-';
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    return `${d}d ${h}h ${m}m`;
  }
</script>

<div class="space-y-6">
  <h1 class="text-2xl font-bold">System</h1>

  {#if loading}
    <div class="flex justify-center py-12"><LoadingSpinner /></div>
  {:else}
    <!-- Service Controls -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <!-- Agent Service -->
      <div class="bg-surface rounded-xl border border-border p-5 space-y-4">
        <div class="flex items-center justify-between">
          <h3 class="font-semibold">Agent Service</h3>
          <div class="flex items-center gap-2">
            <span class="w-2.5 h-2.5 rounded-full {system?.service.active ? 'bg-approve' : 'bg-reject'}"></span>
            <span class="text-sm">{system?.service.active ? 'Active' : 'Inactive'}</span>
          </div>
        </div>
        <div class="flex flex-wrap gap-2">
          <button onclick={handleTrigger} class="px-3 py-1.5 text-sm bg-pr text-white rounded-lg hover:bg-pr/80 cursor-pointer">Trigger Run</button>
          <button onclick={() => doServiceAction('stop', 'claude-agent.service')} class="px-3 py-1.5 text-sm bg-surface-2 rounded-lg text-text-dim hover:text-text cursor-pointer">Stop</button>
        </div>
      </div>

      <!-- Timer -->
      <div class="bg-surface rounded-xl border border-border p-5 space-y-4">
        <div class="flex items-center justify-between">
          <h3 class="font-semibold">Timer</h3>
          <div class="flex items-center gap-2">
            <span class="w-2.5 h-2.5 rounded-full {system?.timer.active ? 'bg-approve' : 'bg-reject'}"></span>
            <span class="text-sm">{system?.timer.active ? 'Active' : 'Inactive'}</span>
          </div>
        </div>
        {#if system?.timer.next_trigger}
          <p class="text-sm text-text-dim">Next: {system.timer.next_trigger}</p>
        {/if}
        <div class="flex flex-wrap gap-2">
          <button onclick={() => doServiceAction('start', 'claude-agent.timer')} class="px-3 py-1.5 text-sm bg-surface-2 rounded-lg text-text-dim hover:text-text cursor-pointer">Enable</button>
          <button onclick={() => doServiceAction('stop', 'claude-agent.timer')} class="px-3 py-1.5 text-sm bg-surface-2 rounded-lg text-text-dim hover:text-text cursor-pointer">Disable</button>
        </div>
      </div>
    </div>

    <!-- Resources -->
    <div class="bg-surface rounded-xl border border-border p-5 space-y-4">
      <h3 class="font-semibold">Resources</h3>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <ResourceMeter
          label="Memory"
          value={system?.resources.memory_mb ?? null}
          max={4096}
          unit="MB"
        />
        <ResourceMeter
          label="Disk Free"
          value={system?.resources.disk_free_gb ?? null}
          max={100}
          unit="GB"
          invert
        />
      </div>
      <div class="flex gap-6 text-sm text-text-dim">
        <span>Load: {system?.resources.load_avg?.map(v => v.toFixed(2)).join(', ') ?? '-'}</span>
        <span>Uptime: {formatUptime(system?.resources.uptime_seconds)}</span>
      </div>
    </div>

    <!-- Auth Status -->
    <div class="bg-surface rounded-xl border border-border p-5 space-y-3">
      <h3 class="font-semibold">Auth Status</h3>
      <div class="flex items-center gap-3">
        <span class="w-2.5 h-2.5 rounded-full {auth?.logged_in && !auth.expired ? 'bg-approve' : 'bg-reject'}"></span>
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
    </div>
  {/if}
</div>
