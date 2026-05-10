// ============================================
// Toast Notification System — Svelte 5 Runes
// No external type dependencies
// ============================================

export interface ToastAction {
  /** Visible link text — e.g. "View run". */
  label: string;
  /** href the action navigates to. SPA links use the existing client router. */
  href: string;
}

export interface Toast {
  id: number;
  type: 'success' | 'error' | 'warning' | 'info';
  text: string;
  action?: ToastAction;
}

export interface AddToastOptions {
  duration?: number;
  action?: ToastAction;
}

let nextId = 0;
export let toasts = $state<Toast[]>([]);

export function addToast(
  type: Toast['type'],
  text: string,
  durationOrOptions: number | AddToastOptions = 4000,
): void {
  const id = nextId++;
  // Backwards-compatible signature: callers may pass a plain duration number,
  // OR an options object with { duration, action }. The action payload is
  // rendered as a "View run" link in the toast (issue #272).
  const opts: AddToastOptions =
    typeof durationOrOptions === 'number'
      ? { duration: durationOrOptions }
      : durationOrOptions;
  const duration = opts.duration ?? 4000;
  toasts.push({ id, type, text, action: opts.action });
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
