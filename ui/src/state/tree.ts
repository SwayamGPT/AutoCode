export interface TreeDir {
  [name: string]: TreeDir | string; // string leaf = full path
}

export function buildTree(paths: string[]): TreeDir {
  const root: TreeDir = {};
  for (const path of paths) {
    let node = root;
    const parts = path.split("/");
    parts.forEach((part, index) => {
      if (index === parts.length - 1) {
        node[part] = path;
        return;
      }
      const child = node[part];
      node = node[part] = typeof child === "object" ? child : {};
    });
  }
  return root;
}

/** Directories first, then files, each alphabetical. */
export function sortedEntries(node: TreeDir): [string, TreeDir | string][] {
  return Object.entries(node).sort(
    ([a, av], [b, bv]) => Number(typeof av === "string") - Number(typeof bv === "string") || a.localeCompare(b),
  );
}

export function diffLineClass(line: string): string {
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+") && !line.startsWith("+++")) return "add";
  if (line.startsWith("-") && !line.startsWith("---")) return "del";
  return "";
}
