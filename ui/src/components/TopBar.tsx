import { useEffect, useState, type FormEvent } from "react";
import { desktopBridge } from "../api";
import { useDesktopBridge } from "../hooks";

interface Props {
  workspace: string;
  model: string;
  onOpen: (path: string) => void;
  onOpenSettings: () => void;
}

export function TopBar({ workspace, model, onOpen, onOpenSettings }: Props) {
  const [path, setPath] = useState(workspace);
  const canBrowse = useDesktopBridge();
  useEffect(() => {
    setPath(workspace);
  }, [workspace]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    onOpen(path.trim());
  };

  const browse = async () => {
    const picked = await desktopBridge()?.pick_folder();
    if (picked) {
      setPath(picked);
      onOpen(picked);
    }
  };

  return (
    <header className="topbar">
      <div className="brand">
        Auto<span>Code</span>
      </div>
      <form className="ws" onSubmit={submit}>
        <input
          type="text"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          placeholder="Project folder to edit (empty = new project)"
          aria-label="Project folder"
          spellCheck={false}
        />
        {canBrowse && (
          <button type="button" onClick={browse}>
            Choose folder
          </button>
        )}
        <button type="submit">Open</button>
      </form>
      <div className="model">{model}</div>
      <button type="button" onClick={onOpenSettings}>
        Settings
      </button>
    </header>
  );
}
