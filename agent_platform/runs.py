from __future__ import annotations

import dataclasses
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .config import setting
from .llm import RunCancelled, build_llms
from .models import PipelineConfig
from .orchestrator import Orchestrator
from .workspace import changed_files, file_diff, load_workspace

FINISHED = {"done", "error", "cancelled"}
WAIT_POLL_SECONDS = 1.0
MAX_SAVED_RUNS = setting("MAX_SAVED_RUNS", 50)
EMPTY_METRICS = {"llm_calls": 0, "prompt_tokens": 0, "output_tokens": 0, "llm_seconds": 0.0}

logger = logging.getLogger("autocode.runs")
AGENT_APPROVAL = setting("AGENT_APPROVAL", "ask")  # ask | auto | read_only


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
        """mode "generate" runs the full pipeline; "review" only scores the given files; "agent" works in the
        workspace with tools (files, shell, git, GitHub)."""
        self.id = uuid.uuid4().hex[:12]
        self.context_id = context_id or self.id
        self.requirement = requirement
        self.workspace = workspace
        self.config = config
        self.mode = mode
        self.created_at = time.time()
        self.finished_at: float | None = None
        self.status = "running"
        self.original: dict[str, str] = files if files is not None else load_workspace(workspace)
        self.files: dict[str, str] = dict(self.original)
        self.plan: dict[str, Any] = {}
        self.reviews: list[dict[str, Any]] = []
        self.passed = False
        self.error = ""
        self.metrics: dict[str, Any] = dict(EMPTY_METRICS)
        self.events: list[dict[str, Any]] = []
        self.cond = threading.Condition()
        self.cancel_event = threading.Event()
        self.approvals: dict[str, dict[str, Any]] = {}

    # --- approvals (agent mode) ----------------------------------------------------------------
    def request_approval(self, tool: str, args: dict[str, Any], summary: str) -> bool:
        """Block the agent until the user approves or declines in the UI (or the run is cancelled)."""
        approval_id = uuid.uuid4().hex[:8]
        decided = threading.Event()
        self.approvals[approval_id] = {"event": decided, "approved": False}
        self.emit("approval_request", {"id": approval_id, "tool": tool, "summary": summary, "args": json.dumps(args)[:2000]})
        while not decided.wait(WAIT_POLL_SECONDS):
            if self.cancel_event.is_set():
                raise RunCancelled()
        approved = bool(self.approvals.pop(approval_id)["approved"])
        self.emit("approval_result", {"id": approval_id, "tool": tool, "approved": approved})
        return approved

    def decide(self, approval_id: str, approved: bool) -> bool:
        pending = self.approvals.get(approval_id)
        if not pending:
            return False
        pending["approved"] = approved
        pending["event"].set()
        return True

    def execute_agent(self) -> None:
        from .tool_agent import ToolAgent
        from .tools import ToolKit

        llm = build_llms(
            self.config,
            on_call=lambda label: self.emit("llm_call", {"label": label}),
            on_done=lambda metrics: self.emit("llm_done", metrics),
            cancel_event=self.cancel_event,
        )["coder"]
        llm.ensure_ready()
        toolkit = ToolKit(
            self.workspace,
            self.request_approval,
            AGENT_APPROVAL,
            self.config.command_timeout_seconds,
            on_change=lambda: self.emit("files", {"files": load_workspace(self.workspace), "stage": "agent"}),
        )
        self.passed, _ = ToolAgent(llm, toolkit, self.emit).run(self.requirement)

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
        elif kind == "llm_done":
            self.record_llm_call(data)
            data = {**data, "totals": dict(self.metrics)}
        with self.cond:
            self.events.append({"id": len(self.events), "kind": kind, "data": data, "ts": time.time()})
            self.cond.notify_all()

    def record_llm_call(self, call: dict[str, Any]) -> None:
        self.metrics = {
            "llm_calls": self.metrics["llm_calls"] + 1,
            "prompt_tokens": self.metrics["prompt_tokens"] + int(call.get("prompt_tokens", 0)),
            "output_tokens": self.metrics["output_tokens"] + int(call.get("output_tokens", 0)),
            "llm_seconds": round(self.metrics["llm_seconds"] + float(call.get("seconds", 0)), 2),
        }
        logger.info(
            "run=%s llm=%r seconds=%s prompt_tokens=%s output_tokens=%s",
            self.id, call.get("label"), call.get("seconds"), call.get("prompt_tokens"), call.get("output_tokens"),
        )

    def execute(self) -> None:
        logger.info("run=%s started mode=%s model=%s workspace=%r", self.id, self.mode, self.config.model, self.workspace)
        try:
            if self.mode == "agent":
                self.execute_agent()
            elif self.mode == "review":
                orchestrator = Orchestrator(self.config, emit=self.emit, cancel_event=self.cancel_event, run_id=self.id)
                self.passed, _ = orchestrator.review(self.requirement, self.original)
            else:
                orchestrator = Orchestrator(self.config, emit=self.emit, cancel_event=self.cancel_event, run_id=self.id)
                self.passed = orchestrator.run(self.requirement, self.original).passed
            self.status = "done"
        except RunCancelled:
            self.status = "cancelled"
        except Exception as exc:  # surface any pipeline failure to the UI instead of killing the thread
            logger.exception("run=%s failed", self.id)
            self.error = str(exc)
            self.status = "error"
        self.finished_at = time.time()
        logger.info(
            "run=%s finished status=%s passed=%s seconds=%.1f metrics=%s",
            self.id, self.status, self.passed, self.finished_at - self.created_at, json.dumps(self.metrics),
        )
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
            "finished_at": self.finished_at,
            "metrics": self.metrics,
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

    # --- persistence -------------------------------------------------------
    def to_record(self) -> dict[str, Any]:
        """What gets saved: only changed files (and their originals), so history stays small."""
        changed = self.changed()
        events = []
        for event in self.events:
            if event["kind"] == "files":
                diffs = event["data"]["diffs"]
                data = {**event["data"], "files": {p: c for p, c in event["data"]["files"].items() if p in diffs}}
                event = {**event, "data": data}
            events.append(event)
        return {
            **self.summary(),
            "context_id": self.context_id,
            "error": self.error,
            "plan": self.plan,
            "reviews": self.reviews,
            "config": dataclasses.asdict(self.config),
            "original": {path: self.original[path] for path in changed if path in self.original},
            "changed": changed,
            "events": events,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any], fallback_config: PipelineConfig) -> Run:
        run = cls.__new__(cls)
        known = {field.name for field in dataclasses.fields(PipelineConfig)}
        try:
            config = PipelineConfig(**{k: v for k, v in record.get("config", {}).items() if k in known})
        except TypeError:
            config = fallback_config
        run.id = str(record["id"])
        run.context_id = str(record.get("context_id") or run.id)
        run.requirement = str(record["requirement"])
        run.workspace = str(record.get("workspace", ""))
        run.config = config
        run.mode = str(record.get("mode", "generate"))
        run.created_at = float(record["created_at"])
        run.finished_at = record.get("finished_at")
        # A run interrupted by a restart can never resume; report it as cancelled.
        run.status = record["status"] if record["status"] in FINISHED else "cancelled"
        run.original = dict(record.get("original", {}))
        run.files = {**run.original, **record.get("changed", {})}
        run.plan = record.get("plan", {})
        run.reviews = record.get("reviews", [])
        run.passed = bool(record.get("passed"))
        run.error = str(record.get("error", ""))
        run.metrics = {**EMPTY_METRICS, **record.get("metrics", {})}
        run.events = list(record.get("events", []))
        if not run.events or run.events[-1]["kind"] != "finished":
            run.events.append(
                {"id": len(run.events), "kind": "finished", "ts": run.created_at,
                 "data": {"status": run.status, "passed": run.passed, "error": run.error}}
            )
        run.cond = threading.Condition()
        run.cancel_event = threading.Event()
        return run


class RunStore:
    def __init__(self, persist_dir: Path | None = None, fallback_config: PipelineConfig | None = None) -> None:
        self.runs: dict[str, Run] = {}
        self.lock = threading.Lock()
        self.persist_dir = persist_dir
        if persist_dir and fallback_config:
            self.load(persist_dir, fallback_config)

    def start(self, run: Run) -> Run:
        with self.lock:
            self.runs[run.id] = run

        def execute_and_save() -> None:
            run.execute()
            self.save(run)

        threading.Thread(target=execute_and_save, name=f"run-{run.id}", daemon=True).start()
        return run

    def get(self, run_id: str) -> Run | None:
        with self.lock:
            return self.runs.get(run_id)

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            runs = list(self.runs.values())
        return [run.summary() for run in sorted(runs, key=lambda run: run.created_at, reverse=True)]

    def save(self, run: Run) -> None:
        if not self.persist_dir:
            return
        try:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            target = self.persist_dir / f"{run.id}.json"
            temp = target.with_suffix(".tmp")
            temp.write_text(json.dumps(run.to_record()), encoding="utf-8")
            os.replace(temp, target)  # atomic: a crash never leaves a half-written record
            self.prune()
        except OSError:
            logger.exception("run=%s could not be saved", run.id)

    def prune(self) -> None:
        assert self.persist_dir is not None
        records = sorted(self.persist_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for stale in records[MAX_SAVED_RUNS:]:
            stale.unlink(missing_ok=True)

    def load(self, persist_dir: Path, fallback_config: PipelineConfig) -> None:
        if not persist_dir.is_dir():
            return
        for path in persist_dir.glob("*.json"):
            try:
                run = Run.from_record(json.loads(path.read_text(encoding="utf-8")), fallback_config)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                logger.warning("skipping unreadable run record %s: %s", path.name, exc)
                continue
            self.runs[run.id] = run
