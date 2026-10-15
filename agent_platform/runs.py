from __future__ import annotations

import dataclasses
import threading
import time
import traceback
import uuid
from typing import Any

from .llm import RunCancelled
from .models import PipelineConfig
from .orchestrator import Orchestrator
from .workspace import changed_files, file_diff, load_workspace

FINISHED = {"done", "error", "cancelled"}
WAIT_POLL_SECONDS = 1.0


class Run:
    def __init__(
        self,
        requirement: str,
        workspace: str,
        config: PipelineConfig,
        mode: str = "generate",
        files: dict[str, str] | None = None,
        context_id: str = "",
    ) -> None:
        """mode "generate" runs the full pipeline; mode "review" only scores the given files."""
        self.id = uuid.uuid4().hex[:12]
        self.context_id = context_id or self.id
        self.requirement = requirement
        self.workspace = workspace
        self.config = config
        self.mode = mode
        self.created_at = time.time()
        self.status = "running"
        self.original: dict[str, str] = files if files is not None else load_workspace(workspace)
        self.files: dict[str, str] = dict(self.original)
        self.plan: dict[str, Any] = {}
        self.reviews: list[dict[str, Any]] = []
        self.passed = False
        self.error = ""
        self.events: list[dict[str, Any]] = []
        self.cond = threading.Condition()
        self.cancel_event = threading.Event()

    def changed(self) -> dict[str, str]:
        return changed_files(self.original, self.files)

    def diffs(self) -> dict[str, str]:
        return {path: file_diff(path, self.original.get(path), content) for path, content in self.changed().items()}

    def emit(self, kind: str, data: dict[str, Any]) -> None:
        if kind == "files":
            self.files = data["files"]
            data = {**data, "diffs": self.diffs()}
        elif kind == "plan":
            self.plan = data["plan"]
        elif kind == "scorecard":
            self.reviews = data["reviews"]
        with self.cond:
            self.events.append({"id": len(self.events), "kind": kind, "data": data, "ts": time.time()})
            self.cond.notify_all()

    def execute(self) -> None:
        try:
            orchestrator = Orchestrator(self.config, emit=self.emit, cancel_event=self.cancel_event)
            if self.mode == "review":
                self.passed, _ = orchestrator.review(self.requirement, self.original)
            else:
                self.passed = orchestrator.run(self.requirement, self.original).passed
            self.status = "done"
        except RunCancelled:
            self.status = "cancelled"
        except Exception as exc:  # surface any pipeline failure to the UI instead of killing the thread
            traceback.print_exc()
            self.error = str(exc)
            self.status = "error"
        self.emit("finished", {"status": self.status, "passed": self.passed, "error": self.error})

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the run finishes. Returns False on timeout."""
        deadline = None if timeout is None else time.time() + timeout
        with self.cond:
            while self.status not in FINISHED:
                if deadline is not None and time.time() >= deadline:
                    return False
                self.cond.wait(WAIT_POLL_SECONDS)
        return True

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "mode": self.mode,
            "requirement": self.requirement,
            "workspace": self.workspace,
            "status": self.status,
            "passed": self.passed,
            "created_at": self.created_at,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "error": self.error,
            "plan": self.plan,
            "files": self.files,
            "original_paths": sorted(self.original),
            "diffs": self.diffs(),
            "reviews": self.reviews,
            "config": dataclasses.asdict(self.config),
        }


class RunStore:
    # ponytail: in-memory only; runs vanish on restart. Persist to disk if history matters.
    def __init__(self) -> None:
        self.runs: dict[str, Run] = {}
        self.lock = threading.Lock()

    def start(self, run: Run) -> Run:
        with self.lock:
            self.runs[run.id] = run
        threading.Thread(target=run.execute, name=f"run-{run.id}", daemon=True).start()
        return run

    def get(self, run_id: str) -> Run | None:
        with self.lock:
            return self.runs.get(run_id)

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            runs = list(self.runs.values())
        return [run.summary() for run in sorted(runs, key=lambda run: run.created_at, reverse=True)]
