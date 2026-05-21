import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { uploadVisionAttachment, deleteVisionAttachment } from './api';

// node test environment has no localStorage — provide a minimal stub
const store: Record<string, string> = {};
const localStorageStub = {
  getItem: (k: string) => store[k] ?? null,
  setItem: (k: string, v: string) => { store[k] = v; },
  removeItem: (k: string) => { delete store[k]; },
  clear: () => { for (const k in store) delete store[k]; },
};

describe('vision attachment API', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
    vi.stubGlobal('localStorage', localStorageStub);
    localStorageStub.setItem('station-api-key', 'test-key');
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorageStub.clear();
  });

  it('uploadVisionAttachment posts multipart with auth header', async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ id: 'a1', filename: 'x.xlsx', mime_type: 'm', size_bytes: 5 }),
    });
    const file = new File(['hi'], 'x.xlsx');
    const out = await uploadVisionAttachment(7, file);
    expect(out.id).toBe('a1');
    const mockFetch = fetch as unknown as ReturnType<typeof vi.fn>;
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain('/api/projects/7/vision/chat/attachments');
    expect(init.method).toBe('POST');
    expect(init.headers.Authorization).toBe('Bearer test-key');
    expect(init.body).toBeInstanceOf(FormData);
  });

  it('deleteVisionAttachment sends DELETE', async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true, status: 204 });
    await deleteVisionAttachment(7, 'a1');
    const mockFetch = fetch as unknown as ReturnType<typeof vi.fn>;
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain('/api/projects/7/vision/chat/attachments/a1');
    expect(init.method).toBe('DELETE');
  });
});
