<script lang="ts">
  let {
    timestamp,
    live = false,
  }: {
    timestamp: string | null;
    live?: boolean;
  } = $props();

  let now = $state(Date.now());

  $effect(() => {
    if (!live) return;
    const id = setInterval(() => { now = Date.now(); }, 30_000);
    return () => clearInterval(id);
  });

  function relative(ts: string | null): string {
    if (!ts) return '--';
    const diff = now - new Date(ts).getTime();
    if (diff < 0) return 'just now';
    const sec = Math.floor(diff / 1000);
    if (sec < 60) return `${sec}s ago`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m ago`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}h ago`;
    const d = Math.floor(hr / 24);
    return `${d}d ago`;
  }

  let display = $derived(relative(timestamp));
</script>

<time
  class="text-tertiary text-xs whitespace-nowrap"
  datetime={timestamp ?? undefined}
  title={timestamp ?? undefined}
>{display}</time>
