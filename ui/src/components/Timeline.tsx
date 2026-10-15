import { useEffect, useRef } from "react";
import type { LogEntry } from "../types";

interface Props {
  log: LogEntry[];
  startedAt: number | undefined;
}

export function Timeline({ log, startedAt }: Props) {
  const endRef = useRef<HTMLLIElement>(null);
  useEffect(() => {
    // Braces matter: scrollIntoView may return a Promise, which React would treat as a cleanup function.
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [log.length]);

  return (
    <div className="scroll">
      <ol className="timeline">
        {log.map((item) => (
          <li key={item.key}>
            <span className="t">{startedAt ? `${Math.max(0, Math.round(item.ts - startedAt))}s` : ""}</span>
            <div className={item.tone}>
              {item.detail !== undefined ? (
                <details>
                  <summary>{item.text}</summary>
                  <pre>{item.detail}</pre>
                </details>
              ) : item.emphasis ? (
                <strong>{item.text}</strong>
              ) : (
                item.text
              )}
            </div>
          </li>
        ))}
        <li ref={endRef} aria-hidden="true" className="end" />
      </ol>
    </div>
  );
}
