<script lang="ts">
  import type { ConfidenceBucket } from '../lib/types';
  import GlassCard from './GlassCard.svelte';

  interface Props {
    data: ConfidenceBucket[];
  }

  let { data }: Props = $props();

  // Perfect calibration line: reported confidence === actual success rate
  // If actual > reported, the model is underconfident (good)
  // If actual < reported, the model is overconfident (bad)
</script>

<GlassCard class="p-3">
  <h4 class="text-xs font-medium text-text mb-2">Confidence Calibration</h4>
  <div class="space-y-1.5">
    {#each data as bucket}
      {@const gap = bucket.actual_success_rate - bucket.avg_reported_confidence}
      <div class="flex items-center gap-2 text-[11px]">
        <span class="text-text-dim w-[55px] text-right font-data">{bucket.bucket}</span>
        <div class="flex-1 flex items-center gap-1">
          <!-- Reported confidence bar (dim) -->
          <div class="relative w-full h-3 bg-white/[0.03] rounded overflow-hidden">
            <div
              class="absolute inset-y-0 left-0 bg-info/20 rounded"
              style="width: {bucket.avg_reported_confidence * 100}%"
            ></div>
            <!-- Actual success bar (bright, overlaid) -->
            <div
              class="absolute inset-y-0 left-0 rounded {gap >= 0 ? 'bg-approve/40' : 'bg-reject/40'}"
              style="width: {bucket.actual_success_rate * 100}%"
            ></div>
          </div>
        </div>
        <span class="font-data w-[32px] text-right {gap >= 0 ? 'text-approve' : 'text-reject'}">
          {(bucket.actual_success_rate * 100).toFixed(0)}%
        </span>
        <span class="text-text-muted font-data w-[20px] text-right">{bucket.total}</span>
      </div>
    {/each}
  </div>
  <div class="flex justify-between text-[9px] text-text-muted mt-1.5">
    <span>Reported confidence</span>
    <span>Actual success | n</span>
  </div>
</GlassCard>
