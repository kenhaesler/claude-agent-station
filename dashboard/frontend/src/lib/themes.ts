/**
 * Theme definitions for the Claude Agent Station dashboard.
 *
 * Each theme provides values for all CSS custom properties defined in app.css.
 * Colors use OKLCH where possible for perceptual uniformity; hex fallbacks
 * are provided for accent colors that Tailwind consumes directly.
 */

export interface ThemeColors {
  // Backgrounds & surfaces
  '--color-bg': string;
  '--color-bg-alt': string;
  '--color-surface': string;
  '--color-surface-solid': string;
  '--color-surface-2': string;
  '--color-surface-3': string;

  // Borders
  '--color-border': string;
  '--color-border-subtle': string;

  // Text
  '--color-text': string;
  '--color-text-dim': string;
  '--color-text-muted': string;

  // Semantic
  '--color-approve': string;
  '--color-reject': string;
  '--color-pr': string;
  '--color-warning': string;
  '--color-info': string;

  // Accent
  '--color-accent-blue': string;
  '--color-accent-emerald': string;
  '--color-accent-purple': string;
  '--color-accent-orange': string;

  // Agent role colors
  '--color-agent-manager': string;
  '--color-agent-dev-0': string;
  '--color-agent-dev-1': string;
  '--color-agent-dev-2': string;
  '--color-agent-coordinator': string;
  '--color-agent-analyst': string;

  // Scrollbar
  '--color-scrollbar': string;
  '--color-scrollbar-hover': string;

  // Markdown / content
  '--color-code-bg': string;
  '--color-code-text': string;
  '--color-pre-bg': string;
  '--color-pre-border': string;
  '--color-blockquote-border': string;
  '--color-blockquote-text': string;
  '--color-link': string;
  '--color-table-border': string;
  '--color-table-header-bg': string;
  '--color-hr': string;

  // StatusOrb defaults
  '--color-status-active': string;
  '--color-status-inactive': string;
  '--color-status-thinking': string;
  '--color-status-error': string;
  '--color-status-idle': string;
}

export interface Theme {
  id: string;
  label: string;
  description: string;
  /** 'dark' or 'light' — used for prefers-color-scheme matching */
  scheme: 'dark' | 'light';
  colors: ThemeColors;
}

// ─── Theme Definitions ──────────────────────────────────────

const midnight: Theme = {
  id: 'midnight',
  label: 'Midnight',
  description: 'Dark OKLCH palette (default)',
  scheme: 'dark',
  colors: {
    '--color-bg': 'oklch(0.13 0.004 260)',
    '--color-bg-alt': 'oklch(0.15 0.005 260)',
    '--color-surface': 'oklch(0.18 0.005 260)',
    '--color-surface-solid': 'oklch(0.16 0.005 260)',
    '--color-surface-2': 'oklch(0.22 0.005 260)',
    '--color-surface-3': 'oklch(0.26 0.006 260)',
    '--color-border': 'oklch(0.30 0.005 260)',
    '--color-border-subtle': 'oklch(0.24 0.004 260)',
    '--color-text': 'oklch(0.93 0.005 260)',
    '--color-text-dim': 'oklch(0.63 0.01 260)',
    '--color-text-muted': 'oklch(0.45 0.008 260)',
    '--color-approve': 'oklch(0.72 0.17 155)',
    '--color-reject': 'oklch(0.63 0.2 25)',
    '--color-pr': 'oklch(0.65 0.18 300)',
    '--color-warning': 'oklch(0.75 0.15 80)',
    '--color-info': 'oklch(0.62 0.17 260)',
    '--color-accent-blue': '#6366f1',
    '--color-accent-emerald': '#10b981',
    '--color-accent-purple': '#a855f7',
    '--color-accent-orange': '#ff6b35',
    '--color-agent-manager': 'oklch(0.75 0.15 80)',
    '--color-agent-dev-0': 'oklch(0.62 0.17 260)',
    '--color-agent-dev-1': 'oklch(0.55 0.18 280)',
    '--color-agent-dev-2': 'oklch(0.65 0.15 200)',
    '--color-agent-coordinator': 'oklch(0.60 0.20 300)',
    '--color-agent-analyst': 'oklch(0.58 0.18 290)',
    '--color-scrollbar': 'oklch(0.35 0.005 260)',
    '--color-scrollbar-hover': 'oklch(0.42 0.005 260)',
    '--color-code-bg': 'rgba(255, 255, 255, 0.08)',
    '--color-code-text': '#f0abfc',
    '--color-pre-bg': 'rgba(0, 0, 0, 0.3)',
    '--color-pre-border': 'rgba(255, 255, 255, 0.06)',
    '--color-blockquote-border': 'rgba(99, 102, 241, 0.5)',
    '--color-blockquote-text': 'rgba(226, 232, 240, 0.7)',
    '--color-link': '#60a5fa',
    '--color-table-border': 'rgba(255, 255, 255, 0.1)',
    '--color-table-header-bg': 'rgba(255, 255, 255, 0.05)',
    '--color-hr': 'rgba(255, 255, 255, 0.1)',
    '--color-status-active': '#22c55e',
    '--color-status-inactive': '#ef4444',
    '--color-status-thinking': '#f59e0b',
    '--color-status-error': '#ef4444',
    '--color-status-idle': '#6b7280',
  },
};

const light: Theme = {
  id: 'light',
  label: 'Light',
  description: 'Clean light backgrounds, dark text',
  scheme: 'light',
  colors: {
    '--color-bg': 'oklch(0.97 0.003 260)',
    '--color-bg-alt': 'oklch(0.95 0.004 260)',
    '--color-surface': 'oklch(0.99 0.002 260)',
    '--color-surface-solid': 'oklch(0.96 0.003 260)',
    '--color-surface-2': 'oklch(0.93 0.004 260)',
    '--color-surface-3': 'oklch(0.90 0.005 260)',
    '--color-border': 'oklch(0.82 0.005 260)',
    '--color-border-subtle': 'oklch(0.88 0.004 260)',
    '--color-text': 'oklch(0.20 0.005 260)',
    '--color-text-dim': 'oklch(0.45 0.01 260)',
    '--color-text-muted': 'oklch(0.60 0.008 260)',
    '--color-approve': 'oklch(0.55 0.17 155)',
    '--color-reject': 'oklch(0.55 0.2 25)',
    '--color-pr': 'oklch(0.55 0.18 300)',
    '--color-warning': 'oklch(0.60 0.15 80)',
    '--color-info': 'oklch(0.50 0.17 260)',
    '--color-accent-blue': '#4f46e5',
    '--color-accent-emerald': '#059669',
    '--color-accent-purple': '#9333ea',
    '--color-accent-orange': '#ea580c',
    '--color-agent-manager': 'oklch(0.60 0.15 80)',
    '--color-agent-dev-0': 'oklch(0.50 0.17 260)',
    '--color-agent-dev-1': 'oklch(0.45 0.18 280)',
    '--color-agent-dev-2': 'oklch(0.55 0.15 200)',
    '--color-agent-coordinator': 'oklch(0.50 0.20 300)',
    '--color-agent-analyst': 'oklch(0.48 0.18 290)',
    '--color-scrollbar': 'oklch(0.78 0.005 260)',
    '--color-scrollbar-hover': 'oklch(0.70 0.005 260)',
    '--color-code-bg': 'rgba(0, 0, 0, 0.06)',
    '--color-code-text': '#9333ea',
    '--color-pre-bg': 'oklch(0.95 0.003 260)',
    '--color-pre-border': 'rgba(0, 0, 0, 0.08)',
    '--color-blockquote-border': 'rgba(79, 70, 229, 0.4)',
    '--color-blockquote-text': 'oklch(0.40 0.01 260)',
    '--color-link': '#4f46e5',
    '--color-table-border': 'rgba(0, 0, 0, 0.1)',
    '--color-table-header-bg': 'rgba(0, 0, 0, 0.04)',
    '--color-hr': 'rgba(0, 0, 0, 0.1)',
    '--color-status-active': '#16a34a',
    '--color-status-inactive': '#dc2626',
    '--color-status-thinking': '#d97706',
    '--color-status-error': '#dc2626',
    '--color-status-idle': '#9ca3af',
  },
};

const solarizedDark: Theme = {
  id: 'solarized-dark',
  label: 'Solarized Dark',
  description: 'Ethan Schoonover\'s warm dark palette',
  scheme: 'dark',
  colors: {
    '--color-bg': '#002b36',
    '--color-bg-alt': '#073642',
    '--color-surface': '#073642',
    '--color-surface-solid': '#003847',
    '--color-surface-2': '#0a4555',
    '--color-surface-3': '#0d5264',
    '--color-border': '#586e75',
    '--color-border-subtle': '#2a4a53',
    '--color-text': '#fdf6e3',
    '--color-text-dim': '#93a1a1',
    '--color-text-muted': '#657b83',
    '--color-approve': '#859900',
    '--color-reject': '#dc322f',
    '--color-pr': '#6c71c4',
    '--color-warning': '#b58900',
    '--color-info': '#268bd2',
    '--color-accent-blue': '#268bd2',
    '--color-accent-emerald': '#859900',
    '--color-accent-purple': '#6c71c4',
    '--color-accent-orange': '#cb4b16',
    '--color-agent-manager': '#b58900',
    '--color-agent-dev-0': '#268bd2',
    '--color-agent-dev-1': '#6c71c4',
    '--color-agent-dev-2': '#2aa198',
    '--color-agent-coordinator': '#d33682',
    '--color-agent-analyst': '#6c71c4',
    '--color-scrollbar': '#586e75',
    '--color-scrollbar-hover': '#839496',
    '--color-code-bg': 'rgba(238, 232, 213, 0.08)',
    '--color-code-text': '#d33682',
    '--color-pre-bg': '#002b36',
    '--color-pre-border': 'rgba(238, 232, 213, 0.06)',
    '--color-blockquote-border': 'rgba(38, 139, 210, 0.5)',
    '--color-blockquote-text': '#93a1a1',
    '--color-link': '#268bd2',
    '--color-table-border': 'rgba(238, 232, 213, 0.1)',
    '--color-table-header-bg': 'rgba(238, 232, 213, 0.05)',
    '--color-hr': 'rgba(238, 232, 213, 0.1)',
    '--color-status-active': '#859900',
    '--color-status-inactive': '#dc322f',
    '--color-status-thinking': '#b58900',
    '--color-status-error': '#dc322f',
    '--color-status-idle': '#657b83',
  },
};

const solarizedLight: Theme = {
  id: 'solarized-light',
  label: 'Solarized Light',
  description: 'Ethan Schoonover\'s warm light palette',
  scheme: 'light',
  colors: {
    '--color-bg': '#fdf6e3',
    '--color-bg-alt': '#eee8d5',
    '--color-surface': '#fdf6e3',
    '--color-surface-solid': '#eee8d5',
    '--color-surface-2': '#e6dfca',
    '--color-surface-3': '#ddd6c1',
    '--color-border': '#93a1a1',
    '--color-border-subtle': '#c9c2a8',
    '--color-text': '#002b36',
    '--color-text-dim': '#586e75',
    '--color-text-muted': '#93a1a1',
    '--color-approve': '#859900',
    '--color-reject': '#dc322f',
    '--color-pr': '#6c71c4',
    '--color-warning': '#b58900',
    '--color-info': '#268bd2',
    '--color-accent-blue': '#268bd2',
    '--color-accent-emerald': '#859900',
    '--color-accent-purple': '#6c71c4',
    '--color-accent-orange': '#cb4b16',
    '--color-agent-manager': '#b58900',
    '--color-agent-dev-0': '#268bd2',
    '--color-agent-dev-1': '#6c71c4',
    '--color-agent-dev-2': '#2aa198',
    '--color-agent-coordinator': '#d33682',
    '--color-agent-analyst': '#6c71c4',
    '--color-scrollbar': '#93a1a1',
    '--color-scrollbar-hover': '#586e75',
    '--color-code-bg': 'rgba(0, 43, 54, 0.06)',
    '--color-code-text': '#d33682',
    '--color-pre-bg': '#eee8d5',
    '--color-pre-border': 'rgba(0, 43, 54, 0.08)',
    '--color-blockquote-border': 'rgba(38, 139, 210, 0.4)',
    '--color-blockquote-text': '#586e75',
    '--color-link': '#268bd2',
    '--color-table-border': 'rgba(0, 43, 54, 0.1)',
    '--color-table-header-bg': 'rgba(0, 43, 54, 0.04)',
    '--color-hr': 'rgba(0, 43, 54, 0.1)',
    '--color-status-active': '#859900',
    '--color-status-inactive': '#dc322f',
    '--color-status-thinking': '#b58900',
    '--color-status-error': '#dc322f',
    '--color-status-idle': '#93a1a1',
  },
};

const nord: Theme = {
  id: 'nord',
  label: 'Nord',
  description: 'Arctic, blue-tinted dark palette',
  scheme: 'dark',
  colors: {
    '--color-bg': '#2e3440',
    '--color-bg-alt': '#3b4252',
    '--color-surface': '#3b4252',
    '--color-surface-solid': '#353c4a',
    '--color-surface-2': '#434c5e',
    '--color-surface-3': '#4c566a',
    '--color-border': '#4c566a',
    '--color-border-subtle': '#434c5e',
    '--color-text': '#eceff4',
    '--color-text-dim': '#d8dee9',
    '--color-text-muted': '#81a1c1',
    '--color-approve': '#a3be8c',
    '--color-reject': '#bf616a',
    '--color-pr': '#b48ead',
    '--color-warning': '#ebcb8b',
    '--color-info': '#81a1c1',
    '--color-accent-blue': '#5e81ac',
    '--color-accent-emerald': '#a3be8c',
    '--color-accent-purple': '#b48ead',
    '--color-accent-orange': '#d08770',
    '--color-agent-manager': '#ebcb8b',
    '--color-agent-dev-0': '#81a1c1',
    '--color-agent-dev-1': '#5e81ac',
    '--color-agent-dev-2': '#88c0d0',
    '--color-agent-coordinator': '#b48ead',
    '--color-agent-analyst': '#5e81ac',
    '--color-scrollbar': '#4c566a',
    '--color-scrollbar-hover': '#81a1c1',
    '--color-code-bg': 'rgba(216, 222, 233, 0.08)',
    '--color-code-text': '#b48ead',
    '--color-pre-bg': '#2e3440',
    '--color-pre-border': 'rgba(216, 222, 233, 0.06)',
    '--color-blockquote-border': 'rgba(94, 129, 172, 0.5)',
    '--color-blockquote-text': '#d8dee9',
    '--color-link': '#88c0d0',
    '--color-table-border': 'rgba(216, 222, 233, 0.1)',
    '--color-table-header-bg': 'rgba(216, 222, 233, 0.05)',
    '--color-hr': 'rgba(216, 222, 233, 0.1)',
    '--color-status-active': '#a3be8c',
    '--color-status-inactive': '#bf616a',
    '--color-status-thinking': '#ebcb8b',
    '--color-status-error': '#bf616a',
    '--color-status-idle': '#4c566a',
  },
};

const dracula: Theme = {
  id: 'dracula',
  label: 'Dracula',
  description: 'Popular dark theme with vivid accents',
  scheme: 'dark',
  colors: {
    '--color-bg': '#282a36',
    '--color-bg-alt': '#21222c',
    '--color-surface': '#44475a',
    '--color-surface-solid': '#343746',
    '--color-surface-2': '#44475a',
    '--color-surface-3': '#535778',
    '--color-border': '#6272a4',
    '--color-border-subtle': '#44475a',
    '--color-text': '#f8f8f2',
    '--color-text-dim': '#bd93f9',
    '--color-text-muted': '#6272a4',
    '--color-approve': '#50fa7b',
    '--color-reject': '#ff5555',
    '--color-pr': '#bd93f9',
    '--color-warning': '#f1fa8c',
    '--color-info': '#8be9fd',
    '--color-accent-blue': '#6272a4',
    '--color-accent-emerald': '#50fa7b',
    '--color-accent-purple': '#bd93f9',
    '--color-accent-orange': '#ffb86c',
    '--color-agent-manager': '#f1fa8c',
    '--color-agent-dev-0': '#8be9fd',
    '--color-agent-dev-1': '#bd93f9',
    '--color-agent-dev-2': '#50fa7b',
    '--color-agent-coordinator': '#ff79c6',
    '--color-agent-analyst': '#bd93f9',
    '--color-scrollbar': '#44475a',
    '--color-scrollbar-hover': '#6272a4',
    '--color-code-bg': 'rgba(248, 248, 242, 0.08)',
    '--color-code-text': '#ff79c6',
    '--color-pre-bg': '#21222c',
    '--color-pre-border': 'rgba(248, 248, 242, 0.06)',
    '--color-blockquote-border': 'rgba(189, 147, 249, 0.5)',
    '--color-blockquote-text': 'rgba(248, 248, 242, 0.7)',
    '--color-link': '#8be9fd',
    '--color-table-border': 'rgba(248, 248, 242, 0.1)',
    '--color-table-header-bg': 'rgba(248, 248, 242, 0.05)',
    '--color-hr': 'rgba(248, 248, 242, 0.1)',
    '--color-status-active': '#50fa7b',
    '--color-status-inactive': '#ff5555',
    '--color-status-thinking': '#f1fa8c',
    '--color-status-error': '#ff5555',
    '--color-status-idle': '#6272a4',
  },
};

const monokai: Theme = {
  id: 'monokai',
  label: 'Monokai',
  description: 'Warm dark editor classic',
  scheme: 'dark',
  colors: {
    '--color-bg': '#272822',
    '--color-bg-alt': '#1e1f1a',
    '--color-surface': '#3e3d32',
    '--color-surface-solid': '#33332a',
    '--color-surface-2': '#49483e',
    '--color-surface-3': '#575648',
    '--color-border': '#75715e',
    '--color-border-subtle': '#49483e',
    '--color-text': '#f8f8f2',
    '--color-text-dim': '#a6a68a',
    '--color-text-muted': '#75715e',
    '--color-approve': '#a6e22e',
    '--color-reject': '#f92672',
    '--color-pr': '#ae81ff',
    '--color-warning': '#e6db74',
    '--color-info': '#66d9ef',
    '--color-accent-blue': '#66d9ef',
    '--color-accent-emerald': '#a6e22e',
    '--color-accent-purple': '#ae81ff',
    '--color-accent-orange': '#fd971f',
    '--color-agent-manager': '#e6db74',
    '--color-agent-dev-0': '#66d9ef',
    '--color-agent-dev-1': '#ae81ff',
    '--color-agent-dev-2': '#a6e22e',
    '--color-agent-coordinator': '#f92672',
    '--color-agent-analyst': '#ae81ff',
    '--color-scrollbar': '#49483e',
    '--color-scrollbar-hover': '#75715e',
    '--color-code-bg': 'rgba(248, 248, 242, 0.08)',
    '--color-code-text': '#f92672',
    '--color-pre-bg': '#1e1f1a',
    '--color-pre-border': 'rgba(248, 248, 242, 0.06)',
    '--color-blockquote-border': 'rgba(102, 217, 239, 0.5)',
    '--color-blockquote-text': 'rgba(248, 248, 242, 0.7)',
    '--color-link': '#66d9ef',
    '--color-table-border': 'rgba(248, 248, 242, 0.1)',
    '--color-table-header-bg': 'rgba(248, 248, 242, 0.05)',
    '--color-hr': 'rgba(248, 248, 242, 0.1)',
    '--color-status-active': '#a6e22e',
    '--color-status-inactive': '#f92672',
    '--color-status-thinking': '#e6db74',
    '--color-status-error': '#f92672',
    '--color-status-idle': '#75715e',
  },
};

const cyberpunk: Theme = {
  id: 'cyberpunk',
  label: 'Cyberpunk',
  description: 'High-contrast neons on deep black',
  scheme: 'dark',
  colors: {
    '--color-bg': '#0a0a0f',
    '--color-bg-alt': '#0f0f18',
    '--color-surface': '#12121f',
    '--color-surface-solid': '#0e0e1a',
    '--color-surface-2': '#1a1a2e',
    '--color-surface-3': '#22223a',
    '--color-border': '#ff00ff40',
    '--color-border-subtle': '#ff00ff20',
    '--color-text': '#f0f0ff',
    '--color-text-dim': '#00ffff',
    '--color-text-muted': '#ff00ff80',
    '--color-approve': '#00ff88',
    '--color-reject': '#ff0044',
    '--color-pr': '#ff00ff',
    '--color-warning': '#ffff00',
    '--color-info': '#00ffff',
    '--color-accent-blue': '#00ccff',
    '--color-accent-emerald': '#00ff88',
    '--color-accent-purple': '#ff00ff',
    '--color-accent-orange': '#ff6600',
    '--color-agent-manager': '#ffff00',
    '--color-agent-dev-0': '#00ffff',
    '--color-agent-dev-1': '#ff00ff',
    '--color-agent-dev-2': '#00ff88',
    '--color-agent-coordinator': '#ff0088',
    '--color-agent-analyst': '#ff00ff',
    '--color-scrollbar': '#ff00ff40',
    '--color-scrollbar-hover': '#ff00ff80',
    '--color-code-bg': 'rgba(0, 255, 255, 0.08)',
    '--color-code-text': '#ff00ff',
    '--color-pre-bg': '#0a0a0f',
    '--color-pre-border': 'rgba(255, 0, 255, 0.15)',
    '--color-blockquote-border': '#00ffff80',
    '--color-blockquote-text': '#00ffffcc',
    '--color-link': '#00ffff',
    '--color-table-border': 'rgba(255, 0, 255, 0.2)',
    '--color-table-header-bg': 'rgba(0, 255, 255, 0.05)',
    '--color-hr': 'rgba(255, 0, 255, 0.3)',
    '--color-status-active': '#00ff88',
    '--color-status-inactive': '#ff0044',
    '--color-status-thinking': '#ffff00',
    '--color-status-error': '#ff0044',
    '--color-status-idle': '#ff00ff40',
  },
};

const terminalGreen: Theme = {
  id: 'terminal-green',
  label: 'Terminal Green',
  description: 'Retro green-on-black CRT aesthetic',
  scheme: 'dark',
  colors: {
    '--color-bg': '#0a0a0a',
    '--color-bg-alt': '#0f0f0f',
    '--color-surface': '#141414',
    '--color-surface-solid': '#111111',
    '--color-surface-2': '#1a1a1a',
    '--color-surface-3': '#222222',
    '--color-border': '#00aa0040',
    '--color-border-subtle': '#00aa0020',
    '--color-text': '#00ff00',
    '--color-text-dim': '#00cc00',
    '--color-text-muted': '#008800',
    '--color-approve': '#00ff00',
    '--color-reject': '#ff3333',
    '--color-pr': '#00ccff',
    '--color-warning': '#ffcc00',
    '--color-info': '#00dd00',
    '--color-accent-blue': '#00ccff',
    '--color-accent-emerald': '#00ff00',
    '--color-accent-purple': '#00ccff',
    '--color-accent-orange': '#ffcc00',
    '--color-agent-manager': '#ffcc00',
    '--color-agent-dev-0': '#00ff00',
    '--color-agent-dev-1': '#00cc00',
    '--color-agent-dev-2': '#00ff88',
    '--color-agent-coordinator': '#00ccff',
    '--color-agent-analyst': '#00dd00',
    '--color-scrollbar': '#00aa0040',
    '--color-scrollbar-hover': '#00aa0080',
    '--color-code-bg': 'rgba(0, 255, 0, 0.06)',
    '--color-code-text': '#00ff88',
    '--color-pre-bg': '#0a0a0a',
    '--color-pre-border': 'rgba(0, 255, 0, 0.1)',
    '--color-blockquote-border': 'rgba(0, 255, 0, 0.3)',
    '--color-blockquote-text': '#00cc00',
    '--color-link': '#00ccff',
    '--color-table-border': 'rgba(0, 255, 0, 0.15)',
    '--color-table-header-bg': 'rgba(0, 255, 0, 0.05)',
    '--color-hr': 'rgba(0, 255, 0, 0.2)',
    '--color-status-active': '#00ff00',
    '--color-status-inactive': '#ff3333',
    '--color-status-thinking': '#ffcc00',
    '--color-status-error': '#ff3333',
    '--color-status-idle': '#008800',
  },
};

const highContrast: Theme = {
  id: 'high-contrast',
  label: 'High Contrast',
  description: 'WCAG AAA accessible, maximum readability',
  scheme: 'dark',
  colors: {
    '--color-bg': '#000000',
    '--color-bg-alt': '#0a0a0a',
    '--color-surface': '#1a1a1a',
    '--color-surface-solid': '#111111',
    '--color-surface-2': '#2a2a2a',
    '--color-surface-3': '#3a3a3a',
    '--color-border': '#ffffff',
    '--color-border-subtle': '#808080',
    '--color-text': '#ffffff',
    '--color-text-dim': '#e0e0e0',
    '--color-text-muted': '#b0b0b0',
    '--color-approve': '#00ff00',
    '--color-reject': '#ff4444',
    '--color-pr': '#cc88ff',
    '--color-warning': '#ffdd00',
    '--color-info': '#44bbff',
    '--color-accent-blue': '#44bbff',
    '--color-accent-emerald': '#00ff00',
    '--color-accent-purple': '#cc88ff',
    '--color-accent-orange': '#ff8800',
    '--color-agent-manager': '#ffdd00',
    '--color-agent-dev-0': '#44bbff',
    '--color-agent-dev-1': '#cc88ff',
    '--color-agent-dev-2': '#00ffaa',
    '--color-agent-coordinator': '#ff44cc',
    '--color-agent-analyst': '#cc88ff',
    '--color-scrollbar': '#808080',
    '--color-scrollbar-hover': '#ffffff',
    '--color-code-bg': 'rgba(255, 255, 255, 0.12)',
    '--color-code-text': '#ff88cc',
    '--color-pre-bg': '#0a0a0a',
    '--color-pre-border': 'rgba(255, 255, 255, 0.3)',
    '--color-blockquote-border': '#44bbff',
    '--color-blockquote-text': '#e0e0e0',
    '--color-link': '#44bbff',
    '--color-table-border': 'rgba(255, 255, 255, 0.3)',
    '--color-table-header-bg': 'rgba(255, 255, 255, 0.1)',
    '--color-hr': 'rgba(255, 255, 255, 0.4)',
    '--color-status-active': '#00ff00',
    '--color-status-inactive': '#ff4444',
    '--color-status-thinking': '#ffdd00',
    '--color-status-error': '#ff4444',
    '--color-status-idle': '#808080',
  },
};

// ─── Exports ────────────────────────────────────────────────

export const themes: Theme[] = [
  midnight,
  light,
  solarizedDark,
  solarizedLight,
  nord,
  dracula,
  monokai,
  cyberpunk,
  terminalGreen,
  highContrast,
];

export const defaultThemeId = 'midnight';

export function getThemeById(id: string): Theme {
  return themes.find(t => t.id === id) ?? midnight;
}
