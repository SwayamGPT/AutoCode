import type { Health } from "../types";

interface Props {
  health: Health;
  model: string;
  onRetry: () => void;
}

export function SetupBanner({ health, model, onRetry }: Props) {
  if (health.ollama && health.model && health.git) return null;
  const step = !health.ollama
    ? <>Install Ollama from ollama.com and start it.</>
    : !health.model
      ? <>Download the model: <code>ollama pull {model}</code></>
      : <>Install Git. AutoCode uses it to apply reviewer patches.</>;
  return (
    <div className="setup" role="alert">
      <div>
        <strong>AutoCode needs one more thing before it can run.</strong> {step}
        {health.message && <div className="setup-detail">{health.message}</div>}
      </div>
      <button type="button" onClick={onRetry}>
        Check again
      </button>
    </div>
  );
}
