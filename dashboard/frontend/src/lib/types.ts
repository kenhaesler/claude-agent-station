export interface Project {
  id: number;
  repo: string;
  priority: string;
  mode: string;
  enabled: boolean;
  branch: string;
  custom_instructions: string | null;
  setup_script: string | null;
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
}

export interface ProjectUpdate {
  priority?: string;
  mode?: string;
  enabled?: boolean;
  branch?: string;
  custom_instructions?: string | null;
  setup_script?: string | null;
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
  employee_index: number | null;
  concurrent_group_id: string | null;
}

export interface RunList {
  runs: Run[];
  total: number;
}

export interface ActiveEmployeeData {
  run_id: string;
  project_id: number;
  mode: string;
  status: string;
  issue_number: number | null;
  turns: number | null;
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
  session_limit_24h: number;
  threshold: number;
  max_session_percent: number;
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
    limit: number;
    effective_limit: number;
    usage_percent: number;
  };
  monthly: {
    tokens_total: number;
    limit: number;
    effective_limit: number;
    usage_percent: number;
  };
  token_reserve_percent: number;
  can_spawn_employee: boolean;
}

export interface StationConfig {
  projects?: any[];
  _mode_options?: Record<string, string>;
  schedule?: string;
  models?: {
    employee?: string;
    manager?: string;
  };
  limits?: {
    max_employee_turns?: number;
    max_analyst_turns?: number;
    max_manager_turns?: number;
    token_limit_daily?: number;
    token_limit_monthly?: number;
    token_reserve_percent?: number;
    session_limit_24h?: number;
    max_session_percent?: number;
    max_concurrent_employees?: number;
    max_employees_per_project?: number;
    token_budget_strategy?: string;
  };
  notifications?: {
    enabled?: boolean;
    method?: string;
    notification_file?: string;
  };
  logging?: {
    log_dir?: string;
    digest_dir?: string;
  };
}

export interface OAuthStartResponse {
  auth_url: string;
  state: string;
}

export interface OAuthCallbackResponse {
  success: boolean;
  error?: string;
}

export interface ToastMessage {
  id: number;
  type: 'success' | 'error' | 'info';
  text: string;
}
