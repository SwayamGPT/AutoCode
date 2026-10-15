import { useMemo, useState, type ReactNode } from "react";
import { highlight } from "../state/highlight";
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
  onExample: (prompt: string) => void;
}

const EXAMPLES = [
  "Build a FastAPI service for user signups with validation, hashed passwords and tests",
  "Create a CLI that converts CSV files to JSON, with streaming for large files",
  "Add rate limiting and structured logging to this project's API",
  "Write a React form for booking appointments with accessible error messages",
];

function Welcome({ onExample }: { onExample: (prompt: string) => void }) {
  return (
    <div className="welcome">
      <h1>Describe what to build. Ten dimensions plan it, build it and check it.</h1>
      <p>
        AutoCode plans the project in ten steps, one per quality dimension from business logic to observability. Each
        dimension makes its own changes, ten reviewers score the result, and a fixer repairs failing tests. It remembers
        what went wrong, so similar tasks go better next time.
      </p>
      <h2>Try one</h2>
      <ul className="examples">
        {EXAMPLES.map((example) => (
          <li key={example}>
            <button type="button" onClick={() => onExample(example)}>
              {example}
            </button>
          </li>
        ))}
      </ul>
      <p className="shortcuts">
        <kbd>Ctrl</kbd> <kbd>Enter</kbd> runs, <kbd>Ctrl</kbd> <kbd>K</kbd> focuses the request, <kbd>Ctrl</kbd>{" "}
        <kbd>,</kbd> opens settings.
      </p>
    </div>
  );
}

function CodeView({ path, content }: { path: string; content: string }) {
  const html = useMemo(() => highlight(path, content), [path, content]);
  const lines = content.split("\n").length;
  return (
    <div className="code-view">
      <pre className="gutter" aria-hidden="true">
        {Array.from({ length: lines }, (_, i) => i + 1).join("\n")}
      </pre>
      {/* highlight() escapes the file content (hljs output or manual escaping), so injecting it is safe. */}
      <pre className="code hljs">
        <code dangerouslySetInnerHTML={{ __html: html }} />
      </pre>
    </div>
  );
}

export function Editor({ path, content, diff, view, onView, planView, memoryView, onExample }: Props) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    const text = view === "diff" ? diff : content;
    if (!text) return;
    await navigator.clipboard.writeText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  const body = (() => {
    if (view === "plan") return planView;
    if (view === "memory") return memoryView;
    if (!path || content === undefined) return <Welcome onExample={onExample} />;
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
    return <CodeView path={path} content={content} />;
  })();

  const showsFile = (view === "code" || view === "diff") && path && content !== undefined;
  return (
    <section className="pane editor" aria-label="Editor">
      <div className="pane-head">
        <span className="path">
          {view === "plan" ? "Ten-step plan" : view === "memory" ? "What AutoCode has learned" : (path ?? "")}
        </span>
        {showsFile && (
          <button type="button" className="copy" onClick={() => void copy()} aria-live="polite">
            {copied ? "Copied" : "Copy"}
          </button>
        )}
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
