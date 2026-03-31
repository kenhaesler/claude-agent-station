<script lang="ts">
  let {
    teamName = '',
    teammateCount = 0,
    activity = '',
    tasksCompleted = 0,
    tasksTotal = 0,
    tokens = '0',
    duration = '0s',
  }: {
    teamName?: string;
    teammateCount?: number;
    activity?: string;
    tasksCompleted?: number;
    tasksTotal?: number;
    tokens?: string;
    duration?: string;
  } = $props();
</script>

<div
  style="display: flex; align-items: center; gap: 16px; padding: 18px 22px; border-radius: 18px;
    background: rgba(255,251,247,0.55); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
    border: 1px solid rgba(240,220,200,0.30);
    box-shadow: 3px 3px 8px rgba(0,0,0,0.04), -3px -3px 8px rgba(255,255,255,0.40);"
>
  <!-- Lead Avatar -->
  <div
    style="width: 48px; height: 48px; border-radius: 14px; flex-shrink: 0;
      background: linear-gradient(135deg, #4A3728, #5C4435);
      display: flex; align-items: center; justify-content: center;
      font-size: 20px; color: #FFF5EE;
      box-shadow: 0 0 16px rgba(224,144,96,0.15);
      animation: lead-breathe 4s ease-in-out infinite;"
  >&#9670;</div>

  <!-- Info -->
  <div style="flex: 1;">
    <div style="font-size: 18px; font-weight: 700; color: #3D2A1A;">Team Lead</div>
    <div style="font-size: 14px; color: #7A6652; margin-top: 3px;">
      Coordinating {teammateCount} teammate{teammateCount !== 1 ? 's' : ''} · Monitoring task progress
    </div>
    {#if activity}
      <div style="font-size: 14px; color: #2E7D32; font-weight: 600; margin-top: 3px;">{activity}</div>
    {/if}
  </div>

  <!-- Meta -->
  <div style="display: flex; gap: 16px; font-size: 14px; color: #8C7A66; flex-shrink: 0;">
    <span>Tasks <span style="font-weight: 700; color: #3D2A1A;">{tasksCompleted}/{tasksTotal}</span></span>
    <span>Tokens <span style="font-weight: 700; color: #3D2A1A;">{tokens}</span></span>
    <span style="font-weight: 700; color: #3D2A1A;">{duration}</span>
  </div>
</div>

<style>
  @keyframes lead-breathe {
    0%, 100% { box-shadow: 0 0 16px rgba(224,144,96,0.15); }
    50% { box-shadow: 0 0 24px rgba(224,144,96,0.25); }
  }
</style>
