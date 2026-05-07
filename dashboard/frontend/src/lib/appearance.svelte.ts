/**
 * Appearance preferences — theme + animation toggle.
 *
 * Per-device user preferences (no backend); persisted to localStorage and
 * applied as data-attributes on <html> so CSS can react via attribute
 * selectors. Initialized from main.ts before mount so the first paint is
 * already on the correct theme (no flash).
 */

type Theme = 'light' | 'dark';

const STORAGE_KEY_THEME = 'cas.appearance.theme';
const STORAGE_KEY_ANIMATIONS = 'cas.appearance.animations';

function readTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY_THEME);
  return stored === 'dark' ? 'dark' : 'light';
}

function readAnimations(): boolean {
  return localStorage.getItem(STORAGE_KEY_ANIMATIONS) !== 'off';
}

function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
}

function applyAnimations(enabled: boolean) {
  document.documentElement.dataset.animations = enabled ? 'on' : 'off';
}

export const appearance = $state({
  theme: readTheme(),
  animationsEnabled: readAnimations(),
});

export function setTheme(theme: Theme) {
  appearance.theme = theme;
  localStorage.setItem(STORAGE_KEY_THEME, theme);
  applyTheme(theme);
}

export function setAnimationsEnabled(enabled: boolean) {
  appearance.animationsEnabled = enabled;
  localStorage.setItem(STORAGE_KEY_ANIMATIONS, enabled ? 'on' : 'off');
  applyAnimations(enabled);
}

export function initAppearance() {
  applyTheme(appearance.theme);
  applyAnimations(appearance.animationsEnabled);
}
