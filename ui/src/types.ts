export type RunStatus = "running" | "done" | "error" | "cancelled";
export type RunMode = "generate" | "agent";

export interface Approval {
  id: string;
  tool: string;
  summary: string;
  args: string;
}

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
  providers_json: string;
  planner_model: string;
  coder_model: string;
  reviewer_model: string;
  critic_model: string;
  embedding_model: string;
  database_url: string;
  learning: boolean;
  ollama_request_timeout_seconds: number;
}

/** Fields a single run may override; the rest change only through saved settings. */
export type RunSettings = Pick<
  Config,
  "temperature" | "max_iterations" | "passing_score" | "num_ctx" | "max_retrieved_files" | "skip_verify" | "fast_mode"
>;

export interface ProviderInfo {
  type: string;
  base_url: string;
  example: string;
  key_env: string;
  has_key: boolean;
}

export interface ModelStatus {
  spec: string;
  provider: string;
  model: string;
  ok: boolean;
  message: string;
  pullable: boolean;
  roles?: string[];
}

export interface PullProgress {
  spec: string;
  status: string;
  completed: number;
  total: number;
  done: boolean;
  error: string;
}

export interface MemoryItem {
  id: number;
  kind: string;
  dimension: string;
  step: string;
  content: string;
  requirement: string;
  score: number | null;
  created_at: string;
  similarity: number;
}

export interface StrategyStat {
  dimension: string;
  arm: string;
  pulls: number;
  mean_reward: number | null;
  expected: number;
}

export type Settings = Partial<Config>;

export interface Lesson {
  dimension: string;
  step: string;
  lesson: string;
}

export interface Health {
  ok: boolean;
  git: boolean;
  models: ModelStatus[];
  embedding: ModelStatus | null;
  memory: { backend: string; count: number; ok: boolean; message?: string };
  learning: boolean;
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
