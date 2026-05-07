import type { VisionSseEvent } from './types';

interface StreamArgs {
  url: string;
  headers: Record<string, string>;
  payload: { session_id: string | null; message: string };
  signal?: AbortSignal;
  fetchImpl?: typeof fetch;
}

export async function* streamVisionChat(args: StreamArgs): AsyncIterable<VisionSseEvent> {
  const fetchFn = args.fetchImpl ?? fetch;
  let resp: Response;
  try {
    resp = await fetchFn(args.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...args.headers },
      body: JSON.stringify(args.payload),
      signal: args.signal,
    });
  } catch (e: any) {
    yield { type: 'error', code: 'network', message: e?.message ?? 'network error' };
    return;
  }

  if (!resp.ok) {
    yield { type: 'error', code: `http_${resp.status}`, message: resp.statusText };
    return;
  }
  if (!resp.body) {
    yield { type: 'error', code: 'no_body', message: 'response has no body' };
    return;
  }

  const decoder = new TextDecoder();
  const reader = resp.body.getReader();
  let buf = '';

  // If caller passes a signal, cancel the reader when it fires
  const onAbort = () => { try { reader.cancel(); } catch {} };
  if (args.signal) {
    if (args.signal.aborted) {
      try { reader.releaseLock(); } catch {}
      return;
    }
    args.signal.addEventListener('abort', onAbort);
  }

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      // SSE separator is two newlines
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, idx);
        buf = buf.slice(idx + 2);

        let eventName = 'message';
        let dataStr = '';
        for (const line of block.split('\n')) {
          if (line.startsWith('event:')) eventName = line.slice(6).trim();
          else if (line.startsWith('data:')) dataStr += line.slice(5).trim();
        }
        if (!dataStr) continue;
        try {
          const data = JSON.parse(dataStr);
          yield { type: eventName, ...data } as VisionSseEvent;
        } catch {
          // Skip malformed
        }
      }
    }
  } catch (e: any) {
    if (e?.name !== 'AbortError') {
      yield { type: 'error', code: 'stream_read', message: e?.message ?? 'stream error' };
    }
  } finally {
    if (args.signal) args.signal.removeEventListener('abort', onAbort);
    try { reader.releaseLock(); } catch {}
  }
}
