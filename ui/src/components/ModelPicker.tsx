import { useId, useState } from "react";
import { api } from "../api";
import type { ProviderInfo } from "../types";

interface Props {
  label: string;
  value: string;
  providers: Record<string, ProviderInfo>;
  onChange: (spec: string) => void;
  /** Role models may be empty, meaning "use the default model". */
  optional?: boolean;
  hint?: string;
}

/** "<provider>:<model>" spec, split the same way the backend does (unknown prefix = Ollama model). */
export function splitSpec(spec: string, providers: Record<string, ProviderInfo>): [string, string] {
  const index = spec.indexOf(":");
  const prefix = index > 0 ? spec.slice(0, index) : "";
  return prefix && prefix in providers ? [prefix, spec.slice(index + 1)] : ["ollama", spec];
}

export function ModelPicker({ label, value, providers, onChange, optional, hint }: Props) {
  const id = useId();
  const [models, setModels] = useState<Record<string, string[]>>({});
  const [loadError, setLoadError] = useState("");
  const [provider, model] = value ? splitSpec(value, providers) : ["ollama", ""];
  const names = Object.keys(providers).sort();

  const loadModels = async (name: string) => {
    if (models[name]) return;
    try {
      const { models: list } = await api.models(name);
      setModels((current) => ({ ...current, [name]: list }));
      setLoadError("");
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error));
    }
  };

  const update = (nextProvider: string, nextModel: string) =>
    onChange(nextModel ? `${nextProvider}:${nextModel}` : optional ? "" : `${nextProvider}:`);

  return (
    <div className="model-picker">
      <label htmlFor={`${id}-model`}>{label}</label>
      <div className="model-row">
        <select aria-label={`${label} provider`} value={provider} onChange={(e) => update(e.target.value, model)}>
          {names.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <input
          id={`${id}-model`}
          type="text"
          list={`${id}-list`}
          value={model}
          placeholder={optional ? "Same as the default model" : providers[provider]?.example}
          onFocus={() => void loadModels(provider)}
          onChange={(e) => update(provider, e.target.value.trim())}
          spellCheck={false}
        />
        <datalist id={`${id}-list`}>
          {(models[provider] ?? []).map((name) => (
            <option key={name} value={name} />
          ))}
        </datalist>
      </div>
      {(hint || loadError) && <div className={`field-hint${loadError ? " bad" : ""}`}>{loadError || hint}</div>}
    </div>
  );
}
