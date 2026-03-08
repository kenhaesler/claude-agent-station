<script lang="ts">
  interface Props { size?: string; }
  let { size = '24' }: Props = $props();
</script>

<svg
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  class="animate-spin text-text-dim"
>
  <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" opacity="0.2" />
  <path
    d="M12 2a10 10 0 0 1 10 10"
    stroke="currentColor"
    stroke-width="3"
    stroke-linecap="round"
  />
</svg>
