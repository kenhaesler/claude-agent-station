/**
 * Theme stub — agent role colors are defined in app.css @theme.
 * This module exists only to provide getRoleColors() to agent-presence.
 */

const ROLE_COLORS: Record<string, string> = {
  manager: '#B06030',
  'dev-0': '#2E7D32',
  'dev-1': 'rgba(99,102,180,1)',
  'dev-2': '#06B6D4',
  coordinator: '#4A3728',
  analyst: '#B06030',
};

export const themeStore = {
  getRoleColors(): Record<string, string> {
    return { ...ROLE_COLORS };
  },

  getStatusColor(status: 'active' | 'inactive' | 'thinking' | 'error' | 'idle'): string {
    const map: Record<string, string> = {
      active: '#2E7D32',
      inactive: '#8C7A66',
      thinking: '#B06030',
      error: '#D06050',
      idle: '#8C7A66',
    };
    return map[status] ?? '#8C7A66';
  },
};
