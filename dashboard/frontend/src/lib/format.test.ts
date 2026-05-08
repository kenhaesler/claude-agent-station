import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  timeAgo,
  formatTokens,
  formatDuration,
  formatBytes,
  shortRepo,
  shortRunId,
  formatPercent,
  formatRunMode,
} from './format';

describe('formatTokens', () => {
  it('returns "-" for null and zero', () => {
    expect(formatTokens(null)).toBe('-');
    expect(formatTokens(0)).toBe('-');
  });

  it('formats sub-thousand values verbatim', () => {
    expect(formatTokens(42)).toBe('42');
    expect(formatTokens(999)).toBe('999');
  });

  it('formats thousands with K suffix and one decimal', () => {
    expect(formatTokens(1_000)).toBe('1.0K');
    expect(formatTokens(45_678)).toBe('45.7K');
  });

  it('formats millions with M suffix', () => {
    expect(formatTokens(1_200_000)).toBe('1.2M');
  });
});

describe('formatDuration', () => {
  it('returns "-" for null', () => {
    expect(formatDuration(null)).toBe('-');
  });

  it('shows seconds under a minute', () => {
    expect(formatDuration(30_000)).toBe('30s');
  });

  it('shows minutes and seconds under an hour', () => {
    expect(formatDuration(4 * 60_000 + 32_000)).toBe('4m 32s');
  });

  it('shows hours and minutes past an hour', () => {
    expect(formatDuration(2 * 3_600_000 + 15 * 60_000)).toBe('2h 15m');
  });
});

describe('formatBytes', () => {
  it('returns "-" for null', () => {
    expect(formatBytes(null)).toBe('-');
  });

  it('shows MB under 1024', () => {
    expect(formatBytes(450)).toBe('450 MB');
  });

  it('shows GB at 1024 and above', () => {
    expect(formatBytes(1_536)).toBe('1.5 GB');
  });
});

describe('shortRepo', () => {
  it('strips owner from owner/name', () => {
    expect(shortRepo('anthropic/claude-agent-station')).toBe('claude-agent-station');
  });

  it('returns input unchanged when no slash', () => {
    expect(shortRepo('lonelyrepo')).toBe('lonelyrepo');
  });
});

describe('shortRunId', () => {
  it('truncates to 8 chars', () => {
    expect(shortRunId('abcdef0123456789')).toBe('abcdef01');
  });

  it('returns "-" for null', () => {
    expect(shortRunId(null)).toBe('-');
  });
});

describe('formatPercent', () => {
  it('rounds and appends %', () => {
    expect(formatPercent(42.7)).toBe('43%');
  });

  it('returns "-" for null', () => {
    expect(formatPercent(null)).toBe('-');
  });
});

describe('timeAgo', () => {
  afterEach(() => vi.useRealTimers());

  it('returns "never" for null', () => {
    expect(timeAgo(null)).toBe('never');
  });

  it('formats seconds when under a minute', () => {
    vi.useFakeTimers();
    const now = new Date('2026-05-08T12:00:00Z');
    vi.setSystemTime(now);
    const past = new Date(now.getTime() - 30_000).toISOString();
    expect(timeAgo(past)).toBe('30s ago');
  });

  it('formats minutes when under an hour', () => {
    vi.useFakeTimers();
    const now = new Date('2026-05-08T12:00:00Z');
    vi.setSystemTime(now);
    const past = new Date(now.getTime() - 5 * 60_000).toISOString();
    expect(timeAgo(past)).toBe('5m ago');
  });

  it('returns "just now" for future timestamps', () => {
    vi.useFakeTimers();
    const now = new Date('2026-05-08T12:00:00Z');
    vi.setSystemTime(now);
    const future = new Date(now.getTime() + 60_000).toISOString();
    expect(timeAgo(future)).toBe('just now');
  });
});

import { formatRunMode, formatSkipReason } from './format';

describe('formatSkipReason', () => {
  it('maps the four canned reasons', () => {
    expect(formatSkipReason('no-eligible-issues-no-vision')).toContain('Vision tab');
    expect(formatSkipReason('no-eligible-issues-bootstrap-dispatched')).toContain('dispatched');
    expect(formatSkipReason('no-eligible-issues-bootstrap-already-running')).toContain('already running');
    expect(formatSkipReason('no-eligible-issues-proposals-pending')).toContain('await');
  });

  it('returns empty for null/undefined', () => {
    expect(formatSkipReason(null)).toBe('');
    expect(formatSkipReason(undefined)).toBe('');
  });

  it('falls back to raw value for unknown reasons', () => {
    expect(formatSkipReason('weird-thing')).toBe('weird-thing');
  });
});

describe('formatRunMode', () => {
  it('returns vision-bootstrap descriptor', () => {
    const m = formatRunMode('vision-bootstrap');
    expect(m.label).toBe('Vision bootstrap');
    expect(m.icon).toBe('✨');
    expect(m.accent).toBe('violet');
  });

  it('returns agent-teams descriptor', () => {
    const m = formatRunMode('agent-teams');
    expect(m.label).toBe('Agent Teams');
    expect(m.accent).toBe('default');
  });

  it('falls back for unknown modes', () => {
    const m = formatRunMode(null);
    expect(m.label).toBe('Run');
    expect(m.accent).toBe('default');
  });
});
