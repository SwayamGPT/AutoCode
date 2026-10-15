import { diffLineClass } from "../state/tree";

interface Props {
  path: string | null;
  content: string | undefined;
  diff: string | undefined;
  view: "code" | "diff";
  onView: (view: "code" | "diff") => void;
}

function Welcome() {
  return (
    <div className="welcome">
      <h1>Describe what to build. Ten reviewers check it before you see it.</h1>
      <p>
        AutoCode plans the files, writes them, and has ten reviewers score the result. It patches what they flag and
        runs the tests, then repeats until every reviewer passes. Everything runs on this machine.
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

export function Editor({ path, content, diff, view, onView }: Props) {
  const body = (() => {
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
        <span className="path">{path ?? ""}</span>
        <div className="tabs" role="group" aria-label="View">
          <button type="button" aria-pressed={view === "code"} onClick={() => onView("code")}>
            Code
          </button>
          <button type="button" aria-pressed={view === "diff"} onClick={() => onView("diff")}>
            Changes
          </button>
        </div>
      </div>
      <div className="scroll">{body}</div>
    </section>
  );
}
