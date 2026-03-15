<script lang="ts">
  import type { AgentRole } from '../lib/agent-presence.svelte';

  interface Props {
    name: string;
    role?: AgentRole;
    color?: string;
    status?: 'active' | 'thinking' | 'idle' | 'error';
    size?: 'sm' | 'md' | 'lg';
    showName?: boolean;
  }

  let { name, role = 'employee', color = '#3b82f6', status = 'idle', size = 'md', showName = false }: Props = $props();

  let sizeClasses = $derived(
    size === 'sm' ? 'w-6 h-6' :
    size === 'lg' ? 'w-10 h-10' : 'w-8 h-8'
  );

  let textSize = $derived(
    size === 'sm' ? 'text-[10px]' :
    size === 'lg' ? 'text-sm' : 'text-xs'
  );

  let statusColor = $derived(
    status === 'active' ? '#22c55e' :
    status === 'thinking' ? '#f59e0b' :
    status === 'error' ? '#ef4444' : '#6b7280'
  );
</script>

<div class="flex items-center gap-1.5">
  <div class="relative {sizeClasses} rounded-full flex items-center justify-center shrink-0" style="background: {color}20; border: 1.5px solid {color}60">
    <svg class="{size === 'sm' ? 'w-3 h-3' : size === 'lg' ? 'w-5 h-5' : 'w-4 h-4'}" viewBox="0 0 16 16" fill="none" stroke={color} stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
      {#if role === 'manager'}
        <!-- Shield -->
        <path d="M8 2L3 4.5V7.5C3 10.5 5 13 8 14C11 13 13 10.5 13 7.5V4.5L8 2Z" />
      {:else if role === 'coordinator'}
        <!-- Network -->
        <circle cx="8" cy="4" r="2" /><circle cx="4" cy="12" r="2" /><circle cx="12" cy="12" r="2" />
        <line x1="8" y1="6" x2="4" y2="10" /><line x1="8" y1="6" x2="12" y2="10" />
      {:else if role === 'analyst'}
        <!-- Search -->
        <circle cx="7" cy="7" r="4" /><line x1="10" y1="10" x2="14" y2="14" stroke-width="2" />
      {:else if role === 'planner'}
        <!-- Compass / Map -->
        <circle cx="8" cy="8" r="6" /><polygon points="8,3 9.5,7 8,6 6.5,7" fill={color} /><polygon points="8,13 6.5,9 8,10 9.5,9" fill={color} opacity="0.5" />
      {:else if role === 'assigner'}
        <!-- Clipboard / Assign -->
        <rect x="4" y="3" width="8" height="11" rx="1" /><path d="M6 2h4v2H6z" fill={color} /><line x1="6" y1="7" x2="10" y2="7" /><line x1="6" y1="9.5" x2="10" y2="9.5" /><line x1="6" y1="12" x2="9" y2="12" />
      {:else}
        <!-- Wrench -->
        <path d="M11.5 2.5L9 5 11 7l2.5-2.5a4 4 0 01-5.5 5.5L4 14l-2-2 4-4A4 4 0 0111.5 2.5z" />
      {/if}
    </svg>

    <!-- Status dot -->
    <div
      class="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-bg"
      style="background: {statusColor}"
      class:animate-pulse={status === 'thinking'}
    ></div>
  </div>

  {#if showName}
    <span class="{textSize} font-medium" style="color: {color}">{name}</span>
  {/if}
</div>
