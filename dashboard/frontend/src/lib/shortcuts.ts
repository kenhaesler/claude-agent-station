/**
 * Centralized keyboard shortcut registry.
 * Manages global and scoped shortcuts with conflict detection.
 */

export interface Shortcut {
  key: string;
  /** Modifier keys required */
  meta?: boolean;
  ctrl?: boolean;
  shift?: boolean;
  alt?: boolean;
  /** Description shown in shortcut reference */
  description: string;
  /** Scope: 'global' always active, others active when scope matches */
  scope: string;
  /** Handler — return true to prevent default */
  handler: (e: KeyboardEvent) => boolean | void;
}

interface RegisteredShortcut extends Shortcut {
  id: string;
}

let shortcuts: RegisteredShortcut[] = [];
let nextId = 0;
let activeScopes: Set<string> = new Set(['global']);

/** Register a shortcut. Returns an unregister function. */
export function registerShortcut(shortcut: Shortcut): () => void {
  const id = `shortcut-${++nextId}`;
  shortcuts.push({ ...shortcut, id });
  return () => {
    shortcuts = shortcuts.filter(s => s.id !== id);
  };
}

/** Register multiple shortcuts at once. Returns an unregister-all function. */
export function registerShortcuts(defs: Shortcut[]): () => void {
  const unregisters = defs.map(registerShortcut);
  return () => unregisters.forEach(fn => fn());
}

/** Set active scopes (in addition to 'global' which is always active) */
export function setActiveScopes(...scopes: string[]) {
  activeScopes = new Set(['global', ...scopes]);
}

/** Add a scope */
export function pushScope(scope: string) {
  activeScopes.add(scope);
}

/** Remove a scope */
export function popScope(scope: string) {
  activeScopes.delete(scope);
}

/** Get all registered shortcuts (for reference dialog) */
export function getAllShortcuts(): Shortcut[] {
  return [...shortcuts];
}

/** Format shortcut key combo for display */
export function formatShortcut(s: Shortcut): string {
  const parts: string[] = [];
  if (s.meta || s.ctrl) parts.push(navigator.platform.includes('Mac') ? '⌘' : 'Ctrl');
  if (s.shift) parts.push('⇧');
  if (s.alt) parts.push(navigator.platform.includes('Mac') ? '⌥' : 'Alt');
  parts.push(s.key.length === 1 ? s.key.toUpperCase() : s.key);
  return parts.join('+');
}

function isInputFocused(): boolean {
  const el = document.activeElement;
  if (!el) return false;
  return el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement || (el as HTMLElement).isContentEditable;
}

function matchesShortcut(e: KeyboardEvent, s: RegisteredShortcut): boolean {
  // For single character keys, skip if input is focused (unless meta/ctrl held)
  if (s.key.length === 1 && !s.meta && !s.ctrl && !s.shift && !s.alt) {
    if (isInputFocused()) return false;
  }

  const metaOrCtrl = e.metaKey || e.ctrlKey;
  if (s.meta || s.ctrl) {
    if (!metaOrCtrl) return false;
  } else {
    if (metaOrCtrl) return false;
  }

  if (s.shift && !e.shiftKey) return false;
  if (!s.shift && e.shiftKey && s.key.length === 1) return false;
  if (s.alt && !e.altKey) return false;

  return e.key.toLowerCase() === s.key.toLowerCase();
}

/** Main keydown handler — wire to svelte:window */
export function handleShortcutKeydown(e: KeyboardEvent): boolean {
  for (const s of shortcuts) {
    if (!activeScopes.has(s.scope)) continue;
    if (matchesShortcut(e, s)) {
      const result = s.handler(e);
      if (result !== false) {
        e.preventDefault();
        return true;
      }
    }
  }
  return false;
}
