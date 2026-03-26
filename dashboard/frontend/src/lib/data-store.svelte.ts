/**
 * Reactive data fetching layer with SSE-driven cache invalidation.
 * Replaces ad-hoc $effect + $state patterns in page components.
 */

import { subscribe as busSubscribe } from './event-bus.svelte';

// --- Query Cache ---

interface CacheEntry<T> {
  data: T;
  timestamp: number;
  stale: boolean;
}

const cache = new Map<string, CacheEntry<unknown>>();
const inflight = new Map<string, Promise<unknown>>();
const refreshCallbacks = new Map<string, Set<() => void>>();

const STALE_TIME = 30_000; // 30s before data considered stale

// --- SSE Invalidation ---

const invalidationMap: Record<string, string[]> = {
  run_start:           ['runs', 'active-employees', 'analytics', 'queue-stats'],
  run_complete:        ['runs', 'active-employees', 'analytics', 'queue-stats'],
  employee_start:      ['runs', 'active-employees'],
  employee_complete:   ['runs', 'active-employees'],
  verdict_execute:     ['runs', 'analytics'],
  coordinator_task_pending:   ['dag'],
  coordinator_task_ready:     ['dag'],
  coordinator_task_running:   ['dag'],
  coordinator_task_completed: ['dag'],
  coordinator_task_failed:    ['dag'],
  queue_pending:       ['queue', 'queue-stats'],
  queue_claimed:       ['queue', 'queue-stats'],
  queue_assigned:      ['queue', 'queue-stats'],
  queue_in_progress:   ['queue', 'queue-stats'],
  queue_review:        ['queue', 'queue-stats'],
  queue_approved:      ['queue', 'queue-stats'],
  queue_rejected:      ['queue', 'queue-stats'],
  queue_completed:     ['queue', 'queue-stats'],
  queue_failed:        ['queue', 'queue-stats'],
  intelligence_decision: ['intelligence'],
  guidance_sent:       ['coordinator-messages'],
};

let sseSubscribed = false;

function ensureSSESubscription() {
  if (sseSubscribed) return;
  sseSubscribed = true;
  busSubscribe('*', (event) => {
    const type = event.type ?? (event as any).event;
    if (!type) return;
    const keys = invalidationMap[type];
    if (keys) keys.forEach(invalidateQuery);
  });
}

/** Invalidate all queries matching a key prefix */
export function invalidateQuery(key: string) {
  for (const [k, entry] of cache) {
    if (k === key || k.startsWith(`${key}:`)) {
      entry.stale = true;
    }
  }
  // Trigger refresh on active queries
  for (const [k, callbacks] of refreshCallbacks) {
    if (k === key || k.startsWith(`${key}:`)) {
      callbacks.forEach(cb => cb());
    }
  }
}

// --- Query Factory ---

export interface QueryResult<T> {
  readonly data: T | null;
  readonly loading: boolean;
  readonly error: string | null;
  refresh: () => void;
}

/**
 * Create a reactive query. Call inside a component's <script> block.
 * Returns a reactive object with data/loading/error.
 *
 * @param key - Cache key (use ':' for parameterized keys, e.g. 'runs:123')
 * @param fetcher - Async function to fetch data
 * @param options - refreshInterval (ms), enabled (conditional)
 */
export function createQuery<T>(
  key: string,
  fetcher: () => Promise<T>,
  options?: {
    refreshInterval?: number;
    enabled?: boolean;
  }
): QueryResult<T> {
  ensureSSESubscription();

  const enabled = options?.enabled ?? true;

  let result = $state<{ data: T | null; loading: boolean; error: string | null }>({
    data: null,
    loading: enabled,
    error: null,
  });

  // Check cache on init
  const cached = cache.get(key) as CacheEntry<T> | undefined;
  if (cached && !cached.stale) {
    result.data = cached.data;
    result.loading = false;
  }

  async function doFetch() {
    if (!enabled) return;

    // Dedup inflight requests
    let promise = inflight.get(key) as Promise<T> | undefined;
    if (!promise) {
      promise = fetcher();
      inflight.set(key, promise);
    }

    try {
      result.loading = result.data === null; // Only show loading if no cached data
      const data = await promise;
      result.data = data;
      result.error = null;
      cache.set(key, { data, timestamp: Date.now(), stale: false });
    } catch (e: any) {
      result.error = e.message || 'Fetch failed';
    } finally {
      result.loading = false;
      inflight.delete(key);
    }
  }

  function refresh() {
    doFetch();
  }

  // Register for SSE invalidation callbacks
  if (!refreshCallbacks.has(key)) {
    refreshCallbacks.set(key, new Set());
  }
  refreshCallbacks.get(key)!.add(refresh);

  // Initial fetch
  if (enabled) doFetch();

  // Auto-refresh interval
  let intervalId: ReturnType<typeof setInterval> | undefined;
  if (options?.refreshInterval && enabled) {
    intervalId = setInterval(refresh, options.refreshInterval);
  }

  return {
    get data() { return result.data; },
    get loading() { return result.loading; },
    get error() { return result.error; },
    refresh,
  };
}

/** Clear all cached data */
export function clearQueryCache() {
  cache.clear();
  inflight.clear();
}
