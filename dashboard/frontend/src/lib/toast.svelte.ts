// ============================================
// Toast Notification System — Svelte 5 Runes
// No external type dependencies
// ============================================

export interface Toast {
  id: number;
  type: 'success' | 'error' | 'warning' | 'info';
  text: string;
}

let nextId = 0;
export let toasts = $state<Toast[]>([]);

export function addToast(type: Toast['type'], text: string, duration = 4000): void {
  const id = nextId++;
  toasts.push({ id, type, text });
  setTimeout(() => removeToast(id), duration);
}

export function removeToast(id: number): void {
  const idx = toasts.findIndex((t) => t.id === id);
  if (idx !== -1) toasts.splice(idx, 1);
}

// Convenience helpers
export function toast(text: string): void {
  addToast('info', text);
}
export function toastSuccess(text: string): void {
  addToast('success', text);
}
export function toastError(text: string): void {
  addToast('error', text, 6000);
}
export function toastWarning(text: string): void {
  addToast('warning', text, 5000);
}
