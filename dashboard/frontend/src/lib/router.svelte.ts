// ============================================
// Client-side Router — History API, Svelte 5 Runes
// ============================================

export type Page =
  | 'command-center'
  | 'mission-control'
  | 'agent-teams'
  | 'run-detail'
  | 'queue'
  | 'queue-detail'
  | 'projects'
  | 'project-detail'
  | 'settings'
  | 'help';

export interface Route {
  page: Page;
  param: string | null;
}

function parsePath(): Route {
  let path = window.location.pathname || '/';

  // Handle legacy hash URLs
  if (window.location.hash.startsWith('#/')) {
    const hashPath = window.location.hash.slice(1);
    history.replaceState({}, '', hashPath);
    path = hashPath;
  }

  const parts = path.split('/').filter(Boolean);

  if (parts.length === 0) return { page: 'command-center', param: null };

  const raw = parts[0];

  // Parameterized routes
  if (raw === 'runs' && parts.length > 1) return { page: 'run-detail', param: parts[1] };
  if (raw === 'queue' && parts.length > 1) return { page: 'queue-detail', param: parts[1] };
  if (raw === 'projects' && parts.length > 1) return { page: 'project-detail', param: parts[1] };
  if (raw === 'settings' && parts.length > 1) return { page: 'settings', param: parts[1] };

  // Transparent redirect for old bookmarks — keep; do not remove in cleanup passes.
  if (raw === 'autonomy-audit') return { page: 'settings', param: 'audit' };

  // Bare /runs (no param) → Dispatch (command-center). Vestigial enum dropped;
  // legacy bookmarks still resolve.
  if (raw === 'runs') return { page: 'command-center', param: null };

  const routeMap: Record<string, Page> = {
    'mission-control': 'mission-control',
    'agent-teams': 'agent-teams',
    queue: 'queue',
    projects: 'projects',
    settings: 'settings',
    help: 'help',
  };

  if (routeMap[raw]) return { page: routeMap[raw], param: null };

  // Fallback
  return { page: 'command-center', param: null };
}

// --- Exported reactive state ---

export let route = $state<Route>(parsePath());

/** Get current path */
export function getCurrentPath(): string {
  if (route.page === 'command-center') return '/';
  if (route.param) return `/${route.page.replace('-detail', '')}/${route.param}`;
  return `/${route.page}`;
}

/** Get current params */
export function getCurrentParams(): Record<string, string> {
  return route.param ? { id: route.param } : {};
}

function onPopState(): void {
  const next = parsePath();
  route.page = next.page;
  route.param = next.param;
}

if (typeof window !== 'undefined') {
  window.addEventListener('popstate', onPopState);
}

/**
 * Navigate to a path using History API.
 * @param replace - Use replaceState instead of pushState (for redirects)
 */
export function navigate(path: string, replace = false): void {
  if (replace) {
    history.replaceState({}, '', path);
  } else {
    history.pushState({}, '', path);
  }
  const next = parsePath();
  route.page = next.page;
  route.param = next.param;
}

/** Parse current route (for external consumers) */
export function parseRoute(): Route {
  return { page: route.page, param: route.param };
}

/**
 * Click handler for <a> tags -- intercepts navigation to use History API.
 */
export function handleLinkClick(e: MouseEvent): void {
  const target = (e.target as HTMLElement).closest('a');
  if (!target) return;
  const href = target.getAttribute('href');
  if (!href) return;
  if (href.startsWith('http') || href.startsWith('//') || href.startsWith('mailto:') || href.startsWith('#')) return;
  if (target.getAttribute('target') === '_blank') return;
  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  navigate(href);
}

/** Get page display name */
export function getPageTitle(page: Page): string {
  const titles: Record<Page, string> = {
    'command-center': 'Command Center',
    'mission-control': 'Mission Control',
    'agent-teams': 'Agent Teams',
    'run-detail': 'Run Detail',
    'queue': 'Queue Board',
    'queue-detail': 'Queue Item',
    'projects': 'Projects',
    'project-detail': 'Project',
    'settings': 'Settings',
    'help': 'Help',
  };
  return titles[page] ?? 'Claude Station';
}
