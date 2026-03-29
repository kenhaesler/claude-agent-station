<script lang="ts">
  let {
    value,
    format,
  }: {
    value: number;
    format?: (n: number) => string;
  } = $props();

  let displayed = $state(value);
  let animFrame = 0;

  $effect(() => {
    const target = value;
    const start = displayed;
    const diff = target - start;
    if (Math.abs(diff) < 0.001) {
      displayed = target;
      return;
    }
    const duration = 400;
    const t0 = performance.now();

    function tick(now: number) {
      const elapsed = now - t0;
      const progress = Math.min(elapsed / duration, 1);
      // ease-out cubic
      const eased = 1 - Math.pow(1 - progress, 3);
      displayed = start + diff * eased;
      if (progress < 1) {
        animFrame = requestAnimationFrame(tick);
      } else {
        displayed = target;
      }
    }

    cancelAnimationFrame(animFrame);
    animFrame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animFrame);
  });

  let text = $derived(format ? format(displayed) : Math.round(displayed).toString());
</script>

<span class="font-mono">{text}</span>
