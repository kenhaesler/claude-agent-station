/**
 * Client-side router — History API based.
 * Mission Control layout with Command Center home.
 */

type Page =
  | 'command-center'      // Home overview (default)
  | 'theater'             // Live agent visualization
  | 'runs'                // Run history
  | 'run-detail'          // Single run deep-dive
  | 'queue'               // Queue Board (kanban)
  | 'queue-detail'        // Single queue item
  | 'intelligence'        // Analytics & Intelligence
  | 'projects'            // Project Registry
  | 'project-detail'      // Single project
  | 'integration'         // Integration Branch Pipeline
  | 'brainstorm'          // Brainstorm Sessions
  | 'brainstorm-session'  // Single brainstorm session
  | 'settings';           // System Configuration

interface Route {
  page: Page;
  param: string | null;
}

/** Map old routes to new equivalents */
const REDIRECTS: Record<string, string> = {
  '/ops': '/',
  '/command': '/',
  '/dashboard': '/',
  '/pulse': '/',
  '/stream': '/runs',
  '/coordinator': '/runs',
  '/logs': '/runs',
  '/decide': '/queue',
  '/plans': '/queue',
  '/config': '/settings',
  '/prompts': '/settings',
  '/system': '/settings',
  '/observatory': '/theater',
  '/agents': '/theater',
  '/analytics': '/intelligence',
};

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

  // Parameterized routes first
  if (raw === 'runs' && parts.length > 1) return { page: 'run-detail', param: parts[1] };
  if (raw === 'stream' && parts.length > 1) {
    navigate(`/runs/${parts[1]}`, true);
    return { page: 'run-detail', param: parts[1] };
  }
  if (raw === 'queue' && parts.length > 1) return { page: 'queue-detail', param: parts[1] };
  if (raw === 'plans' && parts.length > 1) {
    navigate(`/queue/${parts[1]}`, true);
    return { page: 'queue-detail', param: parts[1] };
  }
  if (raw === 'decide' && parts.length > 1) {
    navigate(`/queue/${parts[1]}`, true);
    return { page: 'queue-detail', param: parts[1] };
  }
  if (raw === 'projects' && parts.length > 1) return { page: 'project-detail', param: parts[1] };
  if (raw === 'brainstorm' && parts.length > 1) return { page: 'brainstorm-session', param: parts[1] };
  if (raw === 'settings' && parts.length > 1) return { page: 'settings', param: parts[1] };

  // Redirect old routes (without params)
  const redirect = REDIRECTS[`/${raw}`];
  if (redirect) {
    navigate(redirect, true);
    const rParts = redirect.split('/').filter(Boolean);
    if (rParts.length === 0) return { page: 'command-center', param: null };
    return { page: rParts[0] as Page, param: null };
  }

  // New routes
  const routeMap: Record<string, Page> = {
    theater: 'theater',
    runs: 'runs',
    queue: 'queue',
    intelligence: 'intelligence',
    projects: 'projects',
    integration: 'integration',
    brainstorm: 'brainstorm',
    settings: 'settings',
  };

  if (routeMap[raw]) return { page: routeMap[raw], param: null };

  // Fallback
  return { page: 'command-center', param: null };
}

export let route = $state<Route>(parsePath());

function onPopState() {
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
export function navigate(path: string, replace = false) {
  if (replace) {
    history.replaceState({}, '', path);
  } else {
    history.pushState({}, '', path);
  }
  const next = parsePath();
  route.page = next.page;
  route.param = next.param;
}

/**
 * Click handler for <a> tags — intercepts navigation to use History API.
 */
export function handleLinkClick(e: MouseEvent) {
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
    'theater': 'Agent Theater',
    'runs': 'Runs',
    'run-detail': 'Run Detail',
    'queue': 'Queue Board',
    'queue-detail': 'Queue Item',
    'intelligence': 'Intelligence Hub',
    'projects': 'Projects',
    'project-detail': 'Project',
    'integration': 'Integration',
    'brainstorm': 'Brainstorm',
    'brainstorm-session': 'Brainstorm',
    'settings': 'Settings',
  };
  return titles[page] ?? 'Claude Station';
}
