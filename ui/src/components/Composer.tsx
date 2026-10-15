import { useEffect, useState, type FormEvent, type KeyboardEvent } from "react";
import type { Config, Settings } from "../types";

interface Props {
  config: Config | null;
  running: boolean;
  onRun: (requirement: string, settings: Settings) => void;
  onCancel: () => void;
}

type NumberField = "temperature" | "max_iterations" | "passing_score" | "num_ctx" | "max_retrieved_files";

const NUMBER_FIELDS: { name: NumberField; label: string; min: number; max?: number; step?: number }[] = [
  { name: "temperature", label: "Temperature", min: 0, max: 2, step: 0.1 },
  { name: "max_iterations", label: "Max iterations", min: 1, max: 10 },
  { name: "passing_score", label: "Passing score", min: 0, max: 100 },
  { name: "num_ctx", label: "Context tokens", min: 2048, step: 1024 },
  { name: "max_retrieved_files", label: "Files per reviewer", min: 1, max: 50 },
];

export function Composer({ config, running, onRun, onCancel }: Props) {
  const [requirement, setRequirement] = useState("");
  const [open, setOpen] = useState(false);
  const [settings, setSettings] = useState<Settings>({});
  useEffect(() => {
    if (config) setSettings(config);
  }, [config]);

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    onRun(requirement.trim(), settings);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) submit();
  };

  const set = (name: keyof Settings, value: unknown) => setSettings((current) => ({ ...current, [name]: value }));

  return (
    <form className="composer" onSubmit={submit}>
      <textarea
        value={requirement}
        onChange={(e) => setRequirement(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Add a /health endpoint that reports DB status, with a test"
        aria-label="Request"
      />
      <div className="composer-row">
        <span className="hint">Ctrl+Enter to run</span>
        <button type="button" aria-expanded={open} aria-controls="settings" onClick={() => setOpen(!open)}>
          Settings
        </button>
        {running && (
          <button type="button" onClick={onCancel}>
            Stop
          </button>
        )}
        <button type="submit" className="primary" disabled={running}>
          Run
        </button>
      </div>
      {open && (
        <div className="settings" id="settings">
          <label>
            Model
            <input type="text" value={settings.model ?? ""} onChange={(e) => set("model", e.target.value)} />
          </label>
          {NUMBER_FIELDS.map((field) => (
            <label key={field.name}>
              {field.label}
              <input
                type="number"
                min={field.min}
                max={field.max}
                step={field.step}
                value={settings[field.name] ?? ""}
                onChange={(e) => set(field.name, e.target.value === "" ? undefined : Number(e.target.value))}
              />
            </label>
          ))}
          <label className="wide">
            Peer agents (A2A URLs, comma-separated)
            <input
              type="text"
              value={settings.a2a_peers ?? ""}
              placeholder="http://127.0.0.1:8001"
              onChange={(e) => set("a2a_peers", e.target.value)}
            />
          </label>
          <label className="wide">
            MCP servers file
            <input
              type="text"
              value={settings.mcp_config ?? ""}
              placeholder="~/.autocode/mcp.json"
              onChange={(e) => set("mcp_config", e.target.value)}
            />
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={Boolean(settings.fast_mode)}
              onChange={(e) => set("fast_mode", e.target.checked)}
            />
            Fast mode: one generation pass instead of one per dimension
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={Boolean(settings.skip_verify)}
              onChange={(e) => set("skip_verify", e.target.checked)}
            />
            Skip running tests
          </label>
        </div>
      )}
    </form>
  );
}
