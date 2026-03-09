<script lang="ts">
  interface Props { size?: string; }
  let { size = '24' }: Props = $props();
</script>

<svg
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  class="text-accent-blue"
  style="animation: spin-slow 2s linear infinite"
>
  <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="1" opacity="0.1" />
  <path
    d="M12 2a10 10 0 0 1 10 10"
    stroke="currentColor"
    stroke-width="2"
    stroke-linecap="round"
    opacity="0.8"
  />
  <circle cx="12" cy="2" r="1.5" fill="currentColor" opacity="0.6">
    <animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="2s" repeatCount="indefinite" />
  </circle>
</svg>
