import { useEffect, useState, type FormEvent, type KeyboardEvent, type RefObject } from "react";
import type { Config, RunSettings } from "../types";

interface Props {
  config: Config | null;
  running: boolean;
  onRun: (requirement: string, settings: Partial<RunSettings>) => void;
  onCancel: () => void;
  requirement: string;
  onRequirement: (text: string) => void;
  inputRef: RefObject<HTMLTextAreaElement | null>;
}

type NumberField = "max_iterations" | "passing_score" | "temperature";

const NUMBER_FIELDS: { name: NumberField; label: string; min: number; max: number; step?: number }[] = [
  { name: "max_iterations", label: "Review rounds", min: 1, max: 10 },
  { name: "passing_score", label: "Passing score", min: 0, max: 100 },
  { name: "temperature", label: "Temperature", min: 0, max: 2, step: 0.1 },
];

function runDefaults(config: Config): Partial<RunSettings> {
  const { max_iterations, passing_score, temperature, fast_mode, skip_verify } = config;
  return { max_iterations, passing_score, temperature, fast_mode, skip_verify };
}

/** The request box. Run options apply to this run only; everything else lives in Settings. */
export function Composer({ config, running, onRun, onCancel, requirement, onRequirement, inputRef }: Props) {
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState<Partial<RunSettings>>({});
  useEffect(() => {
    if (config) setOptions(runDefaults(config));
  }, [config]);

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    onRun(requirement.trim(), options);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) submit();
  };

  const set = (name: keyof RunSettings, value: unknown) => setOptions((current) => ({ ...current, [name]: value }));

  return (
    <form className="composer" onSubmit={submit}>
      <textarea
        ref={inputRef}
        value={requirement}
        onChange={(e) => onRequirement(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Add a /health endpoint that reports DB status, with a test"
        aria-label="Request"
      />
      <div className="composer-row">
        <span className="hint">Ctrl+Enter to run</span>
        <button type="button" aria-expanded={open} aria-controls="run-options" onClick={() => setOpen(!open)}>
          Run options
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
        <div className="settings" id="run-options">
          {NUMBER_FIELDS.map((field) => (
            <label key={field.name}>
              {field.label}
              <input
                type="number"
                min={field.min}
                max={field.max}
                step={field.step}
                value={options[field.name] ?? ""}
                onChange={(e) => set(field.name, e.target.value === "" ? undefined : Number(e.target.value))}
              />
            </label>
          ))}
          <label className="check">
            <input type="checkbox" checked={Boolean(options.fast_mode)} onChange={(e) => set("fast_mode", e.target.checked)} />
            Fast mode: one generation pass instead of one per dimension
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={Boolean(options.skip_verify)}
              onChange={(e) => set("skip_verify", e.target.checked)}
            />
            Skip running tests
          </label>
        </div>
      )}
    </form>
  );
}
