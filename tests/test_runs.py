from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from agent_platform import runs as runs_module
from agent_platform.config import config_from_env
from agent_platform.orchestrator import Orchestrator
from agent_platform.runs import Run, RunStore
from tests.fakes import FakeLLM


@pytest.fixture(autouse=True)
def fake_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_orchestrator(config: Any, emit: Any, cancel_event: threading.Event) -> Orchestrator:
        llm = FakeLLM()
        llm.cancel_event = cancel_event
        return Orchestrator(config, emit=emit, llm=llm)

    monkeypatch.setattr(runs_module, "Orchestrator", fake_orchestrator)


def finished_run(store: RunStore, workspace: Path) -> Run:
    run = store.start(Run("adder", str(workspace), config_from_env()))
    assert run.wait(timeout=30)
    for _ in range(100):  # the save happens right after execute() returns
        if store.persist_dir is None or (store.persist_dir / f"{run.id}.json").exists():
            break
        threading.Event().wait(0.05)
    return run


def test_runs_survive_a_restart(tmp_path: Path) -> None:
    workspace = tmp_path / "proj"
    workspace.mkdir()
    (workspace / "README.md").write_text("hello\n")
    history = tmp_path / "runs"
    run = finished_run(RunStore(history, config_from_env()), workspace)

    record = json.loads((history / f"{run.id}.json").read_text())
    assert "README.md" not in record["changed"]  # unchanged workspace files are not stored

    reloaded = RunStore(history, config_from_env()).get(run.id)
    assert reloaded is not None
    assert reloaded.status == "done" and reloaded.passed
    assert reloaded.diffs() == run.diffs()
    assert reloaded.events[-1]["kind"] == "finished"
    assert reloaded.plan["dimensions"][0]["dimension"] == "Business Logic"


def test_unreadable_records_are_skipped_and_running_becomes_cancelled(tmp_path: Path) -> None:
    history = tmp_path / "runs"
    history.mkdir()
    (history / "broken.json").write_text("{not json")
    (history / "half.json").write_text(json.dumps({"id": "x1", "requirement": "r", "created_at": 1.0, "status": "running"}))
    store = RunStore(history, config_from_env())
    assert [summary["id"] for summary in store.list()] == ["x1"]
    assert store.get("x1").status == "cancelled"


def test_history_is_pruned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runs_module, "MAX_SAVED_RUNS", 2)
    workspace = tmp_path / "proj"
    workspace.mkdir()
    store = RunStore(tmp_path / "runs", config_from_env())
    for _ in range(3):
        finished_run(store, workspace)
    assert len(list((tmp_path / "runs").glob("*.json"))) == 2


def test_llm_metrics_are_aggregated() -> None:
    run = Run("x", "", config_from_env(), files={})
    run.emit("llm_done", {"label": "Planner", "seconds": 1.5, "prompt_tokens": 100, "output_tokens": 20})
    run.emit("llm_done", {"label": "Security", "seconds": 0.5, "prompt_tokens": 50, "output_tokens": 5})
    assert run.metrics == {"llm_calls": 2, "prompt_tokens": 150, "output_tokens": 25, "llm_seconds": 2.0}
    assert run.events[-1]["data"]["totals"] == run.metrics
