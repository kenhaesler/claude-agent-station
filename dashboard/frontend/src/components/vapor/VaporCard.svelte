<script lang="ts">
  import type { Snippet } from 'svelte';

  let {
    children,
    hover = true,
    stagger = 0,
    className = '',
  }: {
    children: Snippet;
    hover?: boolean;
    stagger?: number;
    className?: string;
  } = $props();
</script>

<div
  class="vapor-card {className}"
  class:vapor-card-hover={hover}
  style="animation-delay: {stagger}s;"
>
  {@render children()}
</div>

<style>
  .vapor-card {
    background: rgba(255,251,247,0.65);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border: 1px solid rgba(240,220,200,0.6);
    border-radius: 18px;
    padding: 24px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.04), 0 12px 32px rgba(0,0,0,0.07);
    animation: card-in 0.6s cubic-bezier(0.16, 1, 0.3, 1) both;
    transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1), box-shadow 0.3s ease;
    cursor: default;
  }
  .vapor-card-hover:hover {
    transform: translateY(-4px);
    box-shadow: 0 8px 20px rgba(0,0,0,0.06), 0 24px 48px rgba(0,0,0,0.12);
  }
</style>
