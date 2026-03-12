<script lang="ts">
  interface Props {
    messages: string[];
    speed?: number;
  }

  let { messages }: Props = $props();
</script>

<span class="text-xs text-text-dim">{messages[messages.length - 1] ?? ''}</span>
