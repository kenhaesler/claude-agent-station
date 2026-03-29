/**
 * EventBus — Central SSE event dispatcher.
 * One SSE connection, many subscribers. Extracted from agent-presence
 * so multiple stores/components can react to events independently.
 */

/** Generic SSE event shape for the event bus */
interface BusEvent {
  type: string;
  [key: string]: unknown;
}

type EventHandler = (event: BusEvent) => void;

interface Subscription {
  id: number;
  types: string[] | '*';
  handler: EventHandler;
}

let nextId = 0;
const subscribers: Subscription[] = [];

/** Subscribe to specific event types (or '*' for all). Returns unsubscribe fn. */
export function subscribe(types: string[] | '*', handler: EventHandler): () => void {
  const sub: Subscription = { id: ++nextId, types, handler };
  subscribers.push(sub);
  return () => {
    const idx = subscribers.findIndex(s => s.id === sub.id);
    if (idx !== -1) subscribers.splice(idx, 1);
  };
}

/** Dispatch an event to all matching subscribers */
export function dispatch(event: BusEvent): void {
  for (const sub of subscribers) {
    if (sub.types === '*' || sub.types.includes(event.type)) {
      try {
        sub.handler(event);
      } catch {
        // subscriber error — don't break dispatch chain
      }
    }
  }
}

/** Get active subscriber count (for monitoring) */
export function subscriberCount(): number {
  return subscribers.length;
}
