/**
 * Unified command registry for Command Palette + keyboard shortcuts.
 * Both systems draw from the same registry.
 */

import { navigate } from './router.svelte';

export interface Command {
  id: string;
  title: string;
  shortcut?: string;
  scope: 'global' | string;
  category: 'navigation' | 'actions' | 'view' | 'system';
  handler: () => void;
  /** Return false to hide the command */
  available?: () => boolean;
  icon?: string;
}

const commands: Command[] = [];

/** Register a command. Returns unregister function. */
export function registerCommand(cmd: Command): () => void {
  commands.push(cmd);
  return () => {
    const idx = commands.indexOf(cmd);
    if (idx !== -1) commands.splice(idx, 1);
  };
}

/** Register multiple commands. Returns unregister-all function. */
export function registerCommands(cmds: Command[]): () => void {
  const unregs = cmds.map(registerCommand);
  return () => unregs.forEach(fn => fn());
}

/** Get all available commands, optionally filtered by scope */
export function getCommands(scope?: string): Command[] {
  return commands.filter(cmd => {
    if (cmd.available && !cmd.available()) return false;
    if (scope && cmd.scope !== 'global' && cmd.scope !== scope) return false;
    return true;
  });
}

/** Search commands by fuzzy title match */
export function searchCommands(query: string, scope?: string): Command[] {
  const q = query.toLowerCase();
  return getCommands(scope)
    .filter(cmd => cmd.title.toLowerCase().includes(q))
    .sort((a, b) => {
      // Exact prefix match first
      const aStarts = a.title.toLowerCase().startsWith(q) ? 0 : 1;
      const bStarts = b.title.toLowerCase().startsWith(q) ? 0 : 1;
      if (aStarts !== bStarts) return aStarts - bStarts;
      return a.title.localeCompare(b.title);
    });
}

/** Execute a command by ID */
export function executeCommand(id: string): boolean {
  const cmd = commands.find(c => c.id === id);
  if (cmd) {
    cmd.handler();
    return true;
  }
  return false;
}

// --- Default navigation commands ---

registerCommands([
  {
    id: 'nav:home',
    title: 'Go to Command Center',
    shortcut: '1',
    scope: 'global',
    category: 'navigation',
    icon: 'home',
    handler: () => navigate('/'),
  },
  {
    id: 'nav:theater',
    title: 'Go to Agent Theater',
    shortcut: '2',
    scope: 'global',
    category: 'navigation',
    icon: 'theater',
    handler: () => navigate('/theater'),
  },
  {
    id: 'nav:runs',
    title: 'Go to Runs',
    shortcut: '3',
    scope: 'global',
    category: 'navigation',
    icon: 'runs',
    handler: () => navigate('/runs'),
  },
  {
    id: 'nav:queue',
    title: 'Go to Queue Board',
    shortcut: '4',
    scope: 'global',
    category: 'navigation',
    icon: 'queue',
    handler: () => navigate('/queue'),
  },
  {
    id: 'nav:intelligence',
    title: 'Go to Intelligence Hub',
    shortcut: '5',
    scope: 'global',
    category: 'navigation',
    icon: 'intelligence',
    handler: () => navigate('/intelligence'),
  },
  {
    id: 'nav:projects',
    title: 'Go to Projects',
    shortcut: '6',
    scope: 'global',
    category: 'navigation',
    icon: 'projects',
    handler: () => navigate('/projects'),
  },
  {
    id: 'nav:integration',
    title: 'Go to Integration Pipeline',
    shortcut: '7',
    scope: 'global',
    category: 'navigation',
    icon: 'integration',
    handler: () => navigate('/integration'),
  },
  {
    id: 'nav:brainstorm',
    title: 'Go to Brainstorm',
    scope: 'global',
    category: 'navigation',
    icon: 'brainstorm',
    handler: () => navigate('/brainstorm'),
  },
  {
    id: 'nav:settings',
    title: 'Go to Settings',
    shortcut: '8',
    scope: 'global',
    category: 'navigation',
    icon: 'settings',
    handler: () => navigate('/settings'),
  },
]);
