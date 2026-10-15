import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import type { Config, ProviderInfo, Settings } from "../types";
import { ModelPicker } from "./ModelPicker";

interface Props {
  open: boolean;
  onClose: () => void;
  onSaved: (config: Config) => void;
}

interface ProviderDraft {
  type?: string;
  base_url?: string;
  api_key?: string;
}

const ROLE_FIELDS: { field: keyof Config; label: string; hint: string }[] = [
  { field: "planner_model", label: "Planning", hint: "Base plan and the ten dimension plans." },
  { field: "coder_model", label: "Coding", hint: "Foundation build, per-dimension changes and fixes." },
  { field: "reviewer_model", label: "Reviewing", hint: "Ten reviewers per round; a fast model keeps rounds short." },
  { field: "critic_model", label: "Reflection", hint: "Writes the lessons AutoCode learns after each run." },
];

const NUMBER_FIELDS: { field: keyof Config; label: string; min: number; max?: number; step?: number }[] = [
  { field: "max_iterations", label: "Review rounds", min: 1, max: 10 },
  { field: "passing_score", label: "Passing score", min: 0, max: 100 },
  { field: "temperature", label: "Temperature", min: 0, max: 2, step: 0.1 },
  { field: "num_ctx", label: "Context tokens (Ollama)", min: 2048, step: 1024 },
  { field: "max_retrieved_files", label: "Files per reviewer", min: 1, max: 50 },
  { field: "command_timeout_seconds", label: "Test timeout (s)", min: 5, max: 3600 },
  { field: "ollama_request_timeout_seconds", label: "Model timeout (s)", min: 10, max: 7200 },
];

function parseProviders(json: string): Record<string, ProviderDraft> {
  try {
    const value = JSON.parse(json || "{}");
    return value && typeof value === "object" ? value : {};
  } catch {
    return {};
  }
}

export function SettingsDialog({ open, onClose, onSaved }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [draft, setDraft] = useState<Settings>({});
  const [providers, setProviders] = useState<Record<string, ProviderInfo>>({});
  const [providerDraft, setProviderDraft] = useState<Record<string, ProviderDraft>>({});
  const [custom, setCustom] = useState({ name: "", base_url: "" });
  const [path, setPath] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      setError("");
      api
        .settings()
        .then((data) => {
          setConfig(data.config);
          setDraft(data.config);
          setProviders(data.providers);
          setProviderDraft(parseProviders(data.config.providers_json));
          setPath(data.path);
        })
        .catch((err) => setError(err instanceof Error ? err.message : String(err)));
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  const set = (field: keyof Config, value: unknown) => setDraft((current) => ({ ...current, [field]: value }));
  const setProvider = (name: string, field: keyof ProviderDraft, value: string) =>
    setProviderDraft((current) => ({
      ...current,
      [name]: { ...current[name], type: providers[name]?.type ?? "openai", [field]: value },
    }));

  const addCustom = () => {
    const name = custom.name.trim().toLowerCase().replace(/[^a-z0-9_-]/g, "");
    if (!name || !custom.base_url.trim()) {
      setError("A custom provider needs a name and a base URL.");
      return;
    }
    setProviderDraft((current) => ({ ...current, [name]: { type: "openai", base_url: custom.base_url.trim() } }));
    setProviders((current) => ({
      ...current,
      [name]: { type: "openai", base_url: custom.base_url.trim(), example: "", key_env: "", has_key: false },
    }));
    setCustom({ name: "", base_url: "" });
    setError("");
  };

  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!config) return;
    setSaving(true);
    setError("");
    try {
      const changed = Object.fromEntries(
        Object.entries(draft).filter(([key, value]) => config[key as keyof Config] !== value),
      ) as Settings;
      const providersJson = JSON.stringify(providerDraft);
      if (providersJson !== (config.providers_json || "{}")) changed.providers_json = providersJson;
      const saved = await api.saveSettings(changed);
      onSaved(saved.config);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <dialog ref={dialogRef} className="settings-dialog" onClose={onClose} aria-labelledby="settings-title">
      <form onSubmit={save}>
        <header className="dialog-head">
          <h2 id="settings-title">Settings</h2>
          <button type="button" onClick={onClose} aria-label="Close settings">
            Close
          </button>
        </header>
        {!config ? (
          <div className="empty">{error || "Loading…"}</div>
        ) : (
          <div className="dialog-body">
            <section>
              <h3>Models</h3>
              <p className="section-hint">
                Pick a local model (Ollama, LM Studio) or a hosted one. Each step can use its own model, for example a
                hosted planner with fast local reviewers.
              </p>
              <ModelPicker
                label="Default model"
                value={String(draft.model ?? "")}
                providers={providers}
                onChange={(spec) => set("model", spec)}
              />
              <div className="grid-2">
                {ROLE_FIELDS.map(({ field, label, hint }) => (
                  <ModelPicker
                    key={field}
                    label={label}
                    value={String(draft[field] ?? "")}
                    providers={providers}
                    onChange={(spec) => set(field, spec)}
                    optional
                    hint={hint}
                  />
                ))}
              </div>
            </section>

            <section>
              <h3>Providers</h3>
              <p className="section-hint">
                API keys are stored only on this computer ({path}) and are never shown again in full.
              </p>
              <div className="providers">
                {Object.entries(providers)
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([name, info]) => (
                    <div className="provider-row" key={name}>
                      <span className="provider-name">{name}</span>
                      <input
                        type="url"
                        aria-label={`${name} base URL`}
                        value={providerDraft[name]?.base_url ?? info.base_url}
                        placeholder="Default endpoint"
                        onChange={(e) => setProvider(name, "base_url", e.target.value)}
                        spellCheck={false}
                      />
                      {info.type === "ollama" ? (
                        <span className="field-hint">No key needed</span>
                      ) : (
                        <input
                          type="password"
                          aria-label={`${name} API key`}
                          value={providerDraft[name]?.api_key ?? ""}
                          placeholder={info.has_key ? "Key saved" : info.key_env ? `Paste key or set ${info.key_env}` : "API key (optional)"}
                          onChange={(e) => setProvider(name, "api_key", e.target.value)}
                          autoComplete="off"
                        />
                      )}
                    </div>
                  ))}
              </div>
              <div className="provider-row add">
                <input
                  type="text"
                  aria-label="Custom provider name"
                  placeholder="Custom name, e.g. vllm"
                  value={custom.name}
                  onChange={(e) => setCustom({ ...custom, name: e.target.value })}
                />
                <input
                  type="url"
                  aria-label="Custom provider base URL"
                  placeholder="OpenAI-compatible URL, e.g. http://gpu-box:8000/v1"
                  value={custom.base_url}
                  onChange={(e) => setCustom({ ...custom, base_url: e.target.value })}
                />
                <button type="button" onClick={addCustom}>
                  Add provider
                </button>
              </div>
            </section>

            <section>
              <h3>Memory and learning</h3>
              <label className="check">
                <input type="checkbox" checked={Boolean(draft.learning)} onChange={(e) => set("learning", e.target.checked)} />
                Learn from every run: remember lessons and pick the strategies that score best
              </label>
              <ModelPicker
                label="Embedding model"
                value={String(draft.embedding_model ?? "")}
                providers={providers}
                onChange={(spec) => set("embedding_model", spec)}
                hint="Used to find lessons from similar past tasks. If it is unavailable, AutoCode falls back to keyword matching."
              />
              <label>
                Memory database
                <input
                  type="text"
                  value={String(draft.database_url ?? "")}
                  placeholder="Empty: a local file. Or postgresql://user:pass@host:5432/autocode (pgvector)"
                  onChange={(e) => set("database_url", e.target.value)}
                  spellCheck={false}
                />
              </label>
            </section>

            <section>
              <h3>Pipeline</h3>
              <div className="grid-3">
                {NUMBER_FIELDS.map(({ field, label, min, max, step }) => (
                  <label key={field}>
                    {label}
                    <input
                      type="number"
                      min={min}
                      max={max}
                      step={step}
                      value={String(draft[field] ?? "")}
                      onChange={(e) => set(field, e.target.value === "" ? undefined : Number(e.target.value))}
                    />
                  </label>
                ))}
              </div>
              <label className="check">
                <input type="checkbox" checked={Boolean(draft.fast_mode)} onChange={(e) => set("fast_mode", e.target.checked)} />
                Fast mode by default
              </label>
              <label className="check">
                <input type="checkbox" checked={Boolean(draft.skip_verify)} onChange={(e) => set("skip_verify", e.target.checked)} />
                Skip running tests by default
              </label>
            </section>

            <section>
              <h3>Connected agents and tools</h3>
              <label>
                Peer agents (A2A URLs, comma-separated)
                <input
                  type="text"
                  value={String(draft.a2a_peers ?? "")}
                  placeholder="http://127.0.0.1:8001"
                  onChange={(e) => set("a2a_peers", e.target.value)}
                />
              </label>
              <label>
                MCP servers file
                <input
                  type="text"
                  value={String(draft.mcp_config ?? "")}
                  placeholder="~/.autocode/mcp.json"
                  onChange={(e) => set("mcp_config", e.target.value)}
                  spellCheck={false}
                />
              </label>
            </section>
          </div>
        )}
        <footer className="dialog-foot">
          {error && (
            <span className="error-text" role="alert">
              {error}
            </span>
          )}
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="primary" disabled={!config || saving}>
            {saving ? "Saving…" : "Save settings"}
          </button>
        </footer>
      </form>
    </dialog>
  );
}
