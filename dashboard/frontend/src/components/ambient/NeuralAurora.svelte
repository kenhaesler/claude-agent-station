<script lang="ts">
  import { NeuralAuroraRenderer, parseCSSColor } from '../../lib/neural-aurora-renderer';
  import { themeStore } from '../../lib/theme.svelte';

  let { phase = 'idle' }: { phase?: string } = $props();

  let container: HTMLDivElement;
  let canvas: HTMLCanvasElement;
  let renderer: NeuralAuroraRenderer | null = null;

  // Create renderer, resize, and start — all in one effect to avoid race conditions
  $effect(() => {
    if (!canvas || !container) return;

    const r = new NeuralAuroraRenderer(canvas);
    renderer = r;

    // Initial size
    const rect = container.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      r.resize(rect.width, rect.height);
    }

    // Apply theme immediately
    const theme = themeStore.theme;
    const bg = parseCSSColor(theme.colors['--color-bg']);
    r.setThemeColors(bg, theme.scheme);

    // Reduced motion
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    r.setReducedMotion(mq.matches);
    const motionHandler = (e: MediaQueryListEvent) => r.setReducedMotion(e.matches);
    mq.addEventListener('change', motionHandler);

    // Start animation
    r.start();

    // Resize observer
    const ro = new ResizeObserver(([e]) => {
      r.resize(e.contentRect.width, e.contentRect.height);
    });
    ro.observe(container);

    return () => {
      ro.disconnect();
      mq.removeEventListener('change', motionHandler);
      r.destroy();
      renderer = null;
    };
  });

  // React to phase changes
  $effect(() => { renderer?.setPhase(phase); });

  // React to theme changes (after initial)
  $effect(() => {
    const theme = themeStore.theme;
    const bg = parseCSSColor(theme.colors['--color-bg']);
    renderer?.setThemeColors(bg, theme.scheme);
  });
</script>

<div bind:this={container} class="w-full h-full">
  <canvas bind:this={canvas} class="block w-full h-full"></canvas>
</div>
