from __future__ import annotations

from typing import Any

import pytest

from agent_platform.config import config_from_env
from agent_platform.llm import RunCancelled
from agent_platform.orchestrator import Orchestrator
from tests.fakes import FakeLLM


def make(llm: FakeLLM, **overrides: Any) -> tuple[Orchestrator, list[tuple[str, dict]]]:
    import dataclasses

    events: list[tuple[str, dict]] = []
    settings = {"max_iterations": 3, "skip_verify": False, **overrides}
    config = dataclasses.replace(config_from_env(), **settings)
    return Orchestrator(config, emit=lambda kind, data: events.append((kind, data)), llm=llm), events


def test_run_patches_then_passes() -> None:
    orchestrator, events = make(FakeLLM())
    result = orchestrator.run("make an adder")
    assert result.passed
    assert '"""Add two numbers."""' in result.files["app.py"]
    kinds = [kind for kind, _ in events]
    assert kinds.count("scorecard") == 2
    assert ("patches" in kinds) and ("verify" in kinds)
    assert sum(1 for kind in kinds if kind == "review") == 20


def test_run_returns_not_passed_when_reviews_never_pass() -> None:
    orchestrator, events = make(FakeLLM(review_score=10, patch=False), max_iterations=2)
    result = orchestrator.run("make an adder")
    assert not result.passed
    assert [kind for kind, _ in events].count("scorecard") == 2


def test_edit_mode_keeps_existing_files() -> None:
    orchestrator, _ = make(FakeLLM(patch=False))
    result = orchestrator.run("make an adder", {"README.md": "hi\n"})
    assert result.files["README.md"] == "hi\n"
    assert "app.py" in result.files


def test_cancel_stops_run() -> None:
    llm = FakeLLM()
    llm.cancel_event.set()
    orchestrator, _ = make(llm)
    with pytest.raises(RunCancelled):
        orchestrator.run("make an adder")
