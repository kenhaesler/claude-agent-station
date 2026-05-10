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

<div class="lead-card">
  <!-- Lead avatar — square, ink-on-paper, no breathing -->
  <div class="lead-avatar" aria-hidden="true">◆</div>

  <div class="lead-info">
    <div class="lead-title">Team Lead</div>
    <div class="lead-sub">
      Coordinating {teammateCount} teammate{teammateCount !== 1 ? 's' : ''} · Monitoring task progress
    </div>
    {#if activity}
      <div class="lead-activity">{activity}</div>
    {/if}
  </div>

  <div class="lead-stats">
    <div class="stat">
      <span class="k">Tasks</span>
      <span class="v">{tasksCompleted}<span class="s">/{tasksTotal}</span></span>
    </div>
    <div class="stat">
      <span class="k">Tokens</span>
      <span class="v">{tokens}</span>
    </div>
    <div class="stat">
      <span class="k">Elapsed</span>
      <span class="v">{duration}</span>
    </div>
  </div>
</div>

<style>
  .lead-card {
    display: flex; align-items: center; gap: 16px;
    padding: 14px 18px;
    background: var(--paper-2);
    border: 1px solid var(--rule);
    border-left: 3px solid var(--ink);
    font-family: var(--pro-sans);
  }
  .lead-avatar {
    width: 40px; height: 40px;
    flex-shrink: 0;
    background: var(--ink); color: var(--paper);
    display: grid; place-items: center;
    font-size: 18px;
  }
  .lead-info { flex: 1; min-width: 0; }
  .lead-title {
    font-family: var(--pro-sans);
    font-size: 11px; font-weight: 700;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--ink);
  }
  .lead-sub {
    font-family: var(--pro-mono);
    font-size: 11px; color: var(--graphite);
    margin-top: 4px;
  }
  .lead-activity {
    font-family: var(--pro-mono);
    font-size: 12px; color: var(--go);
    font-weight: 500;
    margin-top: 4px;
  }
  .lead-stats {
    display: flex; gap: 0;
    border: 1px solid var(--rule);
    flex-shrink: 0;
  }
  .stat {
    padding: 6px 14px;
    border-right: 1px solid var(--rule);
    display: flex; flex-direction: column; gap: 2px;
    min-width: 80px;
  }
  .stat:last-child { border-right: none; }
  .stat .k {
    font-family: var(--pro-sans);
    font-size: 9px; font-weight: 700;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--graphite);
  }
  .stat .v {
    font-family: var(--pro-mono);
    font-size: 14px; font-weight: 600;
    color: var(--ink); line-height: 1;
    font-variant-numeric: tabular-nums;
  }
  .stat .v .s { color: var(--ash); font-size: 0.8em; }
</style>
