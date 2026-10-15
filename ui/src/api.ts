import type {
  Config,
  Health,
  MemoryItem,
  ProviderInfo,
  PullProgress,
  RunMode,
  RunSettings,
  RunSnapshot,
  RunSummary,
  Settings,
  StrategyStat,
} from "./types";

async function request<T>(path: string, body?: unknown): Promise<T> {
  const init: RequestInit =
    body === undefined
      ? {}
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data as T;
}

export const api = {
  config: () => request<{ config: Config; personas: string[] }>("/api/config"),
  health: () => request<Health>("/api/health"),
  workspace: (path: string) =>
    request<{ path: string; exists: boolean; files: Record<string, string> }>(
      `/api/workspace?path=${encodeURIComponent(path)}`,
    ),
  runs: () => request<{ runs: RunSummary[] }>("/api/runs"),
  run: (id: string) => request<RunSnapshot>(`/api/runs/${id}`),
  startRun: (requirement: string, workspace: string, settings: Partial<RunSettings>, mode: RunMode = "generate") =>
    request<RunSummary>("/api/runs", { requirement, workspace, settings, mode }),
  approve: (runId: string, id: string, approved: boolean) =>
    request<{ ok: boolean }>(`/api/runs/${runId}/approve`, { id, approved }),
  cancel: (id: string) => request<{ ok: boolean }>(`/api/runs/${id}/cancel`, {}),
  apply: (id: string, workspace: string) =>
    request<{ workspace: string; written: string[] }>(`/api/runs/${id}/apply`, { workspace }),
  events: (id: string) => new EventSource(`/api/runs/${id}/events`),
  settings: () => request<{ config: Config; providers: Record<string, ProviderInfo>; path: string }>("/api/settings"),
  saveSettings: (updates: Settings) =>
    request<{ config: Config; providers: Record<string, ProviderInfo>; path: string }>("/api/settings", updates),
  models: (provider: string) => request<{ models: string[] }>(`/api/models?provider=${encodeURIComponent(provider)}`),
  pull: (model: string) => request<PullProgress>("/api/models/pull", { model }),
  pulls: () => request<{ pulls: PullProgress[] }>("/api/models/pull"),
  memory: (q = "", limit = 100) =>
    request<{ backend: string; count: number; items: MemoryItem[] }>(
      `/api/memory?limit=${limit}&q=${encodeURIComponent(q)}`,
    ),
  deleteMemory: (id: number) => request<{ ok: boolean }>("/api/memory/delete", { id }),
  clearMemory: () => request<{ ok: boolean }>("/api/memory/clear", {}),
  learning: () =>
    request<{ backend: string; strategies: Record<string, string>; stats: StrategyStat[] }>("/api/learning"),
};

// Native folder picker, available only inside the desktop window (pywebview js_api).
interface DesktopBridge {
  pick_folder: () => Promise<string | null>;
}

export function desktopBridge(): DesktopBridge | null {
  const bridge = (window as unknown as { pywebview?: { api?: DesktopBridge } }).pywebview?.api;
  return bridge?.pick_folder ? bridge : null;
}
