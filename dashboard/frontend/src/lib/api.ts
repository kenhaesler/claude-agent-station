// ============================================
// API Client — Typed fetch wrapper
// Imports from new unified type system
// ============================================

import type {
  Run, RunList, RunFullContext, ActiveEmployee,
  Project, ProjectCreate,
  Plan, PlanList,
  QueueItem, QueueItemList, QueueStats, BackpressureStatus,
  CoordinatorTask, CoordinatorDAG, CoordinatorMessage, GuidanceSend,
  SystemStatus, AuthStatus,
  AnalyticsResponse, DiffResult, PlanUsage,
  PromptInfo, StationConfig, TokenUsage,
  AgentEvent, Notification,
} from './types';

import { toastError } from './toast.svelte';

const BASE = import.meta.env.VITE_API_URL || '';

// --- API Key helpers (stored in localStorage) ---

const API_KEY_STORAGE_KEY = 'station-api-key';

export function getStoredApiKey(): string | null {
  return localStorage.getItem(API_KEY_STORAGE_KEY);
}

export function setStoredApiKey(key: string): void {
  localStorage.setItem(API_KEY_STORAGE_KEY, key);
}

export function clearStoredApiKey(): void {
  localStorage.removeItem(API_KEY_STORAGE_KEY);
}

// --- Fetch wrapper ---

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30_000);

  const apiKey = getStoredApiKey();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(init?.headers as Record<string, string> ?? {}),
  };
  if (apiKey) {
    headers['Authorization'] = `Bearer ${apiKey}`;
  }

  try {
    const res = await fetch(`${BASE}${path}`, {
      ...init,
      headers,
      signal: controller.signal,
    });

    if (!res.ok) {
      if (res.status === 401) {
        window.dispatchEvent(new CustomEvent('station-auth-required'));
      }
      const body = await res.text();
      let message: string;
      try {
        const parsed = JSON.parse(body);
        const detail = parsed.detail;
        message = Array.isArray(detail)
          ? detail.map((e: Record<string, unknown>) => e.msg ?? JSON.stringify(e)).join('; ')
          : (detail || body);
      } catch {
        message = body;
      }
      throw new Error(`${res.status}: ${message}`);
    }

    if (res.status === 204) return undefined as T;
    return res.json();
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new Error('Request timed out');
    }
    throw err;
  } finally {
    clearTimeout(timeout);
  }
}

/** request() with automatic toast on error */
async function requestWithToast<T>(path: string, init?: RequestInit): Promise<T> {
  try {
    return await request<T>(path, init);
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'Request failed';
    toastError(msg);
    throw err;
  }
}

// --- Query string helper ---

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const q = new URLSearchParams();
  for (const [key, val] of Object.entries(params)) {
    if (val != null && val !== '' && val !== false) {
      q.set(key, String(val));
    }
  }
  const str = q.toString();
  return str ? `?${str}` : '';
}

// --- Health ---

export const getHealth = () =>
  request<{ status: string }>('/api/health');

// --- Projects ---

export const listProjects = () =>
  request<Project[]>('/api/projects');

export const getProject = (id: number) =>
  request<Project>(`/api/projects/${id}`);

export const createProject = (data: ProjectCreate) =>
  requestWithToast<Project>('/api/projects', {
    method: 'POST', body: JSON.stringify(data),
  });

export const updateProject = (id: number, data: Partial<Project>) =>
  requestWithToast<Project>(`/api/projects/${id}`, {
    method: 'PUT', body: JSON.stringify(data),
  });

export const deleteProject = (id: number) =>
  requestWithToast<void>(`/api/projects/${id}`, { method: 'DELETE' });

// --- Permissions (ADR-0001 tray) ---

export interface PermissionRequest {
  id: number;
  request_id: string;
  run_id: string;
  agent_id: string;
  tool_name: string;
  tool_input: Record<string, unknown>;
  autonomy_level: string;
  reason: string | null;
  status: 'pending' | 'approved' | 'denied' | 'timed_out';
  resolution_note: string | null;
  created_at: string | null;
  resolved_at: string | null;
}

export const listPermissionRequests = (params?: { status?: string; run_id?: string; limit?: number }) => {
  const q = new URLSearchParams();
  if (params?.status) q.set('status', params.status);
  if (params?.run_id) q.set('run_id', params.run_id);
  if (params?.limit != null) q.set('limit', String(params.limit));
  const qs = q.toString();
  return request<PermissionRequest[]>(`/api/permissions${qs ? '?' + qs : ''}`);
};

export const resolvePermissionRequest = (
  requestId: string,
  decision: 'approve' | 'deny',
  note?: string,
) =>
  request<PermissionRequest>(`/api/permissions/${encodeURIComponent(requestId)}`, {
    method: 'POST',
    body: JSON.stringify({ decision, note }),
  });

// --- Runs ---

export const listRuns = (params?: {
  limit?: number; offset?: number; project_id?: number;
  status?: string; verdict?: string; concurrent_group_id?: string;
}) =>
  request<RunList>(`/api/runs${qs(params ?? {})}`);

export const getActiveEmployees = () =>
  request<ActiveEmployee[]>('/api/runs/active-employees');

export const getActiveTeammates = () =>
  request<ActiveEmployee[]>('/api/runs/active-teammates');

export const getLatestRun = () =>
  request<Run>('/api/runs/latest');

export const getRun = (runId: string) =>
  request<Run>(`/api/runs/${runId}`);

export const getRunFullContext = (runId: string) =>
  request<RunFullContext>(`/api/runs/${runId}/full`);

export const getRunDiff = (runId: string) =>
  request<DiffResult>(`/api/runs/${runId}/diff`);

export const triggerRun = () =>
  requestWithToast<{ status: string; detail: string }>('/api/runs/trigger', { method: 'POST' });

export const rescanRuns = () =>
  requestWithToast<{ status: string; imported: number }>('/api/runs/rescan', { method: 'POST' });

// --- Config ---

export const getConfig = () =>
  request<StationConfig>('/api/config');

export const updateConfig = (data: Record<string, unknown>) =>
  requestWithToast<StationConfig>('/api/config', {
    method: 'PUT', body: JSON.stringify(data),
  });

export const getUsage = () =>
  request<Record<string, unknown>>('/api/config/usage');

export const getTokenUsage = () =>
  request<TokenUsage>('/api/config/token-usage');

export const getPlanUsage = () =>
  request<PlanUsage>('/api/plan-usage');

export const testNotification = () =>
  requestWithToast<{ success: boolean; message?: string }>('/api/config/test-notification', { method: 'POST' });

// --- System ---

export const getSystemStatus = () =>
  request<SystemStatus>('/api/system/status');

export const serviceAction = (action: string, unit?: string) => {
  const q = unit ? `?unit=${encodeURIComponent(unit)}` : '';
  return requestWithToast<Record<string, unknown>>(`/api/system/service/${action}${q}`, { method: 'POST' });
};

export const getAuthStatus = () =>
  request<AuthStatus>('/api/system/auth');

// --- OAuth (Claude) ---

export const startOAuthLogin = () =>
  request<{ url: string; state: string }>('/api/oauth/start', { method: 'POST' });

export const submitOAuthCode = (code: string, state: string) =>
  request<{ success: boolean; error?: string }>('/api/oauth/callback', {
    method: 'POST', body: JSON.stringify({ code, state }),
  });

export const refreshOAuthToken = () =>
  request<{ refreshed: boolean; error?: string; expires_at?: string }>('/api/oauth/refresh', { method: 'POST' });

// --- GitHub auth: App + PAT ---

export interface GitHubAppStatus {
  state: 'not_created' | 'created_not_installed' | 'installed';
  slug?: string;
  name?: string;
  owner?: string;
  installation_id?: number;
  html_url?: string;
  pat_set: boolean;
}

export interface GitHubAppManifestStart {
  state: string;
  post_url: string;     // https://github.com/settings/apps/new?state=...
  manifest: Record<string, unknown>;
}

export const getGitHubAppStatus = () =>
  request<GitHubAppStatus>('/api/github/app/status');

export const startGitHubAppManifest = () =>
  request<GitHubAppManifestStart>('/api/github/app/manifest/start', { method: 'POST' });

export const disconnectGitHubApp = () =>
  requestWithToast<{ status: string }>('/api/github/app', { method: 'DELETE' });

export const setGitHubPAT = (token: string) =>
  requestWithToast<{ status: string }>('/api/github/app/pat', {
    method: 'PUT', body: JSON.stringify({ token }),
  });

export const clearGitHubPAT = () =>
  requestWithToast<{ status: string }>('/api/github/app/pat', { method: 'DELETE' });

export interface GitHubRepo {
  full_name: string;
  private: boolean;
  html_url: string;
  default_branch: string;
}

export interface GitHubBranch {
  name: string;
  protected: boolean;
}

export const listGitHubRepos = () =>
  request<{ repos: GitHubRepo[] }>('/api/github/app/repos');

export const listGitHubBranches = (repo: string) =>
  request<{ branches: GitHubBranch[] }>(
    `/api/github/app/branches?repo=${encodeURIComponent(repo)}`,
  );

// --- Plans ---

export const listPlans = (params?: {
  limit?: number; offset?: number; project_id?: number; status?: string;
}) =>
  request<PlanList>(`/api/plans${qs(params ?? {})}`);

export const getPlan = (id: number) =>
  request<Plan>(`/api/plans/${id}`);

export const deletePlan = (id: number) =>
  requestWithToast<void>(`/api/plans/${id}`, { method: 'DELETE' });

export const approvePlan = (id: number) =>
  requestWithToast<Plan>(`/api/plans/${id}/approve`, { method: 'POST' });

export const rejectPlan = (id: number) =>
  requestWithToast<Plan>(`/api/plans/${id}/reject`, { method: 'POST' });

export const implementPlan = (id: number) =>
  requestWithToast<Plan>(`/api/plans/${id}/implement`, { method: 'POST' });

// --- Logs ---

export const searchLogs = (q: string, runId?: string, limit?: number) =>
  request<{ results: Record<string, unknown>[]; total: number }>(
    `/api/logs/search${qs({ q, run_id: runId, limit })}`
  );

export const getRunLogs = (runId: string, limit?: number, offset?: number) =>
  request<{ lines: string[]; total: number; run_id: string }>(
    `/api/logs/${runId}${qs({ limit, offset })}`
  );

// --- Analytics ---

export const getAnalytics = (params?: { days?: number; project_id?: number }) =>
  request<AnalyticsResponse>(`/api/analytics${qs(params ?? {})}`);

export interface AutonomySummary {
  days: number;
  total_decisions: number;
  by_level: Record<string, number>;
  by_decision: Record<string, number>;
  by_tool: Record<string, number>;
  by_level_decision: Record<string, Record<string, number>>;
  by_event_type: Record<string, number>;
}

export interface AutonomyAuditRow {
  event_id: number;
  event_type: 'auto_mode_decision' | 'auto_mode_referral';
  workflow_id: string;
  run_id: string | null;
  agent_id: string;
  created_at: string | null;
  tool_name: string;
  decision: string;
  level: string | null;
  reason: string | null;
  request_id: string | null;
  tool_input: Record<string, unknown>;
}

export interface AutonomyAuditResponse {
  total: number;
  limit: number;
  offset: number;
  items: AutonomyAuditRow[];
}

export const getAutonomySummary = (days = 30) =>
  request<AutonomySummary>(`/api/analytics/autonomy${qs({ days })}`);

export const getAutonomyAudit = (params?: {
  run_id?: string;
  tool_name?: string;
  decision?: string;
  event_type?: string;
  limit?: number;
  offset?: number;
}) =>
  request<AutonomyAuditResponse>(`/api/analytics/autonomy-audit${qs(params ?? {})}`);

// --- Prompts ---

export const listPrompts = () =>
  request<PromptInfo[]>('/api/prompts');

export const getPrompt = (role: string) =>
  request<PromptInfo>(`/api/prompts/${role}`);

export const updatePrompt = (role: string, content: string) =>
  requestWithToast<PromptInfo>(`/api/prompts/${role}`, {
    method: 'PUT', body: JSON.stringify({ content }),
  });

export const resetPrompt = (role: string) =>
  requestWithToast<PromptInfo>(`/api/prompts/${role}`, { method: 'DELETE' });

// --- Queue ---

export const listQueue = (params?: {
  state?: string; project_repo?: string; run_id?: string;
  limit?: number; offset?: number;
}) =>
  request<QueueItemList>(`/api/queue${qs(params ?? {})}`);

export const getQueueItem = (id: number) =>
  request<QueueItem>(`/api/queue/${id}`);

export const getQueueStats = () =>
  request<QueueStats>('/api/queue/stats');

export const createQueueItem = (data: Record<string, unknown>) =>
  requestWithToast<QueueItem>('/api/queue', {
    method: 'POST', body: JSON.stringify(data),
  });

export const updateQueueItem = (id: number, data: Record<string, unknown>) =>
  requestWithToast<QueueItem>(`/api/queue/${id}`, {
    method: 'PUT', body: JSON.stringify(data),
  });

export const deleteQueueItem = (id: number) =>
  requestWithToast<void>(`/api/queue/${id}`, { method: 'DELETE' });

export const purgeQueue = (maxAgeDays?: number) =>
  requestWithToast<{ purged: number }>(`/api/queue/purge${qs({ max_age_days: maxAgeDays })}`, { method: 'POST' });

export const batchPauseQueue = (runId: string) =>
  requestWithToast<{ status: string; paused: number }>('/api/queue/batch-pause', {
    method: 'POST', body: JSON.stringify({ run_id: runId }),
  });

export const getBackpressure = () =>
  request<BackpressureStatus>('/api/queue/pressure');

// --- Coordinator ---

export const getCoordinatorTasks = (runId?: string) =>
  request<CoordinatorTask[]>(`/api/coordinator/tasks${qs({ run_id: runId })}`);

export const getCoordinatorDAG = (runId: string) =>
  request<CoordinatorDAG>(`/api/coordinator/dag/${runId}`);

export const getCoordinatorTaskDetails = (taskId: string) =>
  request<CoordinatorTask>(`/api/coordinator/tasks/${taskId}/details`);

export const getCoordinatorMessages = (runId?: string) =>
  request<CoordinatorMessage[]>(`/api/coordinator/messages${qs({ run_id: runId })}`);

export const sendGuidance = (data: GuidanceSend) =>
  requestWithToast<{ status: string }>('/api/coordinator/guidance', {
    method: 'POST', body: JSON.stringify(data),
  });

// --- Agent Events ---

export const getAgentEvents = (params?: {
  event_type?: string; agent_id?: string; run_id?: string; limit?: number;
}) =>
  request<AgentEvent[]>(`/api/agent-events${qs(params ?? {})}`);

export const getAgentEventStats = () =>
  request<{ by_type: Record<string, number>; total: number }>('/api/agent-events/stats/summary');

// --- Notifications ---

export const getNotifications = (params?: { unread_only?: boolean; limit?: number }) =>
  request<Notification[]>(`/api/events/subscribers${qs(params ?? {})}`);
