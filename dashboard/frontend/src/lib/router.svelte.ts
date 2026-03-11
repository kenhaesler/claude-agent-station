type Page = 'dashboard' | 'projects' | 'plans' | 'plan-detail' | 'runs' | 'run-detail' | 'coordinator' | 'queue' | 'analytics' | 'logs' | 'config' | 'prompts' | 'system';

interface Route {
  page: Page;
  param: string | null;
}

function parseHash(): Route {
  const hash = window.location.hash.slice(1) || '/';
  const parts = hash.split('/').filter(Boolean);

  if (parts.length === 0) return { page: 'dashboard', param: null };

  const page = parts[0] as Page;
  const validPages: Page[] = ['dashboard', 'projects', 'plans', 'runs', 'coordinator', 'queue', 'analytics', 'logs', 'config', 'prompts', 'system'];

  if (page === 'plans' && parts.length > 1) {
    return { page: 'plan-detail', param: parts[1] };
  }
  if (page === 'runs' && parts.length > 1) {
    return { page: 'run-detail', param: parts[1] };
  }
  if (validPages.includes(page)) {
    return { page, param: parts[1] ?? null };
  }
  return { page: 'dashboard', param: null };
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
