import type { LogEntry, Plan, Review, RunEvent, RunSnapshot, RunStatus, Tone } from "../types";

export interface RunState {
  personas: string[];
  passingScore: number;
  workspace: string;
  originalPaths: Record<string, true>;
  files: Record<string, string>;
  diffs: Record<string, string>;
  selected: string | null;
  view: "code" | "diff";
  autoSelect: boolean; // jump to the first changed file when a run's files arrive
  reviews: Record<string, Review>;
  log: LogEntry[];
  run: { id: string; startedAt: number; status: RunStatus; passed: boolean } | null;
}

export type Action =
  | { type: "config"; personas: string[]; passingScore: number }
  | { type: "workspace"; path: string; files: Record<string, string> }
  | { type: "runStarted"; id: string; startedAt: number; requirement: string; passingScore: number }
  | { type: "runOpened"; snapshot: RunSnapshot }
  | { type: "event"; event: RunEvent }
  | { type: "select"; path: string }
  | { type: "view"; view: "code" | "diff" }
  | { type: "note"; text: string; tone: Tone };

export const initialState: RunState = {
  personas: [],
  passingScore: 85,
  workspace: "",
  originalPaths: {},
  files: {},
  diffs: {},
  selected: null,
  view: "code",
  autoSelect: false,
  reviews: {},
  log: [],
  run: null,
};

let logCounter = 0;

function entry(text: string, tone: Tone = "plain", ts = Date.now() / 1000, extra: Partial<LogEntry> = {}): LogEntry {
  logCounter += 1;
  return { key: `l${logCounter}`, ts, tone, text, ...extra };
}

export function isPassing(review: Review, passingScore: number): boolean {
  return review.status === "pass" && Number(review.score) >= passingScore;
}

function freshRun(state: RunState, id: string, startedAt: number): RunState {
  return {
    ...state,
    reviews: {},
    log: [],
    autoSelect: true,
    run: { id, startedAt, status: "running", passed: false },
  };
}

function applyEvent(state: RunState, event: RunEvent): RunState {
  const { kind, data, ts } = event;
  const log = (text: string, tone: Tone = "plain", extra: Partial<LogEntry> = {}) => ({
    ...state,
    log: [...state.log, entry(text, tone, ts, extra)],
  });

  switch (kind) {
    case "status":
      return log(data.message);
    case "llm_call":
      // Reviewer calls show up in the gauge; only list the other agents here.
      return state.personas.includes(data.label) ? state : log(`Asking model: ${data.label}`, "muted");
    case "plan": {
      const plan = data.plan as Plan;
      const files = (plan.files ?? []).map((f) => `${f.path}  ${f.purpose ?? ""}`).join("\n");
      return log(`Plan: ${plan.summary || "ready"}`, "plain", {
        detail: `${plan.architecture ?? ""}\n\nFiles:\n${files}\n\nTest: ${plan.test_command || "none"}`,
      });
    }
    case "mcp_call":
      return log(`Tool ${data.server}/${data.tool} ${data.ok ? "returned" : "failed"}`, data.ok ? "plain" : "bad", {
        detail: `${JSON.stringify(data.arguments, null, 2)}\n\n${data.result}`,
      });
    case "files": {
      const files = data.files as Record<string, string>;
      const diffs = data.diffs as Record<string, string>;
      let { selected, view, autoSelect } = state;
      if (autoSelect || !selected || !(selected in files)) {
        selected = Object.keys(diffs)[0] ?? Object.keys(files)[0] ?? null;
        view = selected && diffs[selected] ? "diff" : "code";
        autoSelect = false;
      }
      const verb = data.stage === "patched" ? "Patched" : "Wrote";
      const next = log(`${verb} ${Object.keys(diffs).length} changed files`);
      return { ...next, files, diffs, selected, view, autoSelect };
    }
    case "review": {
      const review = data.review as Review;
      // Remote A2A peers show up as extra bars the first time they report.
      const personas = state.personas.includes(review.persona) ? state.personas : [...state.personas, review.persona];
      return { ...state, personas, reviews: { ...state.reviews, [review.persona]: review } };
    }
    case "scorecard": {
      const reviews = data.reviews as Review[];
      const passed = reviews.filter((r) => isPassing(r, state.passingScore)).length;
      return log(`Iteration ${data.iteration}: ${passed} of ${reviews.length} reviewers passed`, passed === reviews.length ? "ok" : "plain");
    }
    case "patches": {
      const failed = (data.failed as unknown[]).length;
      const applied = (data.applied as unknown[]).length;
      return log(`Applied ${applied} patches${failed ? `, ${failed} did not apply` : ""}`, failed ? "bad" : "plain");
    }
    case "verify": {
      const ok = data.exit_code === 0;
      return log(`Tests ${ok ? "passed" : `failed (exit ${data.exit_code})`}: ${data.command || "none"}`, ok ? "ok" : "bad", {
        detail: data.output || "",
      });
    }
    case "finished": {
      const status = data.status as RunStatus;
      const text =
        status === "done"
          ? data.passed
            ? "Finished. All checks passed."
            : "Finished. Some checks still fail, so read the changes before writing them."
          : status === "cancelled"
            ? "Stopped."
            : `Failed: ${data.error}`;
      const next = log(text, status === "done" ? (data.passed ? "ok" : "plain") : "bad");
      return { ...next, run: next.run && { ...next.run, status, passed: Boolean(data.passed) } };
    }
    default:
      return state;
  }
}

export function reducer(state: RunState, action: Action): RunState {
  switch (action.type) {
    case "config":
      return { ...state, personas: action.personas, passingScore: action.passingScore };
    case "workspace":
      return {
        ...state,
        workspace: action.path,
        originalPaths: Object.fromEntries(Object.keys(action.files).map((p) => [p, true as const])),
        files: action.files,
        diffs: {},
        selected: Object.keys(action.files)[0] ?? null,
        view: "code",
      };
    case "runStarted": {
      const next = freshRun({ ...state, passingScore: action.passingScore, diffs: {} }, action.id, action.startedAt);
      return { ...next, log: [entry(action.requirement, "plain", action.startedAt, { emphasis: true })] };
    }
    case "runOpened": {
      const { snapshot } = action;
      return freshRun(
        {
          ...state,
          workspace: snapshot.workspace,
          originalPaths: Object.fromEntries(snapshot.original_paths.map((p) => [p, true as const])),
          files: snapshot.files,
          diffs: snapshot.diffs,
          selected: null,
        },
        snapshot.id,
        snapshot.created_at,
      );
    }
    case "event":
      return applyEvent(state, action.event);
    case "select":
      return {
        ...state,
        selected: action.path,
        view: state.view === "diff" && !state.diffs[action.path] ? "code" : state.view,
      };
    case "view":
      return { ...state, view: action.view };
    case "note":
      return { ...state, log: [...state.log, entry(action.text, action.tone)] };
  }
}
