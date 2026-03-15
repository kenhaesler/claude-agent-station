import type { Project, ProjectCreate, ProjectUpdate, Run, RunList, RunFullContext, ActiveEmployeeData, Plan, PlanList, SystemStatus, AuthStatus, LogSearchResult, RunLogs, UsageData, TokenUsageData, OAuthStartResponse, OAuthCallbackResponse, CoordinatorTask, CoordinatorTaskDetail, CoordinatorDAG, CoordinatorMessage, AnalyticsData, DiffResult, QueueItem, QueueItemList, QueueStats, IntelligenceInsights, IntelligenceDecision, BackpressureStatus, BrainstormSession, BrainstormSessionDetail } from './types';

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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);

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
        message = parsed.detail || body;
      } catch { message = body; }
      throw new Error(`${res.status}: ${message}`);
    }
    if (res.status === 204) return undefined as T;
    return res.json();
  } finally {
    clearTimeout(timeout);
  }
}

// Health
export const getHealth = () => request<{ status: string }>('/api/health');

// Projects
export const listProjects = () => request<Project[]>('/api/projects');
export const getProject = (id: number) => request<Project>(`/api/projects/${id}`);
export const createProject = (data: ProjectCreate) =>
  request<Project>('/api/projects', { method: 'POST', body: JSON.stringify(data) });
export const updateProject = (id: number, data: ProjectUpdate) =>
  request<Project>(`/api/projects/${id}`, { method: 'PUT', body: JSON.stringify(data) });
export const deleteProject = (id: number) =>
  request<void>(`/api/projects/${id}`, { method: 'DELETE' });

// Runs
export const listRuns = (params?: { limit?: number; offset?: number; project_id?: number; status?: string; verdict?: string; concurrent_group_id?: string }) => {
  const q = new URLSearchParams();
  if (params?.limit) q.set('limit', String(params.limit));
  if (params?.offset) q.set('offset', String(params.offset));
  if (params?.project_id) q.set('project_id', String(params.project_id));
  if (params?.status) q.set('status', params.status);
  if (params?.verdict) q.set('verdict', params.verdict);
  if (params?.concurrent_group_id) q.set('concurrent_group_id', params.concurrent_group_id);
  return request<RunList>(`/api/runs?${q}`);
};
export const getActiveEmployees = () => request<ActiveEmployeeData[]>('/api/runs/active-employees');
export const getLatestRun = () => request<Run>('/api/runs/latest');
export const getRun = (runId: string) => request<Run>(`/api/runs/${runId}`);
export const triggerRun = () => request<{ status: string; detail: string }>('/api/runs/trigger', { method: 'POST' });
export const getRunDiff = (runId: string) => request<DiffResult>(`/api/runs/${runId}/diff`);
export const getRunFullContext = (runId: string) => request<RunFullContext>(`/api/runs/${runId}/full`);
export const rescanRuns = () => request<{ status: string; imported: number }>('/api/runs/rescan', { method: 'POST' });

// Config
export const getConfig = () => request<Record<string, unknown>>('/api/config');
export const getUsage = () => request<UsageData>('/api/config/usage');
export const getTokenUsage = () => request<TokenUsageData>('/api/config/token-usage');
export const updateConfig = (data: Record<string, unknown>) =>
  request<Record<string, unknown>>('/api/config', { method: 'PUT', body: JSON.stringify(data) });
export const testNotification = () =>
  request<{ success: boolean; message?: string }>('/api/config/test-notification', { method: 'POST' });

// System
export const getSystemStatus = () => request<SystemStatus>('/api/system/status');
export const serviceAction = (action: string, unit?: string) => {
  const q = unit ? `?unit=${encodeURIComponent(unit)}` : '';
  return request<Record<string, unknown>>(`/api/system/service/${action}${q}`, { method: 'POST' });
};
export const getAuthStatus = () => request<AuthStatus>('/api/system/auth');

// OAuth
export const startOAuthLogin = () =>
  request<OAuthStartResponse>('/api/oauth/start', { method: 'POST' });
export const submitOAuthCode = (code: string, state: string) =>
  request<OAuthCallbackResponse>('/api/oauth/callback', {
    method: 'POST',
    body: JSON.stringify({ code, state }),
  });

// Plans
export const listPlans = (params?: { limit?: number; offset?: number; project_id?: number; status?: string }) => {
  const q = new URLSearchParams();
  if (params?.limit) q.set('limit', String(params.limit));
  if (params?.offset) q.set('offset', String(params.offset));
  if (params?.project_id) q.set('project_id', String(params.project_id));
  if (params?.status) q.set('status', params.status);
  return request<PlanList>(`/api/plans?${q}`);
};
export const getPlan = (id: number) => request<Plan>(`/api/plans/${id}`);
export const deletePlan = (id: number) =>
  request<void>(`/api/plans/${id}`, { method: 'DELETE' });
export const approvePlan = (id: number) =>
  request<Plan>(`/api/plans/${id}/approve`, { method: 'POST' });
export const rejectPlan = (id: number) =>
  request<Plan>(`/api/plans/${id}/reject`, { method: 'POST' });
export const implementPlan = (id: number) =>
  request<Plan>(`/api/plans/${id}/implement`, { method: 'POST' });

// Logs
export const searchLogs = (q: string, runId?: string, limit?: number) => {
  const params = new URLSearchParams({ q });
  if (runId) params.set('run_id', runId);
  if (limit) params.set('limit', String(limit));
  return request<{ results: LogSearchResult[]; total: number }>(`/api/logs/search?${params}`);
};
export const getRunLogs = (runId: string, limit?: number, offset?: number) => {
  const params = new URLSearchParams();
  if (limit) params.set('limit', String(limit));
  if (offset) params.set('offset', String(offset));
  return request<RunLogs>(`/api/logs/${runId}?${params}`);
};

// Analytics
export const getAnalytics = (params?: { days?: number; project_id?: number }) => {
  const q = new URLSearchParams();
  if (params?.days) q.set('days', String(params.days));
  if (params?.project_id) q.set('project_id', String(params.project_id));
  return request<AnalyticsData>(`/api/analytics?${q}`);
};

// Prompts
export interface PromptData {
  role: string;
  label: string;
  description: string;
  default_content: string;
  custom_content: string | null;
  has_override: boolean;
}
export const listPrompts = () => request<PromptData[]>('/api/prompts');
export const getPrompt = (role: string) => request<PromptData>(`/api/prompts/${role}`);
export const updatePrompt = (role: string, content: string) =>
  request<PromptData>(`/api/prompts/${role}`, { method: 'PUT', body: JSON.stringify({ content }) });
export const resetPrompt = (role: string) =>
  request<PromptData>(`/api/prompts/${role}`, { method: 'DELETE' });

// Queue
export const listQueue = (params?: { state?: string; project_repo?: string; run_id?: string; limit?: number; offset?: number }) => {
  const q = new URLSearchParams();
  if (params?.state) q.set('state', params.state);
  if (params?.project_repo) q.set('project_repo', params.project_repo);
  if (params?.run_id) q.set('run_id', params.run_id);
  if (params?.limit) q.set('limit', String(params.limit));
  if (params?.offset) q.set('offset', String(params.offset));
  return request<QueueItemList>(`/api/queue?${q}`);
};
export const getQueueItem = (id: number) => request<QueueItem>(`/api/queue/${id}`);
export const getQueueStats = () => request<QueueStats>('/api/queue/stats');
export const createQueueItem = (data: Record<string, unknown>) =>
  request<QueueItem>('/api/queue', { method: 'POST', body: JSON.stringify(data) });
export const updateQueueItem = (id: number, data: Record<string, unknown>) =>
  request<QueueItem>(`/api/queue/${id}`, { method: 'PUT', body: JSON.stringify(data) });
export const deleteQueueItem = (id: number) =>
  request<void>(`/api/queue/${id}`, { method: 'DELETE' });

// Coordinator
export const getCoordinatorTasks = (runId?: string) => {
  const q = new URLSearchParams();
  if (runId) q.set('run_id', runId);
  return request<CoordinatorTask[]>(`/api/coordinator/tasks?${q}`);
};
export const getCoordinatorDAG = (runId: string) =>
  request<CoordinatorDAG>(`/api/coordinator/dag/${runId}`);
export const getCoordinatorTaskDetails = (taskId: string) =>
  request<CoordinatorTaskDetail>(`/api/coordinator/tasks/${taskId}/details`);
export const getCoordinatorMessages = (runId?: string) => {
  const q = new URLSearchParams();
  if (runId) q.set('run_id', runId);
  return request<CoordinatorMessage[]>(`/api/coordinator/messages?${q}`);
};
export const sendGuidance = (data: { run_id: string; employee_index: number; guidance_type: string; content: string }) =>
  request<{ status: string }>('/api/coordinator/guidance', {
    method: 'POST',
    body: JSON.stringify(data),
  });

// Intelligence
export const getIntelligenceInsights = () => request<IntelligenceInsights>('/api/intelligence/insights');
export const getIntelligenceDecisions = (params?: { run_id?: string; limit?: number }) => {
  const q = new URLSearchParams();
  if (params?.run_id) q.set('run_id', params.run_id);
  if (params?.limit) q.set('limit', String(params.limit));
  return request<IntelligenceDecision[]>(`/api/intelligence/decisions?${q}`);
};
export const getBackpressure = () => request<BackpressureStatus>('/api/queue/pressure');

// Brainstorm
export const listBrainstormSessions = () => request<BrainstormSession[]>('/api/brainstorm/sessions');
export const createBrainstormSession = (data: { title?: string; project_id?: number; persona?: string }) =>
  request<BrainstormSession>('/api/brainstorm/sessions', { method: 'POST', body: JSON.stringify(data) });
export const getBrainstormSession = (id: string) => request<BrainstormSessionDetail>(`/api/brainstorm/sessions/${id}`);
export const deleteBrainstormSession = (id: string) =>
  request<void>(`/api/brainstorm/sessions/${id}`, { method: 'DELETE' });

/**
 * Send a message in a brainstorm session and return an EventSource-like reader
 * that yields SSE data events for streaming the AI response.
 */
export function streamBrainstormMessage(
  sessionId: string,
  content: string,
  onDelta: (text: string) => void,
  onDone: (fullContent: string, messageId: string) => void,
  onError: (error: string) => void,
): AbortController {
  const controller = new AbortController();
  const apiKey = getStoredApiKey();

  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (apiKey) headers['Authorization'] = `Bearer ${apiKey}`;

  fetch(`${BASE}/api/brainstorm/sessions/${sessionId}/messages`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ content }),
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok) {
        const text = await res.text();
        onError(`${res.status}: ${text}`);
        return;
      }
      const reader = res.body?.getReader();
      if (!reader) { onError('No response body'); return; }

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // Parse SSE lines
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const data = JSON.parse(line.slice(6));
            if (data.type === 'delta') {
              onDelta(data.text);
            } else if (data.type === 'done') {
              onDone(data.full_content, data.message_id);
            } else if (data.type === 'error') {
              onError(data.message);
            }
          } catch {
            // skip malformed lines
          }
        }
      }
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        onError(err.message || 'Stream failed');
      }
    });

  return controller;
}
