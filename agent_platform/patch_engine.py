from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .models import PatchApplyResult


def collect_patches(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    patches = [
        patch
        for review in reviews
        for patch in review.get("patches", [])
        if isinstance(patch, dict) and patch.get("file") and patch.get("unified_diff")
    ]
    return sorted(patches, key=lambda patch: severity_rank.get(str(patch.get("severity", "medium")), 2))


def is_safe_relative_path(path: str) -> bool:
    candidate = Path(path)
    return not candidate.is_absolute() and ".." not in candidate.parts


def write_project(temp_dir: Path, files: dict[str, str]) -> None:
    for relative_path, content in files.items():
        path = temp_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def read_project_files(project_dir: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in project_dir.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        relative_path = path.relative_to(project_dir).as_posix()
        files[relative_path] = path.read_text(encoding="utf-8")
    return files


def run_process(
    args: list[str],
    cwd: Path,
    input_text: str | None = None,
    timeout_seconds: int = 60,
) -> tuple[int, str]:
    completed = subprocess.run(
        args,
        cwd=cwd,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    return completed.returncode, output[-4000:]


class PatchEngine:
    def __init__(self, timeout_seconds: int) -> None:
        self.timeout_seconds = timeout_seconds

    def apply(self, files: dict[str, str], patches: list[dict[str, Any]]) -> PatchApplyResult:
        if not patches:
            return PatchApplyResult(files=files, applied=[], failed=[])

        applied: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            write_project(project_dir, files)

            code, output = run_process(["git", "init", "-q"], project_dir, timeout_seconds=self.timeout_seconds)
            if code != 0:
                return PatchApplyResult(
                    files=files,
                    applied=[],
                    failed=[
                        {
                            "file": "",
                            "reason": "Could not initialize temporary Git repository.",
                            "output": output,
                        }
                    ],
                )

            for patch in patches:
                target_file = str(patch.get("file", ""))
                diff = str(patch.get("unified_diff", ""))
                if not is_safe_relative_path(target_file):
                    failed.append({**patch, "output": "Unsafe patch path."})
                    continue
                if not diff.strip():
                    failed.append({**patch, "output": "Empty unified_diff."})
                    continue
                if not diff.endswith("\n"):
                    diff += "\n"

                code, output = run_process(
                    ["git", "apply", "--check", "-"],
                    project_dir,
                    diff,
                    timeout_seconds=self.timeout_seconds,
                )
                if code != 0:
                    failed.append({**patch, "output": output})
                    continue

                code, output = run_process(
                    ["git", "apply", "-"],
                    project_dir,
                    diff,
                    timeout_seconds=self.timeout_seconds,
                )
                if code != 0:
                    failed.append({**patch, "output": output})
                    continue

                applied.append(patch)

            return PatchApplyResult(files=read_project_files(project_dir), applied=applied, failed=failed)
