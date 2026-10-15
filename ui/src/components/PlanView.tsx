import { isPassing } from "../state/run";
import type { DimensionChange, DimensionPlan, Review } from "../types";

interface Props {
  personas: string[];
  plans: DimensionPlan[];
  changes: Record<string, DimensionChange>;
  reviews: Record<string, Review>;
  passingScore: number;
  onOpenFile: (path: string) => void;
}

function stageOf(plan: DimensionPlan | undefined, change: DimensionChange | undefined, review: Review | undefined, passing: number) {
  if (review) return isPassing(review, passing) ? { label: `Passed, ${review.score}`, tone: "ok" } : { label: `Needs work, ${review.score}`, tone: "bad" };
  if (!plan) return { label: "Waiting to plan", tone: "muted" };
  if (!plan.applicable) return { label: "Not applicable", tone: "muted" };
  if (change && !change.skipped) return { label: `Implemented, ${change.changed.length} files`, tone: "plain" };
  if (change?.skipped) return { label: "Skipped", tone: "muted" };
  return { label: "Planned", tone: "plain" };
}

function Items({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="dim-section">
      <h4>{title}</h4>
      <ul>
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

export function PlanView({ personas, plans, changes, reviews, passingScore, onOpenFile }: Props) {
  // Peers (A2A) review but do not plan, so only the ten local dimensions are listed.
  const dimensions = personas.filter((name) => !name.startsWith("Peer "));
  if (!plans.length) {
    return (
      <div className="empty">
        The plan appears here while a run is planning: one step per quality dimension, each building on the ones before
        it.
      </div>
    );
  }
  return (
    <ol className="dimensions">
      {dimensions.map((name, index) => {
        const plan = plans.find((p) => p.dimension === name);
        const change = changes[name];
        const stage = stageOf(plan, change, reviews[name], passingScore);
        return (
          <li key={name} className={`dimension${plan && !plan.applicable ? " na" : ""}`}>
            <div className="dim-head">
              <span className="dim-step">{index + 1}</span>
              <h3>{name}</h3>
              <span className={`dim-stage ${stage.tone}`}>{stage.label}</span>
            </div>
            {plan && !plan.applicable && <p className="dim-reason">{plan.reason}</p>}
            {plan?.planning_error && <p className="dim-reason bad">Planning failed, using the checklist: {plan.planning_error}</p>}
            {plan && plan.applicable && (
              <div className="dim-body">
                <Items title="Requirements" items={plan.requirements} />
                <Items title="Decisions" items={plan.decisions} />
                <Items title="Acceptance checks" items={plan.acceptance_checks} />
                {plan.files.length > 0 && (
                  <div className="dim-section">
                    <h4>Files</h4>
                    <ul>
                      {plan.files.map((file) => (
                        <li key={file.path}>
                          <button type="button" className="link" onClick={() => onOpenFile(file.path)}>
                            {file.path}
                          </button>{" "}
                          {file.change}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {change && !change.skipped && <p className="dim-summary">{change.summary}</p>}
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
