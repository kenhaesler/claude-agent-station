<script lang="ts">
  import TeammateCard from './TeammateCard.svelte';

  interface TeammateData {
    name: string;
    model?: string;
    task: string;
    status: string;
    statusType: 'working' | 'reviewing' | 'idle' | 'blocked';
    detail?: string;
    latestMessage?: { sender: string; message: string; time: string; type: 'peer' | 'lead' };
    connections?: { direction: 'in' | 'out' | 'both'; target: string; type: 'peer' | 'lead' }[];
  }

  let {
    teammates = [],
  }: {
    teammates?: TeammateData[];
  } = $props();
</script>

<div style="display: grid; grid-template-columns: repeat(3, 1fr); grid-template-rows: 1fr 1fr; gap: 14px; flex: 1;">
  {#each teammates as tm}
    <TeammateCard
      name={tm.name}
      model={tm.model}
      task={tm.task}
      status={tm.status}
      statusType={tm.statusType}
      detail={tm.detail}
      latestMessage={tm.latestMessage}
      connections={tm.connections}
    />
  {/each}

  <!-- Spawn placeholder -->
  {#if teammates.length < 6}
    <div
      style="padding: 18px 20px; border-radius: 16px;
        background: rgba(255,251,247,0.35); border: 1px dashed rgba(240,220,200,0.40);
        display: flex; flex-direction: column; justify-content: center; align-items: center;
        text-align: center; gap: 10px; cursor: pointer;
        transition: background 0.2s;"
      onmouseenter={(e) => e.currentTarget.style.background = 'rgba(255,251,247,0.50)'}
      onmouseleave={(e) => e.currentTarget.style.background = 'rgba(255,251,247,0.35)'}
    >
      <div style="font-size: 28px; opacity: 0.3;">+</div>
      <div style="font-size: 15px; color: #8C7A66; font-weight: 600;">Spawn teammate</div>
      <div style="font-size: 13px; color: #A08E7A;">Or let lead auto-scale</div>
    </div>
  {/if}
</div>
