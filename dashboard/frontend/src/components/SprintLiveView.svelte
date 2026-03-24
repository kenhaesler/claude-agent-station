<script lang="ts">
  import { onMount } from 'svelte';
  import { getSprintStatus } from '../lib/api';
  import type { SprintStatus, SprintRoleStatus } from '../lib/types';
  import GlassCard from './GlassCard.svelte';

  interface Props {
    projectRepo?: string;
  }

  let { projectRepo }: Props = $props();

  let sprint = $state<SprintStatus | null>(null);
  let error = $state<string | null>(null);
  let pollTimer = $state<ReturnType<typeof setInterval> | null>(null);

  async function fetchSprint() {
    if (!projectRepo) return;
    try {
      sprint = await getSprintStatus(projectRepo);
      error = null;
    } catch {
      // Sprint API not available or no sprint data
      error = 'unavailable';
    }
  }

  $effect(() => {
    // Re-fetch when projectRepo changes
    if (projectRepo) {
      fetchSprint();
    }
  });

  onMount(() => {
    fetchSprint();
    const interval = setInterval(fetchSprint, 10000);
    pollTimer = interval;
    return () => clearInterval(interval);
  });

  // Derived computations
  let roles = $derived(sprint?.roles ?? []);
  let completedRoles = $derived(roles.filter(r => r.status === 'complete').length);
  let totalRoles = $derived(roles.length);
  let progressPercent = $derived(totalRoles > 0 ? Math.round((completedRoles / totalRoles) * 100) : 0);

  let isActive = $derived(
    sprint !== null &&
    sprint.sprint_id !== null &&
    sprint.phase !== 'idle' &&
    sprint.phase !== 'complete'
  );

  let isComplete = $derived(sprint?.phase === 'complete');

  function getStatusColor(status: SprintRoleStatus['status']): string {
    switch (status) {
      case 'waiting': return 'bg-white/20';
      case 'active': return 'bg-blue-400 animate-pulse';
      case 'complete': return 'bg-green-400';
      case 'failed': return 'bg-red-400';
      default: return 'bg-white/20';
    }
  }

  function getStatusTextColor(status: SprintRoleStatus['status']): string {
    switch (status) {
      case 'waiting': return 'text-white/40';
      case 'active': return 'text-blue-400';
      case 'complete': return 'text-green-400';
      case 'failed': return 'text-red-400';
      default: return 'text-white/40';
    }
  }

  function formatDuration(ms: number | null): string {
    if (!ms) return '';
    const seconds = Math.round(ms / 1000);
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = seconds % 60;
    return `${minutes}m ${remainingSeconds}s`;
  }

  function formatTimeAgo(dateStr: string | null): string {
    if (!dateStr) return '';
    const diff = Date.now() - new Date(dateStr).getTime();
    const minutes = Math.floor(diff / 60000);
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
  }

  function getRoleLabel(role: string): string {
    return role.charAt(0).toUpperCase() + role.slice(1).replace(/_/g, ' ');
  }

  function getStatusLabel(role: SprintRoleStatus): string {
    switch (role.status) {
      case 'waiting': return 'waiting...';
      case 'active': {
        const turnInfo = role.turn && role.max_turns ? `turn ${role.turn}/${role.max_turns}` : 'analyzing...';
        return turnInfo;
      }
      case 'complete': {
        const parts: string[] = [];
        if (role.proposals_count > 0) parts.push(`${role.proposals_count} proposal${role.proposals_count !== 1 ? 's' : ''}`);
        if (role.reviews_count > 0) parts.push(`${role.reviews_count} review${role.reviews_count !== 1 ? 's' : ''}`);
        return parts.length > 0 ? parts.join(', ') : 'done';
      }
      case 'failed': return 'failed';
      default: return '';
    }
  }

  function getPhaseLabel(phase: SprintStatus['phase']): string {
    switch (phase) {
      case 'idle': return 'Idle';
      case 'analyzing': return 'Sprint in Progress';
      case 'implementing': return 'Implementing';
      case 'validating': return 'Validating';
      case 'complete': return 'Sprint Complete';
      default: return phase;
    }
  }
</script>

{#if sprint && sprint.sprint_id}
  <GlassCard glow={isActive ? 'blue' : isComplete ? 'emerald' : 'none'} class="p-4">
    <!-- Header -->
    <div class="flex items-center justify-between mb-3">
      <div class="flex items-center gap-2">
        <span class="w-2 h-2 rounded-full {isActive ? 'bg-blue-400 animate-pulse' : isComplete ? 'bg-green-400' : 'bg-white/20'}"></span>
        <h3 class="text-sm font-semibold text-text">{getPhaseLabel(sprint.phase)}</h3>
      </div>
      {#if sprint.started_at}
        <span class="text-[10px] text-text-muted font-data">{formatTimeAgo(sprint.started_at)}</span>
      {/if}
    </div>

    <!-- Role list -->
    <div class="space-y-1.5 mb-3">
      {#each roles as role}
        <div class="flex items-center gap-2 py-1">
          <span class="w-1.5 h-1.5 rounded-full shrink-0 {getStatusColor(role.status)}"></span>
          <span class="text-xs font-medium {getStatusTextColor(role.status)} w-24 truncate">
            {getRoleLabel(role.role)}
          </span>
          <span class="text-[10px] text-text-muted flex-1 truncate font-data">
            {getStatusLabel(role)}
          </span>
          {#if role.duration_ms && role.status === 'complete'}
            <span class="text-[10px] text-text-dim font-data shrink-0">
              ({formatDuration(role.duration_ms)})
            </span>
          {/if}
        </div>
      {/each}
    </div>

    <!-- Progress bar -->
    {#if totalRoles > 0}
      <div class="mb-2">
        <div class="w-full h-1 rounded-full bg-white/5 overflow-hidden">
          <div
            class="h-full rounded-full transition-all duration-500 {isComplete ? 'bg-green-400' : 'bg-blue-400'}"
            style="width: {progressPercent}%"
          ></div>
        </div>
      </div>
    {/if}

    <!-- Footer -->
    <div class="flex items-center justify-between">
      <span class="text-[10px] text-text-dim">
        {completedRoles}/{totalRoles} roles complete
      </span>
      {#if sprint.issues_created > 0}
        <span class="text-[10px] text-text-muted font-data">
          {sprint.issues_created} issue{sprint.issues_created !== 1 ? 's' : ''} created
        </span>
      {/if}
    </div>
  </GlassCard>
{:else if sprint && !sprint.sprint_id}
  <!-- No active sprint - show idle state -->
  <GlassCard class="p-4">
    <div class="flex items-center gap-2">
      <span class="w-2 h-2 rounded-full bg-white/20"></span>
      <span class="text-xs text-text-muted">No active sprint</span>
    </div>
  </GlassCard>
{/if}
