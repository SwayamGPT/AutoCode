import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { api } from "./api";
import { ApplyBar } from "./components/ApplyBar";
import { Composer } from "./components/Composer";
import { Editor } from "./components/Editor";
import { FileTree } from "./components/FileTree";
import { MemoryView } from "./components/MemoryView";
import { PipelineStrip } from "./components/PipelineStrip";
import { PlanView } from "./components/PlanView";
import { ReviewerGauge } from "./components/ReviewerGauge";
import { SettingsDialog } from "./components/SettingsDialog";
import { SetupBanner } from "./components/SetupBanner";
import { Timeline } from "./components/Timeline";
import { TopBar } from "./components/TopBar";
import { useRunStream } from "./hooks";
import { initialState, reducer } from "./state/run";
import type { Config, Health, PullProgress, RunSettings, RunSummary } from "./types";

const STATUS_LABELS = { running: "Working…", done: "Done", error: "Failed", cancelled: "Stopped" } as const;
const PULL_POLL_MS = 1500;

function formatTokens(count: number): string {
  return count >= 1000 ? `${(count / 1000).toFixed(1)}k` : String(count);
}

export default function App() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [config, setConfig] = useState<Config | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [pulls, setPulls] = useState<PullProgress[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [requirement, setRequirement] = useState("");
  const promptRef = useRef<HTMLTextAreaElement>(null);

  // Ctrl/Cmd+K focuses the request box, Ctrl/Cmd+, opens settings.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return;
      if (event.key === "k") {
        event.preventDefault();
        promptRef.current?.focus();
      } else if (event.key === ",") {
        event.preventDefault();
        setSettingsOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const [error, setError] = useState("");

  const guard = useCallback(async (action: () => Promise<void>) => {
    setError("");
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const refreshRuns = useCallback(() => {
    void guard(async () => setRuns((await api.runs()).runs));
  }, [guard]);

  const checkHealth = useCallback(() => {
    void guard(async () => setHealth(await api.health()));
  }, [guard]);

  const loadConfig = useCallback(() => {
    void guard(async () => {
      const { config: loaded, personas } = await api.config();
      setConfig(loaded);
      dispatch({ type: "config", personas, passingScore: loaded.passing_score });
    });
  }, [guard]);

  useEffect(() => {
    loadConfig();
    checkHealth();
    refreshRuns();
  }, [loadConfig, checkHealth, refreshRuns]);

  // Poll model downloads while any is in progress; re-check health when they finish.
  const downloading = pulls.some((pull) => !pull.done);
  useEffect(() => {
    if (!downloading) return;
    const timer = window.setInterval(() => {
      void api.pulls().then(({ pulls: latest }) => {
        setPulls(latest);
        if (latest.every((pull) => pull.done)) checkHealth();
      });
    }, PULL_POLL_MS);
    return () => window.clearInterval(timer);
  }, [downloading, checkHealth]);

  useRunStream(state.run?.id, dispatch, refreshRuns);

  const openWorkspace = async (path: string) => {
    const data = await api.workspace(path);
    dispatch({ type: "workspace", path, files: data.files });
    if (path && !data.exists) setError("That folder does not exist yet. AutoCode will create it when you write files.");
  };

  const startRun = (requirement: string, settings: Partial<RunSettings>) =>
    guard(async () => {
      if (!requirement) throw new Error("Describe what to build first.");
      const run = await api.startRun(requirement, state.workspace, settings);
      dispatch({
        type: "runStarted",
        id: run.id,
        startedAt: run.created_at,
        requirement,
        passingScore: Number(settings.passing_score ?? config?.passing_score ?? 85),
      });
      refreshRuns();
    });

  const openRun = (id: string) =>
    guard(async () => {
      if (!id) return;
      dispatch({ type: "runOpened", snapshot: await api.run(id) });
    });

  const apply = (path: string) =>
    guard(async () => {
      if (!state.run) return;
      if (!path) throw new Error("Choose a folder to write the files to.");
      const result = await api.apply(state.run.id, path);
      dispatch({ type: "note", text: `Wrote ${result.written.length} files to ${result.workspace}`, tone: "ok" });
    });

  const pull = (spec: string) =>
    guard(async () => {
      const started = await api.pull(spec);
      setPulls((current) => [...current.filter((p) => p.spec !== spec), started]);
    });

  const status = state.run?.status;
  const changed = Object.keys(state.diffs).length;
  const model = config?.model ?? "";

  return (
    <>
      <TopBar
        workspace={state.workspace}
        model={model}
        onOpen={(path) => void guard(() => openWorkspace(path))}
        onOpenSettings={() => setSettingsOpen(true)}
      />
      {health && (
        <SetupBanner
          health={health}
          pulls={pulls}
          onRetry={checkHealth}
          onPull={(spec) => void pull(spec)}
          onOpenSettings={() => setSettingsOpen(true)}
        />
      )}
      <main className="main">
        <FileTree
          files={state.files}
          diffs={state.diffs}
          originalPaths={state.originalPaths}
          selected={state.selected}
          onSelect={(path) => dispatch({ type: "select", path })}
        />
        <Editor
          path={state.selected}
          content={state.selected ? state.files[state.selected] : undefined}
          diff={state.selected ? state.diffs[state.selected] : undefined}
          view={state.view}
          onView={(view) => dispatch({ type: "view", view })}
          planView={
            <PlanView
              personas={state.personas}
              plans={state.dimensionPlans}
              changes={state.dimensionChanges}
              reviews={state.reviews}
              passingScore={state.passingScore}
              strategies={state.strategies}
              recalled={state.recalled}
              onOpenFile={(path) => dispatch({ type: "select", path })}
            />
          }
          onExample={(example) => {
            setRequirement(example);
            promptRef.current?.focus();
          }}
          memoryView={
            <MemoryView
              dimensions={state.personas.filter((name) => !name.startsWith("Peer "))}
              refreshKey={`${state.run?.id ?? ""}:${status ?? ""}`}
            />
          }
        />
        <section className="pane agent" aria-label="Agent">
          <div className="pane-head">
            <label className="runs">
              Run
              <select value={state.run?.id ?? ""} onChange={(e) => void openRun(e.target.value)}>
                <option value="">New run</option>
                {runs.map((run) => (
                  <option key={run.id} value={run.id}>
                    {run.requirement.slice(0, 48)}
                  </option>
                ))}
              </select>
            </label>
            {state.metrics && (
              <span className="metrics" title="Model calls and tokens used by this run">
                {state.metrics.llm_calls} calls, {formatTokens(state.metrics.prompt_tokens + state.metrics.output_tokens)} tokens
              </span>
            )}
            <span className={`state ${status ?? ""}`} role="status">
              {status ? STATUS_LABELS[status] : ""}
            </span>
          </div>
          <PipelineStrip
            current={state.node}
            visited={state.visited}
            iteration={state.iteration}
            maxIterations={config?.max_iterations ?? 0}
            running={status === "running"}
          />
          <ReviewerGauge personas={state.personas} reviews={state.reviews} passingScore={state.passingScore} />
          <Timeline log={state.log} startedAt={state.run?.startedAt} />
          {state.run && status !== "running" && changed > 0 && (
            <ApplyBar count={changed} defaultPath={state.workspace} onApply={(path) => void apply(path)} />
          )}
          <Composer
            config={config}
            running={status === "running"}
            requirement={requirement}
            onRequirement={setRequirement}
            inputRef={promptRef}
            onRun={(text, settings) => void startRun(text, settings)}
            onCancel={() =>
              void guard(async () => {
                if (!state.run) return;
                await api.cancel(state.run.id);
                dispatch({ type: "note", text: "Stopping after the current model call…", tone: "muted" });
              })
            }
          />
          {error && (
            <div className="error-text" role="alert">
              {error}
            </div>
          )}
        </section>
      </main>
      <SettingsDialog
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onSaved={() => {
          loadConfig();
          checkHealth();
        }}
      />
    </>
  );
}
