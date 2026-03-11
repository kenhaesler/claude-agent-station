import type { Project, ProjectCreate, ProjectUpdate, Run, RunList, ActiveEmployeeData, Plan, PlanList, SystemStatus, AuthStatus, LogSearchResult, RunLogs, UsageData, TokenUsageData, OAuthStartResponse, OAuthCallbackResponse, CoordinatorTask, CoordinatorDAG, CoordinatorMessage, AnalyticsData } from './types';

const BASE = import.meta.env.VITE_API_URL || '';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
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
export const rescanRuns = () => request<{ status: string; imported: number }>('/api/runs/rescan', { method: 'POST' });

// Config
export const getConfig = () => request<Record<string, unknown>>('/api/config');
export const getUsage = () => request<UsageData>('/api/config/usage');
export const getTokenUsage = () => request<TokenUsageData>('/api/config/token-usage');
export const updateConfig = (data: Record<string, unknown>) =>
  request<Record<string, unknown>>('/api/config', { method: 'PUT', body: JSON.stringify(data) });

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

// Coordinator
export const getCoordinatorTasks = (runId?: string) => {
  const q = new URLSearchParams();
  if (runId) q.set('run_id', runId);
  return request<CoordinatorTask[]>(`/api/coordinator/tasks?${q}`);
};
export const getCoordinatorDAG = (runId: string) =>
  request<CoordinatorDAG>(`/api/coordinator/dag/${runId}`);
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
