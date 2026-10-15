export type RunStatus = "running" | "done" | "error" | "cancelled";

export interface Issue {
  severity?: string;
  file?: string;
  description?: string;
  suggested_fix?: string;
}

export interface Review {
  persona: string;
  score?: number | string;
  status?: string;
  confidence?: string;
  issues?: Issue[];
  patches?: unknown[];
}

export interface DimensionPlan {
  dimension: string;
  applicable: boolean;
  reason: string;
  requirements: string[];
  decisions: string[];
  files: { path: string; change: string }[];
  acceptance_checks: string[];
  planning_error?: string;
}

export interface DimensionChange {
  dimension: string;
  step: number;
  total: number;
  changed: string[];
  skipped: boolean;
  summary: string;
}

export interface Plan {
  summary?: string;
  architecture?: string;
  files?: { path: string; purpose?: string }[];
  test_command?: string;
  dimensions?: DimensionPlan[];
}

export interface Metrics {
  llm_calls: number;
  prompt_tokens: number;
  output_tokens: number;
  llm_seconds: number;
}

export interface Config {
  model: string;
  ollama_host: string;
  temperature: number;
  num_ctx: number;
  max_iterations: number;
  passing_score: number;
  max_retrieved_files: number;
  skip_verify: boolean;
  command_timeout_seconds: number;
  mcp_config: string;
  a2a_peers: string;
  fast_mode: boolean;
}

export type Settings = Partial<Config>;

export interface Health {
  ollama: boolean;
  model: boolean;
  git: boolean;
  message: string;
}

export interface RunSummary {
  id: string;
  mode: string;
  requirement: string;
  workspace: string;
  status: RunStatus;
  passed: boolean;
  created_at: number;
  finished_at: number | null;
  metrics: Metrics;
}

export interface RunSnapshot extends RunSummary {
  plan: Plan;
  files: Record<string, string>;
  diffs: Record<string, string>;
  original_paths: string[];
}

export interface RunEvent {
  id: number;
  kind: string;
  data: Record<string, any>; // shape depends on kind; narrowed in state/run.ts
  ts: number;
}

export type Tone = "plain" | "muted" | "ok" | "bad";

export interface LogEntry {
  key: string;
  ts: number;
  tone: Tone;
  text: string;
  detail?: string;
  emphasis?: boolean;
}
