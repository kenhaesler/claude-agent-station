// ============================================
// Central Reactive Data Store — Svelte 5 Runes
// SSE-driven cache invalidation
// ============================================

import type {
  Run, ActiveEmployee, Project,
  QueueStats, BackpressureStatus,
  SystemStatus, AuthStatus,
  Notification,
} from './types';

import {
  listRuns, getActiveEmployees, listProjects,
  getQueueStats, getBackpressure,
  getSystemStatus, getAuthStatus,
} from './api';

import { subscribe as busSubscribe } from './event-bus.svelte';

// --- Loading state per domain ---

interface LoadingState {
  runs: boolean;
  activeEmployees: boolean;
  projects: boolean;
  queueStats: boolean;
  backpressure: boolean;
  systemStatus: boolean;
  authStatus: boolean;
}

// --- Store state ---

export let runs = $state<Run[]>([]);
export let runsTotal = $state<number>(0);
export let activeEmployees = $state<ActiveEmployee[]>([]);
export let projects = $state<Project[]>([]);
export let queueStats = $state<QueueStats | null>(null);
export let backpressure = $state<BackpressureStatus | null>(null);
export let systemStatus = $state<SystemStatus | null>(null);
export let authStatus = $state<AuthStatus | null>(null);
export let notifications = $state<Notification[]>([]);
export let sseConnected = $state<boolean>(false);

export let loading = $state<LoadingState>({
  runs: false,
  activeEmployees: false,
  projects: false,
  queueStats: false,
  backpressure: false,
  systemStatus: false,
  authStatus: false,
});

// --- Refresh functions ---

export async function refreshRuns(limit = 50, offset = 0): Promise<void> {
  loading.runs = true;
  try {
    const result = await listRuns({ limit, offset });
    runs = result.runs;
    runsTotal = result.total;
  } catch {
    // Error handled by API layer
  } finally {
    loading.runs = false;
  }
}

export async function refreshActiveEmployees(): Promise<void> {
  loading.activeEmployees = true;
  try {
    activeEmployees = await getActiveEmployees();
  } catch {
    // Error handled by API layer
  } finally {
    loading.activeEmployees = false;
  }
}

export async function refreshProjects(): Promise<void> {
  loading.projects = true;
  try {
    projects = await listProjects();
  } catch {
    // Error handled by API layer
  } finally {
    loading.projects = false;
  }
}

export async function refreshQueueStats(): Promise<void> {
  loading.queueStats = true;
  try {
    queueStats = await getQueueStats();
  } catch {
    // Error handled by API layer
  } finally {
    loading.queueStats = false;
  }
}

export async function refreshBackpressure(): Promise<void> {
  loading.backpressure = true;
  try {
    backpressure = await getBackpressure();
  } catch {
    // Error handled by API layer
  } finally {
    loading.backpressure = false;
  }
}

export async function refreshSystemStatus(): Promise<void> {
  loading.systemStatus = true;
  try {
    systemStatus = await getSystemStatus();
  } catch {
    // Error handled by API layer
  } finally {
    loading.systemStatus = false;
  }
}

export async function refreshAuthStatus(): Promise<void> {
  loading.authStatus = true;
  try {
    authStatus = await getAuthStatus();
  } catch {
    // Error handled by API layer
  } finally {
    loading.authStatus = false;
  }
}

/** Refresh all domains in parallel */
export async function refreshAll(): Promise<void> {
  await Promise.allSettled([
    refreshRuns(),
    refreshActiveEmployees(),
    refreshProjects(),
    refreshQueueStats(),
    refreshBackpressure(),
    refreshSystemStatus(),
    refreshAuthStatus(),
  ]);
}

// --- SSE event handler ---

/** Map SSE event types to store refresh actions */
const sseRefreshMap: Record<string, (() => Promise<void>)[]> = {
  run_start:           [refreshRuns, refreshActiveEmployees, refreshQueueStats],
  run_complete:        [refreshRuns, refreshActiveEmployees, refreshQueueStats],
  employee_start:      [refreshRuns, refreshActiveEmployees],
  employee_complete:   [refreshRuns, refreshActiveEmployees],
  verdict_execute:     [refreshRuns],
  queue_pending:       [refreshQueueStats],
  queue_claimed:       [refreshQueueStats],
  queue_assigned:      [refreshQueueStats],
  queue_in_progress:   [refreshQueueStats],
  queue_review:        [refreshQueueStats],
  queue_approved:      [refreshQueueStats],
  queue_rejected:      [refreshQueueStats],
  queue_completed:     [refreshQueueStats],
  queue_failed:        [refreshQueueStats],
};

// Debounce rapid SSE events
let pendingRefreshes = new Set<() => Promise<void>>();
let debounceTimer: ReturnType<typeof setTimeout> | null = null;

function flushPendingRefreshes(): void {
  const fns = [...pendingRefreshes];
  pendingRefreshes.clear();
  debounceTimer = null;
  for (const fn of fns) {
    fn();
  }
}

/**
 * Handle an SSE event and update the relevant store data.
 * Call this from your SSE connection handler.
 */
export function handleSSEEvent(event: Record<string, unknown>): void {
  const type = (event.type as string) ?? '';

  // Add notification events directly
  if (type === 'notification' && event.message) {
    notifications = [
      {
        id: Date.now(),
        run_id: (event.run_id as string) ?? null,
        type: (event.notification_type as Notification['type']) ?? 'info',
        message: event.message as string,
        read: false,
        created_at: new Date().toISOString(),
      },
      ...notifications,
    ].slice(0, 100); // Keep max 100
  }

  // Schedule store refreshes based on event type
  const refreshFns = sseRefreshMap[type];
  if (refreshFns) {
    for (const fn of refreshFns) {
      pendingRefreshes.add(fn);
    }
    if (!debounceTimer) {
      debounceTimer = setTimeout(flushPendingRefreshes, 500);
    }
  }
}

/** Update SSE connection state */
export function setSSEConnected(connected: boolean): void {
  sseConnected = connected;
}

// --- SSE event-bus integration ---

let busSubscribed = false;

/**
 * Subscribe to the event bus for SSE-driven updates.
 * Call once at app initialization.
 */
export function initStoreSSE(): void {
  if (busSubscribed) return;
  busSubscribed = true;
  busSubscribe('*', (event) => {
    handleSSEEvent(event as Record<string, unknown>);
  });
}

// --- Query Cache (for advanced consumers) ---

interface CacheEntry<T> {
  data: T;
  timestamp: number;
  stale: boolean;
}

const cache = new Map<string, CacheEntry<unknown>>();
const inflight = new Map<string, Promise<unknown>>();
const refreshCallbacks = new Map<string, Set<() => void>>();

/** Invalidate all queries matching a key prefix */
export function invalidateQuery(key: string): void {
  for (const [k, entry] of cache) {
    if (k === key || k.startsWith(`${key}:`)) {
      entry.stale = true;
    }
  }
  for (const [k, callbacks] of refreshCallbacks) {
    if (k === key || k.startsWith(`${key}:`)) {
      callbacks.forEach((cb) => cb());
    }
  }
}

export interface QueryResult<T> {
  readonly data: T | null;
  readonly loading: boolean;
  readonly error: string | null;
  refresh: () => void;
}

/**
 * Create a reactive query with caching and SSE invalidation.
 */
export function createQuery<T>(
  key: string,
  fetcher: () => Promise<T>,
  options?: { refreshInterval?: number; enabled?: boolean },
): QueryResult<T> {
  const enabled = options?.enabled ?? true;

  let result = $state<{ data: T | null; loading: boolean; error: string | null }>({
    data: null,
    loading: enabled,
    error: null,
  });

  const cached = cache.get(key) as CacheEntry<T> | undefined;
  if (cached && !cached.stale) {
    result.data = cached.data;
    result.loading = false;
  }

  async function doFetch(): Promise<void> {
    if (!enabled) return;

    let promise = inflight.get(key) as Promise<T> | undefined;
    if (!promise) {
      promise = fetcher();
      inflight.set(key, promise);
    }

    try {
      result.loading = result.data === null;
      const data = await promise;
      result.data = data;
      result.error = null;
      cache.set(key, { data, timestamp: Date.now(), stale: false });
    } catch (e: unknown) {
      result.error = e instanceof Error ? e.message : 'Fetch failed';
    } finally {
      result.loading = false;
      inflight.delete(key);
    }
  }

  function refresh(): void {
    doFetch();
  }

  if (!refreshCallbacks.has(key)) {
    refreshCallbacks.set(key, new Set());
  }
  refreshCallbacks.get(key)!.add(refresh);

  if (enabled) doFetch();

  if (options?.refreshInterval && enabled) {
    setInterval(refresh, options.refreshInterval);
  }

  return {
    get data() { return result.data; },
    get loading() { return result.loading; },
    get error() { return result.error; },
    refresh,
  };
}

/** Clear all cached data */
export function clearQueryCache(): void {
  cache.clear();
  inflight.clear();
}
