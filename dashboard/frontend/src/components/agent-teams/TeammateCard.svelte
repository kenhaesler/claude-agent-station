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

  let isActive = $derived(statusType === 'working');
  let isReviewing = $derived(statusType === 'reviewing');
  let isBlocked = $derived(statusType === 'blocked');
  let isIdle = $derived(statusType === 'idle');
  let statusColor = $derived(
    isActive ? '#2E7D32' :
    isReviewing ? '#B06030' :
    isBlocked ? '#D06050' :
    '#8C7A66'
  );
</script>

<div
  class="tm-card"
  class:active={isActive}
  class:plan-review={isReviewing}
  class:blocked={isBlocked}
  class:idle={isIdle}
>
  <!-- Header -->
  <div style="display: flex; align-items: center; gap: 12px;">
    <div class="tm-avatar">
      {#if isActive || isReviewing}
        <div class="status-ring" style="border-color: {isActive ? 'rgba(46,125,50,0.30)' : 'rgba(176,96,48,0.30)'}; animation: ring-pulse 3s ease-in-out infinite;"></div>
      {/if}
      <span style="font-size: 15px;">&#9679;</span>
    </div>
    <div>
      <div style="font-size: 16px; font-weight: 700; color: #3D2A1A;">{name}</div>
      <div style="font-size: 12px; color: #8C7A66;">Teammate · {model}</div>
    </div>
  </div>

  <!-- Task -->
  {#if task}
    <div style="font-size: 15px; color: #3D2A1A; margin-top: 12px; font-weight: 600;">{task}</div>
  {/if}

  <!-- Status -->
  {#if status}
    <div style="font-size: 14px; margin-top: 6px; font-weight: 500; color: {statusColor};">{status}</div>
  {/if}

  <!-- Detail -->
  {#if detail}
    <div style="font-size: 13px; color: #8C7A66; margin-top: 10px;">{detail}</div>
  {/if}

  <!-- Latest Message -->
  {#if latestMessage}
    <MessageBubble
      sender="{latestMessage.type === 'peer' ? '\u2192' : '\u2190'} {latestMessage.sender}"
      message={latestMessage.message}
      time={latestMessage.time}
      type={latestMessage.type}
    />
  {/if}

  <!-- Connection Chips -->
  {#if connections.length > 0}
    <div style="display: flex; gap: 6px; margin-top: auto; padding-top: 12px; flex-wrap: wrap;">
      {#each connections as conn}
        <MessageChip direction={conn.direction} target={conn.target} type={conn.type} />
      {/each}
    </div>
  {/if}
</div>

<style>
  .tm-card {
    padding: 18px 20px; border-radius: 16px;
    background: rgba(255,251,247,0.50); backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
    border: 1px solid rgba(240,220,200,0.25);
    box-shadow: 2px 2px 6px rgba(0,0,0,0.03), -2px -2px 6px rgba(255,255,255,0.35);
    transition: transform 0.2s, box-shadow 0.2s;
    position: relative; overflow: hidden;
    display: flex; flex-direction: column;
  }
  .tm-card:hover {
    transform: translateY(-2px);
    box-shadow: 3px 3px 10px rgba(0,0,0,0.05), -3px -3px 10px rgba(255,255,255,0.45);
  }
  .tm-card.plan-review { border-color: rgba(176,96,48,0.25); }
  .tm-card.blocked { border-color: rgba(208,96,80,0.35); }
  .tm-card.idle { opacity: 0.55; }

  .tm-card::before {
    content: ''; position: absolute; left: 0; top: 14px; bottom: 14px; width: 3px;
    border-radius: 0 3px 3px 0; opacity: 0; transition: opacity 0.3s;
  }
  .tm-card.active::before { background: rgba(46,125,50,0.4); opacity: 1; }
  .tm-card.plan-review::before { background: rgba(176,96,48,0.4); opacity: 1; }
  .tm-card.blocked::before { background: rgba(208,96,80,0.55); opacity: 1; }

  .tm-card.active { animation: card-breathe 4s ease-in-out infinite; }
  .tm-card.plan-review { animation: card-breathe-amber 4s ease-in-out infinite; }

  .tm-avatar {
    width: 38px; height: 38px; border-radius: 11px; flex-shrink: 0;
    background: rgba(255,251,247,0.80);
    border: 1.5px solid rgba(240,220,200,0.35);
    display: flex; align-items: center; justify-content: center;
    position: relative;
    box-shadow: 2px 2px 5px rgba(0,0,0,0.04), -2px -2px 5px rgba(255,255,255,0.30);
  }

  .status-ring {
    position: absolute; inset: -3px; border-radius: 13px;
    border: 1.5px solid transparent;
  }

  @keyframes ring-pulse {
    0%, 100% { opacity: 0.6; transform: scale(1); }
    50% { opacity: 1; transform: scale(1.06); }
  }
</style>
