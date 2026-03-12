<script lang="ts">
  import { timeAgo } from '../lib/format';

  interface Props { date: string | null; }
  let { date }: Props = $props();

  let display = $state(timeAgo(date));

  $effect(() => {
    display = timeAgo(date);
    const interval = setInterval(() => {
      display = timeAgo(date);
    }, 10_000);
    return () => clearInterval(interval);
  });
</script>

<span class="text-text-dim" title={date ?? ''}>
  {display}
</span>
