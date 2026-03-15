/**
 * Reactive intelligence cache — fetches insights once, auto-refreshes every 5 min.
 * Provides lookup helpers for contextual intelligence surfacing.
 */

import { getIntelligenceInsights, getBackpressure } from './api';
import type { IntelligenceInsights, BackpressureStatus, ModeSuccessRate, EscalationStat } from './types';

interface IntelligenceCacheState {
  insights: IntelligenceInsights | null;
  backpressure: BackpressureStatus | null;
  loading: boolean;
  lastFetched: number;
}

const REFRESH_INTERVAL = 5 * 60 * 1000; // 5 minutes

export const intelligenceCache = $state<IntelligenceCacheState>({
  insights: null,
  backpressure: null,
  loading: false,
  lastFetched: 0,
});

let refreshTimer: ReturnType<typeof setInterval> | null = null;

export async function refreshIntelligence() {
  if (intelligenceCache.loading) return;
  intelligenceCache.loading = true;
  try {
    const [insightsRes, bpRes] = await Promise.allSettled([
      getIntelligenceInsights(),
      getBackpressure(),
    ]);
    if (insightsRes.status === 'fulfilled') {
      intelligenceCache.insights = insightsRes.value;
    }
    if (bpRes.status === 'fulfilled') {
      intelligenceCache.backpressure = bpRes.value;
    }
    intelligenceCache.lastFetched = Date.now();
  } catch {
    // silent
  } finally {
    intelligenceCache.loading = false;
  }
}

export function startIntelligenceRefresh() {
  if (refreshTimer) return;
  setTimeout(refreshIntelligence, 0);
  refreshTimer = setInterval(refreshIntelligence, REFRESH_INTERVAL);
}

export function stopIntelligenceRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
}

// --- Lookup helpers ---

/** Get success rate for a given mode (e.g., 'full', 'analyze', 'triage') */
export function getSuccessRate(mode: string): ModeSuccessRate | null {
  if (!intelligenceCache.insights?.success_rates) return null;
  return intelligenceCache.insights.success_rates.find(
    s => s.mode.toLowerCase() === mode.toLowerCase()
  ) ?? null;
}

/** Get success rate for a given escalation rung */
export function getEscalationRate(rung: number): EscalationStat | null {
  if (!intelligenceCache.insights?.escalation_stats) return null;
  return intelligenceCache.insights.escalation_stats.find(
    s => s.rung === rung
  ) ?? null;
}

/** Get current backpressure status */
export function getBackpressureStatus(): BackpressureStatus | null {
  return intelligenceCache.backpressure;
}

/** Check if backpressure is elevated (YELLOW or RED) */
export function isBackpressureElevated(): boolean {
  const bp = intelligenceCache.backpressure;
  if (!bp) return false;
  return bp.level === 'YELLOW' || bp.level === 'RED';
}
