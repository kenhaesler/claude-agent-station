/**
 * Attention state management.
 *
 * Tracks escalation level based on pending decision count and time.
 * Levels:
 *   0 = ambient    (no visual indicator)
 *   1 = contextual (subtle badge / dot)
 *   2 = banner     (AttentionBanner shown)
 *   3 = notification (browser notification — future)
 */

export const attentionState = $state({
  /** Current escalation level (0-3). */
  level: 0 as 0 | 1 | 2 | 3,
  /** Whether the user dismissed the banner. */
  dismissed: false,
  /** Timestamp of the last dismissal. */
  lastDismissed: 0,
  /** Timestamp of the first time pending > 0 was detected. */
  pendingSince: 0,
});

const ESCALATION_DELAY_MS = 5 * 60 * 1000; // 5 minutes before escalating to banner
const RE_SHOW_DELAY_MS = 60 * 1000;         // 60 seconds before re-showing after dismiss

/**
 * Update attention level based on current pending decision count.
 * Call this whenever pendingDecisionCount changes (e.g., from polling).
 */
export function updateAttention(pendingCount: number): void {
  const now = Date.now();

  if (pendingCount <= 0) {
    // Nothing pending — reset to ambient
    attentionState.level = 0;
    attentionState.dismissed = false;
    attentionState.pendingSince = 0;
    return;
  }

  // Track when pending items first appeared
  if (attentionState.pendingSince === 0) {
    attentionState.pendingSince = now;
  }

  const pendingDuration = now - attentionState.pendingSince;

  // If dismissed recently, stay at contextual level
  if (attentionState.dismissed) {
    const sinceDismiss = now - attentionState.lastDismissed;
    if (sinceDismiss < RE_SHOW_DELAY_MS) {
      attentionState.level = 1;
      return;
    }
    // Re-show time elapsed — clear dismissed flag
    attentionState.dismissed = false;
  }

  // Escalate based on how long decisions have been pending
  if (pendingDuration >= ESCALATION_DELAY_MS) {
    attentionState.level = 2; // banner
  } else {
    attentionState.level = 1; // contextual
  }
}

/**
 * User dismissed the attention banner.
 */
export function dismissAttention(): void {
  attentionState.dismissed = true;
  attentionState.lastDismissed = Date.now();
  attentionState.level = 1; // drop to contextual
}
