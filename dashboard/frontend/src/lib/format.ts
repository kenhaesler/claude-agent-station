// ============================================
// Formatting Utilities
// ============================================

/** Relative time string from a date string or null */
export function timeAgo(dateStr: string | null): string {
  if (!dateStr) return 'never';
  const date = new Date(dateStr);
  const now = Date.now();
  const diff = now - date.getTime();

  if (diff < 0) return 'just now';

  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return date.toLocaleDateString();
}

/** Format token counts as "1.2M", "45K", etc. */
export function formatTokens(n: number | null): string {
  if (n == null || n === 0) return '-';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return `${n}`;
}

/** Format milliseconds as "4m 32s", "2h 15m", etc. */
export function formatDuration(ms: number | null): string {
  if (ms == null) return '-';
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainSec = seconds % 60;
  if (minutes < 60) return `${minutes}m ${remainSec}s`;
  const hours = Math.floor(minutes / 60);
  const remainMin = minutes % 60;
  return `${hours}h ${remainMin}m`;
}

/** Format megabytes as "1.2 GB", "450 MB", etc. Matches backend memory_mb fields. */
export function formatBytes(mb: number | null): string {
  if (mb == null) return '-';
  if (mb >= 1_024) return `${(mb / 1_024).toFixed(1)} GB`;
  return `${Math.round(mb)} MB`;
}

/** "owner/name" -> "name" */
export function shortRepo(repo: string): string {
  const parts = repo.split('/');
  return parts.length > 1 ? parts[parts.length - 1] : repo;
}

/** Truncate run ID to 8 characters */
export function shortRunId(id: string | null): string {
  if (!id) return '-';
  return id.slice(0, 8);
}

/** Format a number as a percentage string */
export function formatPercent(n: number | null): string {
  if (n == null) return '-';
  return `${Math.round(n)}%`;
}

/** Format a date string to locale string */
export function formatDate(dateStr: string | null): string {
  if (!dateStr) return '-';
  return new Date(dateStr).toLocaleString();
}

/** @deprecated Use formatTokens instead. Kept for historical data display. */
export function formatCost(usd: number | null): string {
  if (usd == null) return '-';
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

export interface RunModeDescriptor {
  label: string;
  icon: string;
  accent: 'default' | 'violet';
}

/** Map a Run.mode value to its UI descriptor. */
export function formatRunMode(mode: string | null | undefined): RunModeDescriptor {
  switch (mode) {
    case 'vision-bootstrap':
      return { label: 'Vision bootstrap', icon: '✨', accent: 'violet' };
    case 'agent-teams':
      return { label: 'Agent Teams', icon: '◆', accent: 'default' };
    default:
      return { label: 'Run', icon: '◆', accent: 'default' };
  }
}

/** Human-readable hint for a Run.skip_reason value. Returns the raw value
 *  if no canned text matches. */
export function formatSkipReason(reason: string | null | undefined): string {
  if (!reason) return '';
  switch (reason) {
    case 'no-eligible-issues-no-vision':
      return 'No vision yet — define one in the Vision tab.';
    case 'no-eligible-issues-bootstrap-dispatched':
      return 'Vision analyst dispatched.';
    case 'no-eligible-issues-bootstrap-already-running':
      return 'Vision analyst is already running.';
    case 'no-eligible-issues-proposals-pending':
      return 'Vision-suggested issues await your acceptance.';
    default:
      return reason;
  }
}
