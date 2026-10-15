import { useState } from "react";
import { isPassing } from "../state/run";
import type { Review } from "../types";

interface Props {
  personas: string[];
  reviews: Record<string, Review>;
  passingScore: number;
}

const LOW_SCORE = 20; // below this the score label sits above the fill so it stays readable

export function ReviewerGauge({ personas, reviews, passingScore }: Props) {
  const [focus, setFocus] = useState<string | null>(null);
  const columns = { gridTemplateColumns: `repeat(${Math.max(personas.length, 1)}, 1fr)` };
  const focused = focus ? reviews[focus] : undefined;

  return (
    <div className="gauge-wrap">
      <div className="gauge-title">
        <span>Reviewer scores</span>
        <span>pass at {passingScore}</span>
      </div>
      <div className="gauge" style={columns}>
        <div className="pass-line" style={{ bottom: `${passingScore}%` }} />
        {personas.map((name) => {
          const review = reviews[name];
          const score = review ? Math.max(0, Math.min(100, Number(review.score) || 0)) : null;
          const pass = review ? isPassing(review, passingScore) : false;
          const label = review ? `${name}: ${score}, ${pass ? "passing" : "needs work"}` : `${name}: waiting`;
          return (
            <button
              type="button"
              key={name}
              className={`bar${pass ? " pass" : ""}`}
              aria-label={label}
              title={label}
              aria-pressed={focus === name}
              onClick={() => setFocus(focus === name ? null : name)}
            >
              <span className="fill" style={{ height: `${score ?? 0}%` }} />
              {score !== null && <span className={`score${score < LOW_SCORE ? " low" : ""}`}>{score}</span>}
            </button>
          );
        })}
      </div>
      <div className="gauge-labels" style={columns}>
        {personas.map((name) => (
          <span key={name} title={name}>
            {name.split(/[ &/]/)[0]}
          </span>
        ))}
      </div>
      {focus && focused && (
        <div className="persona-detail">
          <h3>
            {focus}: {String(focused.score)} ({focused.status})
          </h3>
          {(focused.issues ?? []).length === 0 && <div className="issue">No issues reported.</div>}
          {(focused.issues ?? []).map((issue, i) => (
            <div className="issue" key={i}>
              <span className={`sev ${issue.severity ?? ""}`}>{issue.severity || "note"}</span>
              {issue.file && <code>{issue.file} </code>}
              {issue.description}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
