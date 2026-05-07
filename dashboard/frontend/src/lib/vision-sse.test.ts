import { describe, it, expect, vi } from 'vitest';
import { streamVisionChat } from './vision-sse';

describe('streamVisionChat', () => {
  it('parses event-stream into typed events', async () => {
    const sse =
      'event: assistant_text\ndata: {"delta":"hi"}\n\n' +
      'event: coverage_update\ndata: {"covered":["problem"],"remaining":[]}\n\n' +
      'event: done\ndata: {}\n\n';

    const fakeFetch = vi.fn(async () => ({
      ok: true,
      body: new ReadableStream({
        start(c) { c.enqueue(new TextEncoder().encode(sse)); c.close(); },
      }),
    }) as any);

    const events = [];
    for await (const e of streamVisionChat({
      url: 'http://test/chat',
      headers: {},
      payload: { session_id: null, message: 'hi' },
      fetchImpl: fakeFetch,
    })) {
      events.push(e);
    }

    expect(events.map(e => e.type)).toEqual([
      'assistant_text', 'coverage_update', 'done',
    ]);
    expect((events[0] as any).delta).toBe('hi');
  });

  it('yields error event on non-200 status', async () => {
    const fakeFetch = vi.fn(async () => ({
      ok: false, status: 500, body: null, statusText: 'fail',
    }) as any);

    const events = [];
    for await (const e of streamVisionChat({
      url: 'http://test/chat',
      headers: {},
      payload: { session_id: null, message: 'hi' },
      fetchImpl: fakeFetch,
    })) {
      events.push(e);
    }
    expect(events).toEqual([{ type: 'error', code: 'http_500', message: 'fail' }]);
  });

  it('aborts via AbortController and ends gracefully', async () => {
    const ctrl = new AbortController();
    const fakeFetch = vi.fn(async (_input: URL | RequestInfo, _init?: RequestInit) => {
      // simulate hanging response
      return { ok: true, body: new ReadableStream({ start() {} }) } as unknown as Response;
    });
    const it = streamVisionChat({
      url: 'http://test/chat',
      headers: {},
      payload: { session_id: null, message: 'hi' },
      fetchImpl: fakeFetch,
      signal: ctrl.signal,
    });
    setTimeout(() => ctrl.abort(), 5);
    const events: any[] = [];
    try {
      for await (const e of it) events.push(e);
    } catch (e) { /* aborted */ }
    expect(events.length).toBeLessThanOrEqual(1);
  });
});
