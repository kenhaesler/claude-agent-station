<script lang="ts">
  import { timeAgo } from '../lib/format';

  interface Props { date: string | null; }
  let { date }: Props = $props();
</script>

<span class="text-text-dim" title={date ?? ''}>
  {timeAgo(date)}
</span>
