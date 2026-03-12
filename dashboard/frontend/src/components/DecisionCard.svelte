<script lang="ts">
  import type { Plan } from '../lib/types';
  import { approvePlan, rejectPlan, implementPlan } from '../lib/api';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import StatusBadge from './StatusBadge.svelte';
  import MarkdownRenderer from './MarkdownRenderer.svelte';

  interface Props {
    plan: Plan;
    projectRepo?: string;
    onAction?: () => void;
  }

  let { plan, projectRepo, onAction }: Props = $props();

  let acting = $state(false);
  let expanded = $state(false);

  async function handleApprove() {
    acting = true;
    try {
      await approvePlan(plan.id);
      toastSuccess(`Plan "${plan.title}" approved`);
      onAction?.();
    } catch (e: any) {
      toastError(`Failed: ${e.message}`);
    }
    acting = false;
  }

  async function handleReject() {
    acting = true;
    try {
      await rejectPlan(plan.id);
      toastSuccess(`Plan "${plan.title}" rejected`);
      onAction?.();
    } catch (e: any) {
      toastError(`Failed: ${e.message}`);
    }
    acting = false;
  }

  async function handleImplement() {
    acting = true;
    try {
      await implementPlan(plan.id);
      toastSuccess(`Plan "${plan.title}" queued for implementation`);
      onAction?.();
    } catch (e: any) {
      toastError(`Failed: ${e.message}`);
    }
    acting = false;
  }

  let scopeColor = $derived(
    plan.estimated_scope === 'large' ? 'text-reject' :
    plan.estimated_scope === 'medium' ? 'text-warning' : 'text-approve'
  );

  let filesCount = $derived(() => {
    try {
      const files = JSON.parse(plan.files_affected ?? '[]');
      return Array.isArray(files) ? files.length : 0;
    } catch {
      return 0;
    }
  });
</script>

<div class="border border-border rounded-lg bg-surface overflow-hidden {plan.status === 'draft' ? 'border-l-2 border-l-warning' : ''}">
  <div class="p-3 space-y-2">
    <!-- Header -->
    <div class="flex items-start justify-between gap-2">
      <div class="min-w-0">
        <div class="flex items-center gap-2">
          <StatusBadge value={plan.status} />
          <h3 class="text-sm font-semibold text-text truncate">{plan.title}</h3>
        </div>
        <div class="flex items-center gap-2 mt-1 text-[10px] text-text-muted font-data">
          {#if plan.estimated_scope}
            <span class={scopeColor}>Scope: {plan.estimated_scope}</span>
          {/if}
          {#if filesCount() > 0}
            <span>{filesCount()} files</span>
          {/if}
          {#if plan.issue_number}
            <span>Issue #{plan.issue_number}</span>
          {/if}
          {#if projectRepo}
            <span>{projectRepo}</span>
          {/if}
        </div>
      </div>
    </div>

    <!-- Description -->
    {#if plan.description}
      <p class="text-xs text-text-dim leading-relaxed line-clamp-2">{plan.description}</p>
    {/if}

    <!-- Steps (collapsed by default) -->
    {#if plan.steps}
      <button
        onclick={() => expanded = !expanded}
        class="text-[10px] text-info hover:underline cursor-pointer"
      >
        {expanded ? 'Hide steps' : 'View steps'}
      </button>

      {#if expanded}
        <div class="text-xs text-text-dim leading-relaxed mt-1 pl-2 border-l-2 border-border-subtle">
          <MarkdownRenderer content={plan.steps} />
        </div>
      {/if}
    {/if}

    <!-- Actions -->
    {#if plan.status === 'draft'}
      <div class="flex items-center gap-2 pt-1">
        <button
          onclick={handleApprove}
          disabled={acting}
          class="px-3 py-1 text-xs font-medium rounded bg-approve/20 text-approve hover:bg-approve/30 cursor-pointer disabled:opacity-40 transition-colors"
        >
          Approve
        </button>
        <button
          onclick={handleReject}
          disabled={acting}
          class="px-3 py-1 text-xs font-medium rounded bg-reject/20 text-reject hover:bg-reject/30 cursor-pointer disabled:opacity-40 transition-colors"
        >
          Reject
        </button>
        <button
          onclick={handleImplement}
          disabled={acting}
          class="px-3 py-1 text-xs font-medium rounded bg-info/20 text-info hover:bg-info/30 cursor-pointer disabled:opacity-40 transition-colors"
        >
          Implement
        </button>
      </div>
    {/if}
  </div>
</div>
