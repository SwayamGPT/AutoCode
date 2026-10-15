from __future__ import annotations

from typing import Any

import pytest

from agent_platform.config import config_from_env
from agent_platform.llm import RunCancelled
from agent_platform.orchestrator import Orchestrator
from tests.fakes import FakeLLM, is_review_call


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


def test_ten_step_planning_then_per_dimension_changes() -> None:
    llm = FakeLLM()
    orchestrator, events = make(llm)
    result = orchestrator.run("make an adder")

    plans = [data for kind, data in events if kind == "dimension_plan"]
    assert [p["step"] for p in plans] == list(range(1, 11))
    assert result.plan["dimensions"][7] == {
        "dimension": "Data Management",
        "applicable": False,
        "reason": "No persistent data.",
        "requirements": [],
        "decisions": [],
        "files": [],
        "acceptance_checks": [],
    }
    # Later planning steps see earlier decisions.
    assert "Business Logic decision" in llm.prompts["Plan: Security"]

    changes = {data["dimension"]: data for kind, data in events if kind == "dimension_change"}
    assert len(changes) == 9  # Business Logic is the foundation build
    assert changes["Data Management"]["skipped"] is True
    assert changes["Security"]["changed"] == ["SECURITY.md"]
    assert result.files["SECURITY.md"] == "# Threat model\n"
    assert "../escape.py" not in result.files  # unsafe paths from the model are dropped

    # Reviewers score against their own dimension plan.
    assert "Security check" in llm.prompts["Security"]


def test_fast_mode_skips_dimension_implementation() -> None:
    llm = FakeLLM()
    orchestrator, events = make(llm, fast_mode=True)
    orchestrator.run("make an adder")
    assert not any(call.startswith("Implement: ") for call in llm.calls)
    assert sum(1 for kind, _ in events if kind == "dimension_plan") == 10


def test_returns_best_reviewed_version_when_gates_never_pass() -> None:
    class Declining(FakeLLM):
        def ask_json(self, system_prompt: str, user_prompt: str, label: str, temperature: float | None = None) -> dict[str, Any]:
            result = super().ask_json(system_prompt, user_prompt, label)
            if not is_review_call(label):
                return result
            first_round = sum(1 for c in self.calls if c == label) == 1
            return {**result, "score": 70 if first_round else 40, "status": "needs_work"}

    orchestrator, events = make(Declining(), max_iterations=2)
    result = orchestrator.run("make an adder")
    assert not result.passed
    assert all(review["score"] == 70 for review in result.reviews)
    assert any(kind == "files" and data["stage"] == "best:iteration 1" for kind, data in events)
