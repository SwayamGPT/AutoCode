from __future__ import annotations

import difflib
from pathlib import Path

IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".next",
    ".idea",
    ".vscode",
}
MAX_FILE_BYTES = 200_000
MAX_WORKSPACE_FILES = 400


def is_safe_relative_path(path: str) -> bool:
    candidate = Path(path)
    return bool(path) and not candidate.is_absolute() and ".." not in candidate.parts


def write_project(root: Path, files: dict[str, str]) -> list[str]:
    """Write files under root, skipping unsafe paths. Returns the paths written."""
    written: list[str] = []
    for relative_path, content in files.items():
        if not is_safe_relative_path(relative_path):
            continue
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(relative_path)
    return written


def read_project_files(root: Path) -> dict[str, str]:
    """Read text files under root, skipping ignored dirs, binaries, and oversized files."""
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if len(files) >= MAX_WORKSPACE_FILES:
            break
        relative = path.relative_to(root)
        if not path.is_file() or any(part in IGNORED_DIRS for part in relative.parts):
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            files[relative.as_posix()] = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
    return files


def load_workspace(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    root = Path(path).expanduser()
    if not root.exists():
        return {}
    if not root.is_dir():
        raise ValueError(f"Workspace is not a directory: {root}")
    return read_project_files(root)


def changed_files(original: dict[str, str], final: dict[str, str]) -> dict[str, str]:
    return {path: content for path, content in final.items() if original.get(path) != content}


def file_diff(path: str, old: str | None, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            (old or "").splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="/dev/null" if old is None else f"a/{path}",
            tofile=f"b/{path}",
        )
    )
