from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .models import PatchApplyResult
from .config import setting
from .workspace import is_safe_relative_path, write_project

TIMEOUT_EXIT_CODE = 124
COMMAND_OUTPUT_CHARS = setting("COMMAND_OUTPUT_CHARS", 4000)  # tail of test output kept


def collect_patches(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    patches = [
        patch
        for review in reviews
        for patch in review.get("patches", [])
        if isinstance(patch, dict) and patch.get("file") and patch.get("unified_diff")
    ]
    return sorted(patches, key=lambda patch: severity_rank.get(str(patch.get("severity", "medium")), 2))


def read_back(project_dir: Path, paths: set[str]) -> dict[str, str]:
    files: dict[str, str] = {}
    for relative_path in sorted(paths):
        path = project_dir / relative_path
        if path.is_file():
            files[relative_path] = path.read_text(encoding="utf-8")
    return files


def run_process(
    args: list[str],
    cwd: Path,
    input_text: str | None = None,
    timeout_seconds: int = 60,
) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            args,
            cwd=cwd,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return TIMEOUT_EXIT_CODE, f"Timed out after {timeout_seconds}s."
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    return completed.returncode, output[-COMMAND_OUTPUT_CHARS:]


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

            touched = set(files) | {str(patch.get("file", "")) for patch in applied}
            return PatchApplyResult(files=read_back(project_dir, touched), applied=applied, failed=failed)
