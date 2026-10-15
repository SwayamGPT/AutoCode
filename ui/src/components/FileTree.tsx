import { buildTree, sortedEntries, type TreeDir } from "../state/tree";

interface Props {
  files: Record<string, string>;
  diffs: Record<string, string>;
  originalPaths: Record<string, true>;
  selected: string | null;
  onSelect: (path: string) => void;
}

export function FileTree({ files, diffs, originalPaths, selected, onSelect }: Props) {
  const paths = Object.keys(files);
  const changed = Object.keys(diffs).length;

  const renderNode = (node: TreeDir) =>
    sortedEntries(node).map(([name, value]) => {
      if (typeof value === "string") {
        const mark = diffs[value] ? (value in originalPaths ? "M" : "A") : "";
        return (
          <button
            type="button"
            key={value}
            className={`file${value === selected ? " active" : ""}`}
            onClick={() => onSelect(value)}
            aria-current={value === selected ? "true" : undefined}
          >
            <span>{name}</span>
            {mark && (
              <span className={`mark ${mark}`} title={mark === "A" ? "New file" : "Modified"}>
                {mark}
              </span>
            )}
          </button>
        );
      }
      return (
        <details open key={name}>
          <summary>{name}</summary>
          <div className="children">{renderNode(value)}</div>
        </details>
      );
    });

  return (
    <section className="pane files" aria-label="Files">
      <div className="pane-head">
        {paths.length ? `${paths.length} files${changed ? `, ${changed} changed` : ""}` : "No files yet"}
      </div>
      <div className="scroll">
        <div className="tree">
          {paths.length ? (
            renderNode(buildTree(paths))
          ) : (
            <div className="empty">Files appear here once the agent writes them, or when you open a project folder.</div>
          )}
        </div>
      </div>
    </section>
  );
}
