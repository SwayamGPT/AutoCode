interface Props {
  current: string | null;
  visited: string[];
  iteration: number;
  maxIterations: number;
  running: boolean;
}

interface Step {
  node: string;
  label: string;
}

// The LangGraph nodes: a one-way preparation phase, then the verify/fix/review/patch loop, then learning.
const PREPARE: Step[] = [
  { node: "recall", label: "Recall" },
  { node: "context", label: "Context" },
  { node: "plan", label: "Plan" },
  { node: "build", label: "Build" },
  { node: "implement", label: "Implement" },
];
const LOOP: Step[] = [
  { node: "verify", label: "Test" },
  { node: "fix", label: "Fix" },
  { node: "review", label: "Review" },
  { node: "patch", label: "Patch" },
];
const LEARN: Step = { node: "learn", label: "Learn" };

/** Where the run is in the pipeline graph; the review loop shows which round it is on. */
export function PipelineStrip({ current, visited, iteration, maxIterations, running }: Props) {
  if (!visited.length) return null;
  const seen = new Set(visited);
  const active = current === "finish" ? "learn" : current;
  const step = ({ node, label }: Step) => {
    const state = node === active && running ? "current" : seen.has(node) ? "done" : "todo";
    return (
      <li key={node} className={state} aria-current={state === "current" ? "step" : undefined}>
        {label}
      </li>
    );
  };
  return (
    <div className="pipeline" aria-label="Pipeline progress">
      <ol className="phase">{PREPARE.map(step)}</ol>
      <div className="phase-row">
        <ol className="phase loop" aria-label="Review loop">
          {LOOP.map(step)}
        </ol>
        <span className="round">{iteration > 0 ? `round ${iteration} of ${maxIterations}` : "review loop"}</span>
        <ol className="phase">{step(LEARN)}</ol>
      </div>
    </div>
  );
}
