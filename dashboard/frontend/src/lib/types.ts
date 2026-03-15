export interface Project {
  id: number;
  repo: string;
  priority: string;
  mode: string;
  enabled: boolean;
  branch: string;
  custom_instructions: string | null;
  setup_script: string | null;
  security_review_enabled: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface ProjectCreate {
  repo: string;
  priority?: string;
  mode?: string;
  enabled?: boolean;
  branch?: string;
  custom_instructions?: string | null;
  setup_script?: string | null;
  security_review_enabled?: boolean;
}

export interface ProjectUpdate {
  priority?: string;
  mode?: string;
  enabled?: boolean;
  branch?: string;
  custom_instructions?: string | null;
  setup_script?: string | null;
  security_review_enabled?: boolean;
}

export interface Run {
  id: number;
  run_id: string;
  project_id: number | null;
  mode: string | null;
  model: string | null;
  status: string | null;
  verdict: string | null;
  issue_number: number | null;
  branch: string | null;
  cost_usd: number | null;  // Deprecated: kept for historical data
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
}

export interface RunList {
  runs: Run[];
  total: number;
}

export interface ActiveEmployeeData {
  run_id: string;
  project_id: number | null;
  mode: string;
  status: string;
  issue_number: number | null;
  turns: number | null;
  employee_index: number | null;
  concurrent_group_id: string | null;
  model: string | null;
  branch: string | null;
}

export interface Plan {
  id: number;
  project_id: number;
  issue_number: number | null;
  issue_title: string | null;
  title: string;
  description: string | null;
  steps: string | null;
  estimated_scope: string | null;
  files_affected: string | null;
  status: string;
  run_id: string | null;
  implementation_run_id: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface PlanList {
  plans: Plan[];
  total: number;
}

export interface SystemStatus {
  service: { active: boolean };
  timer: { active: boolean; next_trigger: string | null };
  resources: {
    memory_total_mb: number | null;
    memory_used_mb: number | null;
    memory_available_mb: number | null;
    load_avg: number[] | null;
    disk_total_gb: number | null;
    disk_free_gb: number | null;
    disk_used_gb: number | null;
    uptime_seconds: number | null;
  };
}

export interface AuthStatus {
  logged_in: boolean;
  expired: boolean;
  expires_at: string | null;
  error?: string;
}

export interface LogSearchResult {
  file: string;
  line: number;
  content: string;
}

export interface RunLogs {
  run_id: string;
  lines: Record<string, unknown>[];
  total: number;
}

export interface UsageData {
  sessions_used: number;
  max_usage_percent: number;
  plan_limit: number;
  window_start_ts: number;
  window_remaining_hours: number;
  usage_percent: number;
  last_run_ts: number;
}

export interface TokenUsageData {
  daily: {
    tokens_input: number;
    tokens_output: number;
    tokens_total: number;
  };
  monthly: {
    tokens_total: number;
  };
  max_usage_percent: number;
  reserve_percent: number;
}

export interface PlanUsageModelData {
  model: string;
  tokens_used: number;
  tokens_limit: number;
  usage_percent: number;
}

export interface PlanUsageData {
  timestamp: string;
  detection_method: string;
  plan_tier: string;
  session_tokens_used: number;
  session_tokens_limit: number;
  session_usage_percent: number;
  weekly_tokens_used: number;
  weekly_tokens_limit: number;
  weekly_usage_percent: number;
  weekly_reset_at: string;
  per_model: PlanUsageModelData[];
  is_throttled: boolean;
  should_throttle: boolean;
  throttle_reason: string;
  error: string | null;
}

export interface StationConfig {
  projects?: any[];
  _mode_options?: Record<string, string>;
  schedule?: string;
  models?: {
    employee?: string;
    manager?: string;
    analyst?: string;
    planner?: string;
    router?: string;
  };
  limits?: {
    max_usage_percent?: number;
    reserve_percent?: number;
    max_employee_turns?: number;
    max_analyst_turns?: number;
    max_planner_turns?: number;
    max_manager_turns?: number;
    max_fix_turns?: number;
    max_triage_turns?: number;
    max_review_turns?: number;
    max_concurrent_employees?: number;
    max_employees_per_project?: number;
    token_budget_strategy?: string;
  };
  intelligence?: {
    auto_mode_selection?: boolean;
    progressive_deepening?: boolean;
    confidence_gating?: boolean;
    independent_verification?: boolean;
    adaptive_scheduling?: boolean;
    work_stealing?: boolean;
  };
  notifications?: {
    enabled?: boolean;
    method?: string;
    notification_file?: string;
    webhook_url?: string;
    webhook_type?: string;
    notify_on?: string[];
    dashboard_url?: string;
    telegram_chat_id?: string;
  };
  logging?: {
    log_dir?: string;
    digest_dir?: string;
  };
}

// --- Agent Events ---

export interface AgentEvent {
  event_id: number;
  workflow_id: string;
  run_id: string | null;
  agent_id: string;
  event_type: string;
  event_data: string;
  parent_event_id: number | null;
  created_at: string | null;
}

// --- Task Outcomes ---

export interface TaskOutcome {
  id: number;
  queue_item_id: number | null;
  project_repo: string;
  issue_number: number | null;
  issue_type: string | null;
  complexity_score: number | null;
  mode_used: string;
  model_used: string;
  escalation_rung: number;
  prompt_version: number;
  confidence_reported: number | null;
  success: boolean;
  tests_passed: boolean | null;
  verdict: string | null;
  failure_category: string | null;
  tokens_consumed: number | null;
  duration_seconds: number | null;
  created_at: string | null;
}

// --- Backpressure ---

export interface BackpressureStatus {
  level: string;
  usage_percent: number;
  max_concurrent: number;
  effective_concurrent: number;
  model_restriction: string | null;
  turn_cap: number | null;
}

export interface OAuthStartResponse {
  auth_url: string;
  state: string;
}

export interface OAuthCallbackResponse {
  success: boolean;
  error?: string;
}

export interface GitHubDeviceStartResponse {
  flow_id: string;
  user_code: string;
  verification_uri: string;
  expires_in: number;
}

export interface GitHubDevicePollResponse {
  status: 'pending' | 'complete' | 'expired' | 'error';
  username?: string;
  error?: string;
}

export interface GitHubOAuthStatusResponse {
  connected: boolean;
  username?: string;
  scopes?: string[];
  error?: string;
}

export interface ToastMessage {
  id: number;
  type: 'success' | 'error' | 'info';
  text: string;
}

// --- Diff Viewer ---

export interface DiffLine {
  type: 'add' | 'remove' | 'context';
  content: string;
  old_line: number | null;
  new_line: number | null;
}

export interface DiffHunk {
  header: string;
  old_start: number;
  old_count: number;
  new_start: number;
  new_count: number;
  lines: DiffLine[];
}

export interface DiffFile {
  filename: string;
  old_filename: string | null;
  additions: number;
  deletions: number;
  is_new: boolean;
  is_deleted: boolean;
  is_binary: boolean;
  hunks: DiffHunk[];
}

export interface DiffResult {
  files: DiffFile[];
  total_additions: number;
  total_deletions: number;
  total_files: number;
}

// --- Queue ---

export interface QueueItem {
  id: number;
  project_repo: string;
  issue_number: number | null;
  issue_title: string | null;
  state: string;
  priority: number;
  assigned_to: number | null;
  run_id: string | null;
  employee_report: string | null;
  manager_feedback: string | null;
  retry_count: number;
  max_retries: number;
  context: string | null;
  error_message: string | null;
  mode: string | null;
  complexity_score: number | null;
  escalation_rung: number;
  escalated_from: number | null;
  parent_task_id: string | null;
  confidence: number | null;
  handoff_context: string | null;
  created_at: string | null;
  updated_at: string | null;
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

// --- Coordinator ---

export interface CoordinatorTask {
  id: string;
  run_id: string;
  project_repo: string;
  issue_number: number | null;
  title: string;
  description: string | null;
  status: string;  // pending/ready/running/completed/failed/blocked
  employee_index: number | null;
  depends_on: string | null;  // JSON array of task IDs
  workspace: string | null;
  expected_files: string | null;
  touched_files: string | null;
  exit_code: number | null;
  error_message: string | null;
  result_summary: string | null;
  log_path: string | null;
  branch: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface CoordinatorTaskDetail extends CoordinatorTask {
  employee_report: Record<string, unknown> | null;
  log_excerpt: string | null;
}

export interface CoordinatorDAG {
  run_id: string;
  project_repo: string;
  tasks: CoordinatorTask[];
  summary: Record<string, number>;
}

export interface CoordinatorMessage {
  id: number;
  run_id: string;
  task_id: string | null;
  direction: string;
  message_type: string;
  content: string;
  employee_index: number | null;
  created_at: string | null;
}

// --- Analytics ---

export interface DailyTokenUsage {
  date: string;
  tokens_total: number;
  tokens_input: number;
  tokens_output: number;
  run_count: number;
}

export interface VerdictDistribution {
  verdict: string;
  count: number;
}

export interface ProjectTokenUsage {
  project_id: number;
  project_repo: string;
  tokens_total: number;
  tokens_input: number;
  tokens_output: number;
  run_count: number;
}

export interface DailyRunCount {
  date: string;
  total: number;
  success: number;
  failed: number;
}

/** Unified run context returned by GET /api/runs/{id}/full */
export interface RunFullContext {
  run: Run;
  coordinator_tasks: CoordinatorTask[];
  coordinator_messages: CoordinatorMessage[];
  queue_item: QueueItem | null;
  plan: Plan | null;
  project_repo: string | null;
  intelligence_decisions: AgentEvent[];
}

// --- Intelligence ---

export interface IntelligenceInsights {
  success_rates: ModeSuccessRate[];
  calibration: ConfidenceBucket[];
  token_efficiency: TokenEfficiency[];
  escalation_stats: EscalationStat[];
  total_samples: number;
  intelligence_event_count: number;
}

export interface ModeSuccessRate {
  mode: string;
  model: string;
  total: number;
  successes: number;
  success_rate: number;
  avg_tokens: number | null;
  avg_duration: number | null;
}

export interface ConfidenceBucket {
  bucket: string;
  total: number;
  actual_success_rate: number;
  avg_reported_confidence: number;
}

export interface TokenEfficiency {
  mode: string;
  avg_tokens_success: number | null;
  avg_tokens_failure: number | null;
  total: number;
}

export interface EscalationStat {
  rung: number;
  total: number;
  success_rate: number;
}

export interface IntelligenceDecision {
  event_id: number;
  workflow_id: string;
  run_id: string | null;
  event_type: string;
  event_data: string;
  created_at: string | null;
}

// --- Brainstorm ---

export interface BrainstormSession {
  id: string;
  project_id: number | null;
  title: string | null;
  persona: string;
  created_at: string | null;
  updated_at: string | null;
  message_count: number;
  project_repo: string | null;
}

export interface BrainstormMessage {
  id: string;
  session_id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string | null;
}

export interface BrainstormSessionDetail extends BrainstormSession {
  messages: BrainstormMessage[];
}

export interface AnalyticsData {
  days: number;
  total_tokens: number;
  total_tokens_input: number;
  total_tokens_output: number;
  total_runs: number;
  failed_runs: number;
  daily_token_usage: DailyTokenUsage[];
  verdict_distribution: VerdictDistribution[];
  project_token_usage: ProjectTokenUsage[];
  daily_run_counts: DailyRunCount[];
}
