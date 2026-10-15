import type { Health, PullProgress } from "../types";

interface Props {
  health: Health;
  pulls: PullProgress[];
  onRetry: () => void;
  onPull: (spec: string) => void;
  onOpenSettings: () => void;
}

function percent(pull: PullProgress): number {
  return pull.total ? Math.round((pull.completed / pull.total) * 100) : 0;
}

/** Tells the user exactly what is missing, and fixes what it can with one click. */
export function SetupBanner({ health, pulls, onRetry, onPull, onOpenSettings }: Props) {
  const embedding = health.embedding && !health.embedding.ok ? health.embedding : null;
  const broken = health.models.filter((model) => !model.ok);
  const activePulls = pulls.filter((pull) => !pull.done || pull.error);
  if (health.ok && !embedding && !activePulls.length) return null;

  const pullFor = (spec: string) => pulls.find((pull) => pull.spec === spec);

  return (
    <div className="setup" role="status">
      <div className="setup-items">
        {!health.git && (
          <div>
            <strong>Install Git.</strong> AutoCode uses it to apply reviewer patches.
          </div>
        )}
        {broken.map((model) => {
          const pull = pullFor(model.spec);
          return (
            <div key={model.spec}>
              <strong>{model.spec}</strong> ({(model.roles ?? []).join(", ")}) is not ready.{" "}
              {model.pullable ? (
                pull && !pull.done ? (
                  <span className="pull">
                    Downloading, {percent(pull)}%
                    <progress value={pull.completed} max={pull.total || 1} />
                  </span>
                ) : (
                  <button type="button" onClick={() => onPull(model.spec)}>
                    Download model
                  </button>
                )
              ) : (
                <span className="setup-detail">{model.message}</span>
              )}
              {pull?.error && <span className="setup-detail bad"> Download failed: {pull.error}</span>}
            </div>
          );
        })}
        {embedding && (
          <div>
            <strong>Embedding model {embedding.spec}</strong> is not ready, so memory uses keyword matching.{" "}
            {embedding.pullable &&
              (pullFor(embedding.spec) && !pullFor(embedding.spec)!.done ? (
                <span className="pull">
                  Downloading, {percent(pullFor(embedding.spec)!)}%
                  <progress value={pullFor(embedding.spec)!.completed} max={pullFor(embedding.spec)!.total || 1} />
                </span>
              ) : (
                <button type="button" onClick={() => onPull(embedding.spec)}>
                  Download embedding model
                </button>
              ))}
          </div>
        )}
      </div>
      <div className="setup-actions">
        <button type="button" onClick={onOpenSettings}>
          Settings
        </button>
        <button type="button" onClick={onRetry}>
          Check again
        </button>
      </div>
    </div>
  );
}
