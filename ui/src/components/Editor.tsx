import type { ReactNode } from "react";
import type { View } from "../state/run";
import { diffLineClass } from "../state/tree";

interface Props {
  path: string | null;
  content: string | undefined;
  diff: string | undefined;
  view: View;
  onView: (view: View) => void;
  planView: ReactNode;
  memoryView: ReactNode;
}

function Welcome() {
  return (
    <div className="welcome">
      <h1>Describe what to build. Ten dimensions plan it, build it and check it.</h1>
      <p>
        AutoCode plans the project in ten steps, one per quality dimension from business logic to observability. Each
        dimension then makes its own code changes, and ten reviewers score the result against those plans. It patches
        what they flag and runs the tests until everything passes. Everything runs on this machine.
      </p>
      <ol>
        <li>Choose a project folder to change existing code, or leave it empty to start fresh.</li>
        <li>
          Write the request in the panel on the right and press <kbd>Ctrl</kbd>+<kbd>Enter</kbd>.
        </li>
        <li>Read the changes, then write them to disk.</li>
      </ol>
    </div>
  );
}

export function Editor({ path, content, diff, view, onView, planView, memoryView }: Props) {
  const body = (() => {
    if (view === "plan") return planView;
    if (view === "memory") return memoryView;
    if (!path || content === undefined) return <Welcome />;
    if (view === "diff") {
      if (!diff) return <div className="empty">No changes in this file.</div>;
      return (
        <pre className="code diff">
          {diff
            .replace(/\n$/, "")
            .split("\n")
            .map((line, i) => (
              <span key={i} className={`l ${diffLineClass(line)}`}>
                {line || " "}
              </span>
            ))}
        </pre>
      );
    }
    return (
      <pre className="code">
        {content.split("\n").map((line, i) => (
          <span key={i} className="l">
            {line || " "}
          </span>
        ))}
      </pre>
    );
  })();

  return (
    <section className="pane editor" aria-label="Editor">
      <div className="pane-head">
        <span className="path">
          {view === "plan" ? "Ten-step plan" : view === "memory" ? "What AutoCode has learned" : (path ?? "")}
        </span>
        <div className="tabs" role="group" aria-label="View">
          <button type="button" aria-pressed={view === "plan"} onClick={() => onView("plan")}>
            Plan
          </button>
          <button type="button" aria-pressed={view === "code"} onClick={() => onView("code")}>
            Code
          </button>
          <button type="button" aria-pressed={view === "diff"} onClick={() => onView("diff")}>
            Changes
          </button>
          <button type="button" aria-pressed={view === "memory"} onClick={() => onView("memory")}>
            Memory
          </button>
        </div>
      </div>
      <div className="scroll">{body}</div>
    </section>
  );
}
