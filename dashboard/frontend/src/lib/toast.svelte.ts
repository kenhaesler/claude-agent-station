import type { ToastMessage } from './types';

let nextId = 0;
export let toasts = $state<ToastMessage[]>([]);

export function addToast(type: ToastMessage['type'], text: string, duration = 4000) {
  const id = nextId++;
  toasts.push({ id, type, text });
  setTimeout(() => {
    const idx = toasts.findIndex(t => t.id === id);
    if (idx !== -1) toasts.splice(idx, 1);
  }, duration);
}

export function toast(text: string) { addToast('info', text); }
export function toastSuccess(text: string) { addToast('success', text); }
export function toastError(text: string) { addToast('error', text, 6000); }
