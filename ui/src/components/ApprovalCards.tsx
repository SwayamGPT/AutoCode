import type { Approval } from "../types";

interface Props {
  approvals: Approval[];
  onDecide: (id: string, approved: boolean) => void;
}

/** The agent pauses on shell commands, commits, pushes and GitHub writes until the user decides. */
export function ApprovalCards({ approvals, onDecide }: Props) {
  if (!approvals.length) return null;
  return (
    <div className="approvals" role="alert">
      {approvals.map((approval) => (
        <div className="approval" key={approval.id}>
          <div className="approval-text">
            <strong>{approval.summary}</strong>
            <details>
              <summary>Details</summary>
              <pre>{approval.args}</pre>
            </details>
          </div>
          <div className="approval-actions">
            <button type="button" onClick={() => onDecide(approval.id, false)}>
              Deny
            </button>
            <button type="button" className="primary" onClick={() => onDecide(approval.id, true)} autoFocus>
              Allow
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
