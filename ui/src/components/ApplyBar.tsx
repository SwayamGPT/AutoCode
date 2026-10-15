import { useEffect, useState, type FormEvent } from "react";

interface Props {
  count: number;
  defaultPath: string;
  onApply: (path: string) => void;
}

export function ApplyBar({ count, defaultPath, onApply }: Props) {
  const [path, setPath] = useState(defaultPath);
  useEffect(() => {
    setPath(defaultPath);
  }, [defaultPath]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    onApply(path.trim());
  };

  return (
    <form className="applybar" onSubmit={submit}>
      <input
        type="text"
        value={path}
        onChange={(e) => setPath(e.target.value)}
        aria-label="Write files to folder"
        placeholder="Folder to write files to"
        spellCheck={false}
      />
      <button type="submit" className="primary">
        Write {count} file{count === 1 ? "" : "s"}
      </button>
    </form>
  );
}
