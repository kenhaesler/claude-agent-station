type Page = 'command' | 'stream' | 'stream-detail' | 'decide' | 'decide-detail' | 'config';

interface Route {
  page: Page;
  param: string | null;
}

/** Map old hash routes to new equivalents */
const REDIRECTS: Record<string, string> = {
  '/': '/command',
  '/dashboard': '/command',
  '/runs': '/stream',
  '/coordinator': '/stream',
  '/queue': '/stream',
  '/analytics': '/command',
  '/logs': '/stream',
  '/plans': '/decide',
  '/projects': '/config',
  '/prompts': '/config',
  '/settings': '/config',
  '/config': '/config',
  '/system': '/config',
};

function parseHash(): Route {
  const hash = window.location.hash.slice(1) || '/';
  const parts = hash.split('/').filter(Boolean);

  if (parts.length === 0) return { page: 'command', param: null };

  const raw = parts[0];

  // Handle old routes with params first
  if (raw === 'runs' && parts.length > 1) {
    window.location.hash = `/stream/${parts[1]}`;
    return { page: 'stream-detail', param: parts[1] };
  }
  if (raw === 'plans' && parts.length > 1) {
    window.location.hash = `/decide/${parts[1]}`;
    return { page: 'decide-detail', param: parts[1] };
  }

  // Handle old route redirects (without params)
  const redirect = REDIRECTS[`/${raw}`];
  if (redirect && !['command', 'stream', 'decide', 'config'].includes(raw)) {
    window.location.hash = redirect;
    return { page: redirect.slice(1) as Page, param: null };
  }

  // New routes
  if (raw === 'command') return { page: 'command', param: null };
  if (raw === 'stream' && parts.length > 1) return { page: 'stream-detail', param: parts[1] };
  if (raw === 'stream') return { page: 'stream', param: null };
  if (raw === 'decide' && parts.length > 1) return { page: 'decide-detail', param: parts[1] };
  if (raw === 'decide') return { page: 'decide', param: null };
  if (raw === 'config') return { page: 'config', param: parts[1] ?? null };

  // Fallback
  return { page: 'command', param: null };
}

export let route = $state<Route>(parseHash());

function onHashChange() {
  const next = parseHash();
  route.page = next.page;
  route.param = next.param;
}

if (typeof window !== 'undefined') {
  window.addEventListener('hashchange', onHashChange);
}

export function navigate(path: string) {
  window.location.hash = path;
}
