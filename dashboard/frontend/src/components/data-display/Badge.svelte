<script lang="ts">
  let {
    text,
    variant = 'default',
  }: {
    text: string;
    variant?: 'default' | 'success' | 'error' | 'warning' | 'info' | 'pr';
  } = $props();

  const variantClasses: Record<string, string> = {
    default: 'bg-surface-2 text-secondary',
    success: 'bg-approve/15 text-approve',
    error: 'bg-reject/15 text-reject',
    warning: 'bg-warning/15 text-warning',
    info: 'bg-info/15 text-indigo',
    pr: 'bg-pr/15 text-pr',
  };
</script>

<span
  class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium leading-tight {variantClasses[variant] ?? variantClasses.default}"
>{text}</span>
