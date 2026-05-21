// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, fireEvent, waitFor, cleanup } from '@testing-library/svelte';
import VisionChat from './VisionChat.svelte';

vi.mock('../../lib/api', () => ({
  uploadVisionAttachment: vi.fn().mockResolvedValue({
    id: 'a1', filename: 'data.xlsx', mime_type: 'x', size_bytes: 12345,
  }),
  deleteVisionAttachment: vi.fn().mockResolvedValue(undefined),
  getVisionChatSession: vi.fn().mockRejectedValue(new Error('404')),
  cancelVisionChat: vi.fn(),
  commitVision: vi.fn(),
  getStoredApiKey: () => 'k',
  visionChatTurnUrl: (n: number) => `/api/projects/${n}/vision/chat`,
}));

vi.mock('../../lib/vision-sse', () => ({
  streamVisionChat: vi.fn(() => (async function*() { yield { type: 'done' }; })()),
}));

vi.mock('../../lib/toast.svelte', () => ({
  toastError: vi.fn(), toastSuccess: vi.fn(), addToast: vi.fn(),
}));

describe('VisionChat attachments', () => {
  afterEach(() => cleanup());
  it('uploads selected file and shows chip', async () => {
    const { getByTestId, findByText } = render(VisionChat, { props: { projectId: 1 } });
    const input = getByTestId('vision-chat-attach-input') as HTMLInputElement;

    const file = new File(['hi'], 'data.xlsx');
    await fireEvent.change(input, { target: { files: [file] } });

    await findByText(/data\.xlsx/);
  });

  it('removes a pending chip on × click', async () => {
    const { getByTestId, findByText, queryByText } = render(VisionChat, { props: { projectId: 1 } });
    const input = getByTestId('vision-chat-attach-input') as HTMLInputElement;
    await fireEvent.change(input, { target: { files: [new File(['hi'], 'data.xlsx')] } });
    await findByText(/data\.xlsx/);
    const remove = getByTestId('vision-chat-attachment-remove-a1');
    await fireEvent.click(remove);
    await waitFor(() => expect(queryByText(/data\.xlsx/)).toBeNull());
  });
});
