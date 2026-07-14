from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .patch_engine import write_project


def run_command(command: str, cwd: Path, timeout_seconds: int) -> tuple[int, str]:
    if not command:
        return 0, "No command configured."
    completed = subprocess.run(
        command,
        cwd=cwd,
        shell=True,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    return completed.returncode, output[-4000:]


class Validator:
    def __init__(self, timeout_seconds: int) -> None:
        self.timeout_seconds = timeout_seconds

    def run(self, plan: dict[str, Any], files: dict[str, str]) -> dict[str, Any]:
        test_command = str(plan.get("test_command") or "").strip()
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            write_project(project_dir, files)
            if test_command:
                code, output = run_command(test_command, project_dir, self.timeout_seconds)
                return {"command": test_command, "exit_code": code, "output": output}

            py_files = [str(path.relative_to(project_dir)) for path in project_dir.rglob("*.py")]
            if not py_files:
                return {"command": "", "exit_code": 0, "output": "No Python files to compile."}
            code, output = run_command("python -m compileall -q .", project_dir, self.timeout_seconds)
            return {"command": "python -m compileall -q .", "exit_code": code, "output": output}
