<script lang="ts">
  let {
    code,
    language,
  }: {
    code: string;
    language?: string;
  } = $props();
</script>

<div class="rounded-lg overflow-hidden border border-[var(--color-pre-border)]">
  {#if language}
    <div class="px-3 py-1.5 text-[10px] text-text-muted bg-[var(--color-pre-bg)] border-b border-[var(--color-pre-border)] uppercase tracking-wider">
      {language}
    </div>
  {/if}
  <pre
    class="font-data text-sm leading-relaxed p-3 overflow-x-auto m-0"
    style="background: var(--color-pre-bg); color: var(--color-text-dim);"
  ><code>{code}</code></pre>
</div>
