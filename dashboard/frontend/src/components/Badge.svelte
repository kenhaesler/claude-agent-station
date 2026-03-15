<script lang="ts">
  interface Props {
    variant?: 'default' | 'success' | 'error' | 'warning' | 'info' | 'purple' | 'muted';
    size?: 'sm' | 'md';
    class?: string;
    label: string;
    /** Optional count displayed after label */
    count?: number;
    /** Optional icon indicator (dot) */
    dot?: boolean;
  }

  let { variant = 'default', size = 'sm', class: className = '', label, count, dot = false }: Props = $props();

  const variantClasses: Record<string, string> = {
    default: 'bg-surface-2 text-text-dim',
    success: 'bg-approve/15 text-approve',
    error: 'bg-reject/15 text-reject',
    warning: 'bg-warning/15 text-warning',
    info: 'bg-info/15 text-info',
    purple: 'bg-accent-purple/15 text-accent-purple',
    muted: 'bg-surface-2 text-text-muted',
  };

  const sizeClasses: Record<string, string> = {
    sm: 'text-[10px] px-1.5 py-0.5',
    md: 'text-xs px-2 py-0.5',
  };

  const dotColors: Record<string, string> = {
    default: 'bg-text-dim',
    success: 'bg-approve',
    error: 'bg-reject',
    warning: 'bg-warning',
    info: 'bg-info',
    purple: 'bg-accent-purple',
    muted: 'bg-text-muted',
  };
</script>

<span class="inline-flex items-center gap-1 rounded-full font-medium {variantClasses[variant]} {sizeClasses[size]} {className}">
  {#if dot}
    <span class="w-1.5 h-1.5 rounded-full {dotColors[variant]}"></span>
  {/if}
  {label}
  {#if count != null}
    <span class="font-data">{count}</span>
  {/if}
</span>
