<script lang="ts">
  import { themeStore } from '../../lib/theme.svelte';

  let { phase = 'idle' }: { phase?: string } = $props();

  const phaseHues: Record<string, number> = {
    idle: 260,
    employee: 220,
    manager_review: 40,
    executing_verdict: 160,
    coordinating: 280,
  };

  let hue = $derived(phaseHues[phase] ?? 260);
  let isDark = $derived(themeStore.theme.scheme === 'dark');
  let intensity = $derived(isDark ? 1 : 0.35);
</script>

<div
  class="ambient-glow"
  style="--gh: {hue}; --gi: {intensity};"
>
  <div class="glow-blob glow-1"></div>
  <div class="glow-blob glow-2"></div>
  <div class="glow-blob glow-3"></div>
</div>

<style>
  .ambient-glow {
    position: absolute;
    inset: 0;
    overflow: hidden;
    pointer-events: none;
  }

  .glow-blob {
    position: absolute;
    border-radius: 50%;
    filter: blur(80px);
  }

  .glow-1 {
    width: 60%;
    height: 60%;
    top: -10%;
    left: -10%;
    background: radial-gradient(circle, hsl(var(--gh) 50% 30% / calc(0.08 * var(--gi))), transparent 70%);
    animation: drift1 40s ease-in-out infinite;
  }

  .glow-2 {
    width: 50%;
    height: 50%;
    bottom: -10%;
    right: -10%;
    background: radial-gradient(circle, hsl(calc(var(--gh) + 20) 45% 25% / calc(0.06 * var(--gi))), transparent 70%);
    animation: drift2 30s ease-in-out infinite;
  }

  .glow-3 {
    width: 45%;
    height: 45%;
    top: 30%;
    left: 40%;
    background: radial-gradient(circle, hsl(calc(var(--gh) - 20) 40% 20% / calc(0.05 * var(--gi))), transparent 70%);
    animation: drift3 20s ease-in-out infinite;
  }

  @keyframes drift1 {
    0%, 100% { transform: translate(0, 0) scale(1); opacity: 0.7; }
    33% { transform: translate(5%, 8%) scale(1.1); opacity: 1; }
    66% { transform: translate(-3%, 5%) scale(0.95); opacity: 0.5; }
  }

  @keyframes drift2 {
    0%, 100% { transform: translate(0, 0) scale(1); opacity: 0.7; }
    50% { transform: translate(-8%, -5%) scale(1.15); opacity: 1; }
  }

  @keyframes drift3 {
    0%, 100% { transform: translate(0, 0) scale(1); opacity: 0.5; }
    25% { transform: translate(6%, -4%) scale(1.05); opacity: 0.9; }
    75% { transform: translate(-4%, 6%) scale(0.9); opacity: 0.5; }
  }

  @media (prefers-reduced-motion: reduce) {
    .glow-blob { animation-play-state: paused; }
  }
</style>
