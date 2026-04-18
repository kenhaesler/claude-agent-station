// ============================================
// ORBITAL COMMAND — Type Definitions
// Mirrors backend Pydantic schemas
// ============================================

// --- Enums ---
export type AgentMode = 'full' | 'analyze' | 'plan' | 'triage' | 'review' | 'fix';
export type RunStatus = 'started' | 'employee_done' | 'reviewing' | 'finished' | 'verdict' | 'plan_reviewing' | 'plan_review_done' | string;
export type Verdict = 'APPROVE' | 'PR' | 'REJECT';
export type QueueState = 'pending' | 'assigned' | 'claimed' | 'planning' | 'in_progress' | 'review' | 'verifying' | 'approved' | 'rejected' | 'escalated' | 'paused' | 'failed' | 'cancelled' | 'completed';
export type PlanStatus = 'draft' | 'approved' | 'implementing' | 'completed' | 'rejected';
export type CoordinatorTaskStatus = 'pending' | 'ready' | 'running' | 'completed' | 'blocked' | 'failed';
export type BackpressureLevel = 'GREEN' | 'YELLOW' | 'RED' | 'BLACK';

// --- Projects ---
export interface Project {
  id: number;
  repo: string;
  priority: 'low' | 'medium' | 'high' | 'critical';
  mode: AgentMode;
  enabled: boolean;
  branch: string;
  custom_instructions: string | null;
  setup_script: string | null;
  security_review_enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProjectCreate {
  repo: string;
  priority?: string;
  mode?: AgentMode;
  enabled?: boolean;
  branch?: string;
  custom_instructions?: string;
  setup_script?: string;
  security_review_enabled?: boolean;
}

// --- Runs ---
export interface Run {
  id: number;
  run_id: string;
  project_id: number | null;
  mode: AgentMode | null;
  model: string | null;
  status: RunStatus;
  verdict: Verdict | null;
  issue_number: number | null;
  branch: string | null;
  cost_usd: number | null;
  tokens_input: number | null;
  tokens_output: number | null;
  tokens_total: number | null;
  turns: number | null;
  duration_ms: number | null;
  started_at: string | null;
  finished_at: string | null;
  employee_report: string | null;
  verdict_detail: string | null;
  log_file: string | null;
  trace_id: string | null;
  employee_index: number | null;
  concurrent_group_id: string | null;
  team_name: string | null;
  team_members: string | null;
}

export interface RunList {
  runs: Run[];
  total: number;
}

export interface ActiveEmployee {
  run_id: string;
  project_id: number | null;
  mode: string | null;
  status: string;
  issue_number: number | null;
  turns: number | null;
  employee_index: number | null;
  concurrent_group_id: string | null;
  model: string | null;
  branch: string | null;
  tokens_total?: number | null;
  started_at?: string | null;
}

export interface TeammateStatus {
  agent_id: string;
  name: string;
  task_id: string | null;
  issue_number: number | null;
  status: 'spawned' | 'planning' | 'implementing' | 'completed' | 'stuck';
  turns_used: number | null;
  tokens_used: number | null;
  files_touched: string[] | null;
}

export interface TeamSummary {
  team_name: string;
  lead_agent_id: string;
  teammates: TeammateStatus[];
  tasks_total: number;
  tasks_completed: number;
  tasks_in_progress: number;
  conflicts: number;
}

export interface RunFullContext {
  run: Run;
  coordinator_tasks: CoordinatorTask[];
  coordinator_messages: CoordinatorMessage[];
  queue_item: QueueItem | null;
  plan: Plan | null;
  project_repo: string | null;
  intelligence_decisions: AgentEvent[];
  team_summary: TeamSummary | null;
}

export interface DiffHunk {
  old_start: number;
  old_count: number;
  new_start: number;
  new_count: number;
  lines: DiffLine[];
}

export interface DiffLine {
  type: 'add' | 'delete' | 'context';
  content: string;
  old_line: number | null;
  new_line: number | null;
}

export interface DiffFile {
  path: string;
  status: 'added' | 'modified' | 'deleted' | 'renamed';
  old_path?: string;
  hunks: DiffHunk[];
  additions: number;
  deletions: number;
}

export interface DiffResult {
  files: DiffFile[];
  total_additions: number;
  total_deletions: number;
}

// --- Queue ---
export interface QueueItem {
  id: number;
  project_repo: string;
  issue_number: number | null;
  issue_title: string | null;
  state: QueueState;
  priority: number;
  assigned_to: string | null;
  run_id: string | null;
  employee_report: string | null;
  manager_feedback: string | null;
  retry_count: number;
  max_retries: number;
  context: string | null;
  error_message: string | null;
  mode: AgentMode | null;
  complexity_score: number | null;
  escalation_rung: number;
  escalated_from: number | null;
  parent_task_id: number | null;
  confidence: number | null;
  handoff_context: string | null;
  created_at: string;
  updated_at: string;
  assigned_at: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface QueueItemList {
  items: QueueItem[];
  total: number;
}

export interface QueueStats {
  by_state: Record<string, number>;
  total: number;
  avg_time_to_complete_ms: number | null;
}

export interface BackpressureStatus {
  level: BackpressureLevel;
  usage_percent: number;
  max_concurrent: number;
  effective_concurrent: number;
  model_restriction: string | null;
  turn_cap: number | null;
}

// --- Plans ---
export interface Plan {
  id: number;
  project_id: number | null;
  issue_number: number | null;
  issue_title: string | null;
  title: string;
  description: string | null;
  steps: string | null;
  estimated_scope: string | null;
  files_affected: string | null;
  status: PlanStatus;
  run_id: string | null;
  implementation_run_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface PlanList {
  plans: Plan[];
  total: number;
}

// --- Coordinator ---
export interface CoordinatorTask {
  id: string;
  run_id: string;
  project_repo: string | null;
  issue_number: number | null;
  title: string | null;
  description: string | null;
  status: CoordinatorTaskStatus;
  employee_index: number | null;
  depends_on: string | null;
  workspace: string | null;
  expected_files: string | null;
  touched_files: string | null;
  exit_code: number | null;
  error_message: string | null;
  result_summary: string | null;
  log_path: string | null;
  branch: string | null;
  dag_json: string | null;
  teammate_agent_id: string | null;
  claimed_by: string | null;
  claimed_at: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface CoordinatorMessage {
  id: number;
  run_id: string;
  task_id: string | null;
  direction: 'to_employee' | 'from_monitor' | 'system';
  message_type: 'guidance' | 'conflict' | 'progress' | 'error';
  content: string | null;
  employee_index: number | null;
  created_at: string;
}

export interface CoordinatorDAG {
  run_id: string;
  project_repo: string | null;
  tasks: CoordinatorTask[];
  summary: Record<string, unknown>;
}

export interface GuidanceSend {
  run_id: string;
  employee_index: number;
  guidance_type: 'info' | 'warning' | 'redirect' | 'stop';
  content: string;
  workspace?: string;
}

// --- Agent Events ---
export interface AgentEvent {
  event_id: string;
  workflow_id: string;
  run_id: string | null;
  agent_id: string | null;
  event_type: string;
  event_data: Record<string, unknown>;
  parent_event_id: string | null;
  team_name: string | null;
  created_at: string;
}

// --- Notifications ---
export interface Notification {
  id: number;
  run_id: string | null;
  type: 'approve' | 'reject' | 'pr' | 'error' | 'info';
  message: string;
  read: boolean;
  created_at: string;
}

// --- Config ---
export interface StationConfig {
  models: { employee: string; manager: string };
  limits: {
    max_employee_turns: number;
    max_employee_budget: number;
    max_manager_turns: number;
    max_usage_percent: number;
    reserve_percent: number;
    max_concurrent_employees: number;
  };
  schedule: { interval: string };
  notifications: { enabled: boolean; targets: NotificationTarget[] };
  logging: Record<string, unknown>;
  intelligence: Record<string, unknown>;
  integration: Record<string, unknown>;
  sprint: Record<string, unknown>;
}

export interface NotificationTarget {
  type: 'generic' | 'slack' | 'discord' | 'telegram';
  url: string;
  notify_on: string[];
}

export interface TokenUsage {
  daily: { tokens_input: number; tokens_output: number; tokens_total: number };
  monthly: { tokens_total: number };
  max_usage_percent: number;
  reserve_percent: number;
}

// --- System ---
export interface SystemStatus {
  service: { active: boolean; status: string };
  timer: { active: boolean; next: string | null };
  resources: {
    memory_total_mb?: number;
    memory_available_mb?: number;
    memory_used_mb?: number;
    load_avg?: number[];
    disk_total_gb?: number;
    disk_free_gb?: number;
    disk_used_gb?: number;
    uptime_seconds?: number;
  };
}

export interface AuthStatus {
  logged_in: boolean;
  expired: boolean;
  expires_at: string | null;
  remaining_seconds: number | null;
  auto_refresh_available: boolean;
  error: string | null;
}

// --- Plan Usage ---
export interface PlanUsage {
  timestamp: string;
  detection_method: string;
  plan_tier: string;
  session_tokens_used: number | null;
  session_tokens_limit: number | null;
  session_tokens_percent: number | null;
  weekly_tokens_used: number | null;
  weekly_tokens_limit: number | null;
  weekly_tokens_percent: number | null;
  weekly_reset_at: string | null;
  per_model: ModelUsage[];
  is_throttled: boolean;
  should_throttle: boolean;
  throttle_reason: string | null;
  error: string | null;
}

export interface ModelUsage {
  model: string;
  tokens_used: number;
  tokens_limit: number;
  usage_percent: number;
}

// --- Analytics ---
export interface AnalyticsResponse {
  days: number;
  total_tokens: number;
  total_tokens_input: number;
  total_tokens_output: number;
  total_runs: number;
  failed_runs: number;
  daily_token_usage: { date: string; tokens_total: number; tokens_input: number; tokens_output: number; run_count: number }[];
  verdict_distribution: { verdict: string; count: number }[];
  project_token_usage: { project_id: number; project_repo: string; tokens_total: number; run_count: number }[];
  daily_run_counts: { date: string; total: number; success: number; failed: number }[];
}

// --- Prompts ---
export interface PromptInfo {
  role: string;
  label: string;
  description: string;
  default_content: string;
  custom_content: string | null;
  has_override: boolean;
}

// --- SSE Event ---
export interface SSEEvent {
  type: string;
  data: Record<string, unknown>;
}
