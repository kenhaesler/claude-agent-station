<script lang="ts">
  import type { SystemStatus, AuthStatus } from '../lib/types';
  import { getSystemStatus, getAuthStatus, serviceAction, triggerRun, startOAuthLogin, submitOAuthCode } from '../lib/api';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';
  import ResourceMeter from '../components/ResourceMeter.svelte';
  import StatusOrb from '../components/StatusOrb.svelte';
  import GlassCard from '../components/GlassCard.svelte';

  let system = $state<SystemStatus | null>(null);
  let auth = $state<AuthStatus | null>(null);
  let loading = $state(true);

  // OAuth flow state
  type OAuthFlowState = 'idle' | 'waiting_for_code' | 'submitting' | 'done';
  let oauthFlow = $state<OAuthFlowState>('idle');
  let oauthState = $state('');
  let oauthCode = $state('');
  let oauthError = $state('');

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

  async function handleOAuthStart() {
    oauthError = '';
    try {
      const res = await startOAuthLogin();
      oauthState = res.state;
      oauthFlow = 'waiting_for_code';
      oauthCode = '';
      window.open(res.auth_url, '_blank');
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
        await load();
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

<div class="space-y-6 animate-fade-in-up">
  <h1 class="text-2xl font-bold">System</h1>

  {#if loading}
    <div class="flex justify-center py-12"><LoadingSpinner /></div>
  {:else}
    <!-- Service Controls -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <!-- Agent Service -->
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

      <!-- Timer -->
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
        <ResourceMeter
          label="Memory"
          value={system?.resources.memory_used_mb ?? null}
          max={system?.resources.memory_total_mb ?? 4096}
          unit="MB"
        />
        <ResourceMeter
          label="Disk Used"
          value={system?.resources.disk_used_gb ?? null}
          max={system?.resources.disk_total_gb ?? 100}
          unit="GB"
        />
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
        <button
          onclick={handleOAuthStart}
          class="px-3 py-1.5 text-sm bg-gradient-to-r from-accent-blue to-accent-emerald text-white rounded-lg hover:shadow-lg cursor-pointer transition-all"
        >
          {auth?.logged_in && !auth.expired ? 'Re-authenticate' : 'Login with Claude'}
        </button>
      {:else if oauthFlow === 'waiting_for_code'}
        <div class="space-y-2">
          <p class="text-sm text-text-dim">
            A new tab has opened. Authenticate on claude.ai, then click "Copy Code" and paste it below.
          </p>
          <div class="flex gap-2">
            <input
              type="text"
              bind:value={oauthCode}
              placeholder="Paste authorization code"
              class="flex-1 px-3 py-1.5 text-sm bg-white/[0.04] border border-border/50 rounded-lg focus:outline-none focus:border-accent-blue/50 transition-colors"
              onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter') handleOAuthSubmit(); }}
            />
            <button
              onclick={handleOAuthSubmit}
              disabled={!oauthCode.trim()}
              class="px-3 py-1.5 text-sm bg-gradient-to-r from-accent-blue to-accent-emerald text-white rounded-lg cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            >Submit</button>
            <button
              onclick={handleOAuthCancel}
              class="px-3 py-1.5 text-sm glass rounded-lg text-text-dim hover:text-text cursor-pointer transition-colors"
            >Cancel</button>
          </div>
        </div>
      {:else if oauthFlow === 'submitting'}
        <div class="flex items-center gap-2 text-sm text-text-dim">
          <LoadingSpinner />
          <span>Exchanging code for tokens...</span>
        </div>
      {:else if oauthFlow === 'done'}
        <p class="text-sm text-approve">Authentication successful!</p>
      {/if}

      {#if oauthError}
        <p class="text-xs text-reject">{oauthError}</p>
      {/if}
    </GlassCard>
  {/if}
</div>
