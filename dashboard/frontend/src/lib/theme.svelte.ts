/**
 * Theme store — reactive theme management with localStorage persistence.
 *
 * - Persists user choice to localStorage under 'station-theme'
 * - Falls back to prefers-color-scheme when no saved preference
 * - Applies theme by setting CSS custom properties on document.documentElement
 * - Provides the current theme's agent role color map for JS consumers
 */

import { themes, defaultThemeId, getThemeById, type Theme, type ThemeColors } from './themes';

const STORAGE_KEY = 'station-theme';

// ─── Detect preferred scheme ───────────────────────────────

function getSystemScheme(): 'dark' | 'light' {
  if (typeof window === 'undefined') return 'dark';
  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

function pickDefaultTheme(): string {
  const scheme = getSystemScheme();
  if (scheme === 'light') return 'light';
  return defaultThemeId;
}

function loadSavedThemeId(): string {
  if (typeof window === 'undefined') return defaultThemeId;
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved && themes.some(t => t.id === saved)) return saved;
  } catch {
    // localStorage may be blocked
  }
  return pickDefaultTheme();
}

// ─── State ─────────────────────────────────────────────────

let currentThemeId = $state(loadSavedThemeId());
let currentTheme = $derived(getThemeById(currentThemeId));

// ─── Apply theme to DOM ────────────────────────────────────

function applyTheme(theme: Theme) {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;

  // Set every CSS variable
  for (const [key, value] of Object.entries(theme.colors)) {
    root.style.setProperty(key, value);
  }

  // Set data-theme attribute (for CSS selectors like [data-theme="light"])
  root.setAttribute('data-theme', theme.id);

  // Set color-scheme for native form controls
  root.style.setProperty('color-scheme', theme.scheme);
}

// Apply on load immediately (before first paint via the $effect below)
if (typeof document !== 'undefined') {
  applyTheme(getThemeById(loadSavedThemeId()));
}

// Re-apply reactively whenever the theme changes.
// Use $effect.root() since this runs at module scope, outside any component.
const _cleanup = $effect.root(() => {
  $effect(() => {
    applyTheme(currentTheme);
  });
});

// Listen for system scheme changes
if (typeof window !== 'undefined') {
  window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
    // Only auto-switch if the user hasn't explicitly chosen a theme
    try {
      if (!localStorage.getItem(STORAGE_KEY)) {
        currentThemeId = pickDefaultTheme();
      }
    } catch {
      // ignore
    }
  });
}

// ─── Public API ────────────────────────────────────────────

export const themeStore = {
  get id() { return currentThemeId; },
  get theme() { return currentTheme; },
  get themes() { return themes; },

  setTheme(id: string) {
    const theme = getThemeById(id);
    currentThemeId = theme.id;
    try {
      localStorage.setItem(STORAGE_KEY, theme.id);
    } catch {
      // localStorage blocked
    }
  },

  /** Reset to system preference */
  resetToSystem() {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore
    }
    currentThemeId = pickDefaultTheme();
  },

  /**
   * Get the role color map for JS consumers (agent-presence, etc.)
   * Returns the current theme's agent colors keyed by role slug.
   */
  getRoleColors(): Record<string, string> {
    const c = currentTheme.colors;
    return {
      manager: c['--color-agent-manager'],
      'dev-0': c['--color-agent-dev-0'],
      'dev-1': c['--color-agent-dev-1'],
      'dev-2': c['--color-agent-dev-2'],
      coordinator: c['--color-agent-coordinator'],
      analyst: c['--color-agent-analyst'],
    };
  },

  /** Get a specific status color */
  getStatusColor(status: 'active' | 'inactive' | 'thinking' | 'error' | 'idle'): string {
    return currentTheme.colors[`--color-status-${status}` as keyof ThemeColors] ?? '#6b7280';
  },
};
