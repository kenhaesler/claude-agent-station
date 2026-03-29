/**
 * Theme stub — agent role colors are defined in app.css @theme.
 * This module exists only to provide getRoleColors() to agent-presence.
 */

const ROLE_COLORS: Record<string, string> = {
  manager: '#F59E0B',
  'dev-0': '#6366F1',
  'dev-1': '#8B5CF6',
  'dev-2': '#06B6D4',
  coordinator: '#A855F7',
  analyst: '#7C3AED',
};

export const themeStore = {
  getRoleColors(): Record<string, string> {
    return { ...ROLE_COLORS };
  },

  getStatusColor(status: 'active' | 'inactive' | 'thinking' | 'error' | 'idle'): string {
    const map: Record<string, string> = {
      active: '#10B981',
      inactive: '#606078',
      thinking: '#8B5CF6',
      error: '#F43F5E',
      idle: '#606078',
    };
    return map[status] ?? '#606078';
  },
};
