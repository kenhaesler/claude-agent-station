<script lang="ts">
  import { untrack } from 'svelte';

  interface Props {
    value: number;
    format?: (n: number) => string;
    duration?: number;
  }

  let { value, format, duration = 600 }: Props = $props();

  let displayed = $state(0);
  let rafId: number | undefined;

  $effect(() => {
    const target = value;
    const start = untrack(() => displayed);
    const diff = target - start;
    if (Math.abs(diff) < 0.001) {
      displayed = target;
      return;
    }
    const startTime = performance.now();

    function tick(now: number) {
      const elapsed = now - startTime;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      displayed = start + diff * eased;
      if (progress < 1) {
        rafId = requestAnimationFrame(tick);
      } else {
        displayed = target;
      }
    }

    if (rafId) cancelAnimationFrame(rafId);
    rafId = requestAnimationFrame(tick);

    return () => { if (rafId) cancelAnimationFrame(rafId); };
  });

  let text = $derived(format ? format(displayed) : String(Math.round(displayed)));
</script>

<span class="data-readout">{text}</span>
