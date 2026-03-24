type Page = 'command' | 'stream' | 'stream-detail' | 'decide' | 'decide-detail' | 'config' | 'brainstorm' | 'brainstorm-session' | 'agents' | 'agent-detail' | 'analytics' | 'integration';

interface Route {
  page: Page;
  param: string | null;
}

/** Map old routes to new equivalents */
const REDIRECTS: Record<string, string> = {
  '/': '/command',
  '/dashboard': '/command',
  '/runs': '/stream',
  '/coordinator': '/stream',
  '/queue': '/stream',
  '/logs': '/stream',
  '/plans': '/decide',
  '/projects': '/config',
  '/prompts': '/config',
  '/settings': '/config',
  '/system': '/config',
  '/observatory': '/agents',
};

function parsePath(): Route {
  let path = window.location.pathname || '/';

  // Handle legacy hash URLs — redirect to clean path
  if (window.location.hash.startsWith('#/')) {
    const hashPath = window.location.hash.slice(1);
    history.replaceState({}, '', hashPath);
    path = hashPath;
  }

  const parts = path.split('/').filter(Boolean);

  if (parts.length === 0) return { page: 'command', param: null };

  const raw = parts[0];

  // Handle old routes with params first
  if (raw === 'runs' && parts.length > 1) {
    navigate(`/stream/${parts[1]}`, true);
    return { page: 'stream-detail', param: parts[1] };
  }
  if (raw === 'plans' && parts.length > 1) {
    navigate(`/decide/${parts[1]}`, true);
    return { page: 'decide-detail', param: parts[1] };
  }

  // Handle old route redirects (without params)
  const redirect = REDIRECTS[`/${raw}`];
  if (redirect && !['command', 'stream', 'decide', 'config', 'brainstorm', 'agents', 'analytics', 'integration'].includes(raw)) {
    navigate(redirect, true);
    return { page: redirect.slice(1) as Page, param: null };
  }

  // New routes
  if (raw === 'command') return { page: 'command', param: null };
  if (raw === 'stream' && parts.length > 1) return { page: 'stream-detail', param: parts[1] };
  if (raw === 'stream') return { page: 'stream', param: null };
  if (raw === 'decide' && parts.length > 1) return { page: 'decide-detail', param: parts[1] };
  if (raw === 'decide') return { page: 'decide', param: null };
  if (raw === 'config') return { page: 'config', param: parts[1] ?? null };
  if (raw === 'brainstorm' && parts.length > 1) return { page: 'brainstorm-session', param: parts[1] };
  if (raw === 'brainstorm') return { page: 'brainstorm', param: null };
  if (raw === 'agents' && parts.length > 1) return { page: 'agent-detail', param: parts[1] };
  if (raw === 'agents') return { page: 'agents', param: null };
  if (raw === 'analytics') return { page: 'analytics', param: null };
  if (raw === 'integration') return { page: 'integration', param: null };

  // Fallback
  return { page: 'command', param: null };
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
 * Use on the root element to handle all internal links.
 */
export function handleLinkClick(e: MouseEvent) {
  const target = (e.target as HTMLElement).closest('a');
  if (!target) return;
  const href = target.getAttribute('href');
  if (!href) return;
  // Skip external links, anchors, and special protocols
  if (href.startsWith('http') || href.startsWith('//') || href.startsWith('mailto:') || href.startsWith('#')) return;
  // Skip links with target="_blank"
  if (target.getAttribute('target') === '_blank') return;
  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  navigate(href);
}
