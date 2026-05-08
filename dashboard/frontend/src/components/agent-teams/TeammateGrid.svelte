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
</div>
