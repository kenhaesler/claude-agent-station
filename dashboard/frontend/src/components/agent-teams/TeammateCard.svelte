<script lang="ts">
  import MessageChip from './MessageChip.svelte';
  import MessageBubble from './MessageBubble.svelte';

  let {
    name,
    model = 'claude-opus-4-7',
    task = '',
    status = '',
    statusType = 'idle',
    detail = '',
    latestMessage,
    connections = [],
  }: {
    name: string;
    model?: string;
    task?: string;
    status?: string;
    statusType?: 'working' | 'reviewing' | 'idle' | 'blocked';
    detail?: string;
    latestMessage?: { sender: string; message: string; time: string; type: 'peer' | 'lead' };
    connections?: { direction: 'in' | 'out' | 'both'; target: string; type: 'peer' | 'lead' }[];
  } = $props();

  let isActive    = $derived(statusType === 'working');
  let isReviewing = $derived(statusType === 'reviewing');
  let isBlocked   = $derived(statusType === 'blocked');
  let isIdle      = $derived(statusType === 'idle');
</script>

<div
  class="tm-card"
  class:active={isActive}
  class:reviewing={isReviewing}
  class:blocked={isBlocked}
  class:idle={isIdle}
>
  <!-- Header -->
  <div class="tm-head">
    <div class="tm-id">
      <span class="tm-dot" aria-hidden="true"></span>
      <div>
        <div class="tm-name">{name}</div>
        <div class="tm-model">Teammate · {model}</div>
      </div>
    </div>
  </div>

  {#if task}
    <div class="tm-task">{task}</div>
  {/if}

  {#if status}
    <div class="tm-status" class:active={isActive} class:reviewing={isReviewing} class:blocked={isBlocked}>
      {status}
    </div>
  {/if}

  {#if detail}
    <div class="tm-detail">{detail}</div>
  {/if}

  {#if latestMessage}
    <MessageBubble
      sender="{latestMessage.type === 'peer' ? '→' : '←'} {latestMessage.sender}"
      message={latestMessage.message}
      time={latestMessage.time}
      type={latestMessage.type}
    />
  {/if}

  {#if connections.length > 0}
    <div class="tm-connections">
      {#each connections as conn}
        <MessageChip direction={conn.direction} target={conn.target} type={conn.type} />
      {/each}
    </div>
  {/if}
</div>

<style>
  .tm-card {
    padding: 14px 16px;
    background: var(--paper-2);
    border: 1px solid var(--rule);
    border-left: 3px solid transparent;
    display: flex; flex-direction: column; gap: 8px;
    font-family: var(--pro-sans);
    transition: border-color 200ms ease;
  }
  .tm-card:hover { border-color: var(--rule-2); }
  .tm-card.active    { border-left-color: var(--go); }
  .tm-card.reviewing { border-left-color: var(--caution); }
  .tm-card.blocked   { border-left-color: var(--abort); }
  .tm-card.idle      { opacity: 0.65; }

  .tm-head {
    display: flex; align-items: center; gap: 12px;
    border-bottom: 1px solid var(--rule);
    padding-bottom: 8px;
  }
  .tm-id { display: flex; align-items: center; gap: 10px; }
  .tm-dot {
    width: 7px; height: 7px;
    background: var(--ash);
    flex-shrink: 0;
  }
  .tm-card.active    .tm-dot { background: var(--go); }
  .tm-card.reviewing .tm-dot { background: var(--caution); }
  .tm-card.blocked   .tm-dot { background: var(--abort); }

  .tm-name {
    font-family: var(--pro-sans);
    font-size: 14px; font-weight: 600;
    color: var(--ink);
  }
  .tm-model {
    font-family: var(--pro-mono);
    font-size: 11px; color: var(--graphite);
    margin-top: 1px;
  }

  .tm-task {
    font-family: var(--pro-sans);
    font-size: 13px; font-weight: 500;
    color: var(--ink); line-height: 1.35;
  }
  .tm-status {
    font-family: var(--pro-mono);
    font-size: 12px; color: var(--graphite);
  }
  .tm-status.active    { color: var(--go); }
  .tm-status.reviewing { color: var(--caution); }
  .tm-status.blocked   { color: var(--abort); }

  .tm-detail {
    font-family: var(--pro-mono);
    font-size: 11px; color: var(--ash);
  }
  .tm-connections {
    display: flex; gap: 4px; margin-top: auto; padding-top: 6px;
    flex-wrap: wrap;
  }
</style>
