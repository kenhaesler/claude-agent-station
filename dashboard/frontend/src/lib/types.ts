export interface Project {
  id: number;
  repo: string;
  priority: string;
  mode: string;
  enabled: boolean;
  branch: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface ProjectCreate {
  repo: string;
  priority?: string;
  mode?: string;
  enabled?: boolean;
  branch?: string;
}

export interface ProjectUpdate {
  priority?: string;
  mode?: string;
  enabled?: boolean;
  branch?: string;
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
  cost_usd: number | null;
  turns: number | null;
  duration_ms: number | null;
  started_at: string | null;
  finished_at: string | null;
  employee_report: string | null;
  verdict_detail: string | null;
  log_file: string | null;
}

export interface RunList {
  runs: Run[];
  total: number;
}

export interface SystemStatus {
  service: { active: boolean };
  timer: { active: boolean; next_trigger: string | null };
  resources: {
    memory_mb: number | null;
    load_avg: number[] | null;
    disk_free_gb: number | null;
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

export interface ToastMessage {
  id: number;
  type: 'success' | 'error' | 'info';
  text: string;
}
