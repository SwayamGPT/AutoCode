from __future__ import annotations

import dataclasses
import json
import os
import random
from pathlib import Path
from typing import Any

import pytest

from agent_platform.config import config_from_env
from agent_platform.learning import STRATEGIES, Embedder, Learner, RunTrace, StrategyPolicy
from agent_platform.memory_store import HASH_EMBEDDING, Memory, SqliteStore, hash_embedding, open_store
from agent_platform.orchestrator import Orchestrator
from tests.fakes import FakeLLM


@pytest.fixture()
def store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(tmp_path / "memory.db")


def lesson(text: str, requirement: str, dimension: str = "Security") -> Memory:
    return Memory("lesson", text, requirement, hash_embedding(requirement), HASH_EMBEDDING, dimension, "plan")


def test_sqlite_store_ranks_by_similarity_and_filters_by_dimension(store: SqliteStore) -> None:
    store.add([
        lesson("Rate-limit login.", "build a login api with jwt auth"),
        lesson("Index the orders table.", "design an orders database schema", "Data Management"),
        lesson("Escape HTML output.", "render a markdown blog"),
    ])
    hits = store.search(hash_embedding("jwt login api for users"), HASH_EMBEDDING, "Security", 2)
    assert hits[0].content == "Rate-limit login." and hits[0].similarity > hits[1].similarity
    assert all(hit.dimension == "Security" for hit in hits)
    assert store.search(hash_embedding("x"), "other-model", None, 5) == []
    assert store.count() == 3
    store.delete(hits[0].id)
    assert store.count() == 2
    store.clear()
    assert store.count() == 0


def test_bandit_learns_the_better_strategy(store: SqliteStore) -> None:
    policy = StrategyPolicy(store, random.Random(7))
    for _ in range(60):
        arm, _ = policy.choose("Security")
        policy.reward("Security", arm, 0.95 if arm == "thorough" else 0.2)
    stats = {row["arm"]: row for row in store.all_arm_stats()}
    assert stats["thorough"]["pulls"] > stats["focused"]["pulls"] + stats["test_first"]["pulls"]
    assert stats["thorough"]["mean_reward"] == pytest.approx(0.95)
    assert set(STRATEGIES) >= set(stats)


def test_reward_is_clamped(store: SqliteStore) -> None:
    policy = StrategyPolicy(store)
    policy.reward("UI/UX", "focused", 7.0)
    assert store.all_arm_stats()[0]["mean_reward"] == 1.0


def test_embedder_falls_back_to_hash_when_model_unreachable() -> None:
    embedder = Embedder("ollama:nomic-embed-text", "", "http://127.0.0.1:9", timeout_seconds=1)
    vectors, model = embedder.embed(["hello world"])
    assert model == HASH_EMBEDDING and len(vectors[0]) == 256
    assert Embedder("hash", "", "", 1).embed(["x"])[1] == HASH_EMBEDDING


def test_reflection_stores_lessons_and_recall_finds_them(store: SqliteStore) -> None:
    events: list[tuple[str, dict]] = []
    learner = Learner(store, Embedder("hash", "", "", 1), FakeLLM(), lambda k, d: events.append((k, d)), random.Random(1))
    trace = RunTrace("build an adder library", strategies={"Maintainability": "focused"})
    reviews = [{"persona": "Maintainability", "score": 80, "status": "needs_work", "issues": []}]
    learner.learn(trace, reviews, passed=False)

    kinds = [k for k, _ in events]
    assert "learning" in kinds and "reflection" in kinds
    assert store.all_arm_stats()[0] == {
        "dimension": "Maintainability", "arm": "focused", "pulls": 1, "mean_reward": 0.8, "expected": 0.6
    }
    learner.learn(trace, reviews, passed=False)  # the same lessons again must not be stored twice
    assert sum(1 for m in store.recent(50) if m.kind == "lesson") == 2

    recalled = learner.recall("build an adder library with tests", ["Maintainability"])
    assert recalled["Maintainability"] == ["[implement] Add docstrings to public functions."]
    assert recalled["General"] == ["[fix] Run the compile check before review."]


def test_orchestrator_learns_across_runs() -> None:
    config = dataclasses.replace(config_from_env(), max_iterations=3)
    first: list[tuple[str, dict]] = []
    Orchestrator(config, emit=lambda k, d: first.append((k, d)), llm=FakeLLM()).run("make an adder")
    assert any(k == "reflection" for k, _ in first)
    assert {d["dimension"] for k, d in first if k == "strategy"} >= {"Business Logic", "Security"}

    llm = FakeLLM()
    second: list[tuple[str, dict]] = []
    Orchestrator(config, emit=lambda k, d: second.append((k, d)), llm=llm).run("make an adder")
    recall = next(d for k, d in second if k == "memory_recall")
    assert recall["total"] >= 2
    assert "Add docstrings to public functions." in llm.prompts["Plan: Maintainability"]


def test_learning_off_skips_memory() -> None:
    config = dataclasses.replace(config_from_env(), learning=False)
    events: list[tuple[str, dict]] = []
    Orchestrator(config, emit=lambda k, d: events.append((k, d)), llm=FakeLLM()).run("make an adder")
    assert not {"memory_recall", "strategy", "reflection"} & {k for k, _ in events}


def test_fixer_repairs_failing_validation() -> None:
    class Broken(FakeLLM):
        def ask_json(self, system_prompt: str, user_prompt: str, label: str, temperature: float | None = None) -> dict[str, Any]:
            if label == "Generator":
                return {"files": {"app.py": "def add(a, b:\n    return a + b\n"}}
            return super().ask_json(system_prompt, user_prompt, label)

    events: list[tuple[str, dict]] = []
    config = dataclasses.replace(config_from_env(), max_iterations=3, fast_mode=True)
    result = Orchestrator(config, emit=lambda k, d: events.append((k, d)), llm=Broken()).run("make an adder")
    fix = next(d for k, d in events if k == "fix")
    assert fix == {"diagnosis": "Syntax error in app.py.", "changed": ["app.py"], "verified": True}
    assert result.passed


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="set TEST_DATABASE_URL to a Postgres with pgvector")
def test_pgvector_store_round_trip() -> None:
    from agent_platform.memory_store import PgVectorStore

    pg = PgVectorStore(os.environ["TEST_DATABASE_URL"])
    pg.clear()
    pg.add([lesson("Rate-limit login.", "jwt login api"), lesson("Escape HTML.", "markdown blog")])
    hits = pg.search(hash_embedding("jwt login api"), HASH_EMBEDDING, "Security", 1)
    assert hits[0].content == "Rate-limit login." and hits[0].similarity > 0.9
    pg.update_arm("Security", "focused", 0.5)
    pg.update_arm("Security", "focused", 1.0)
    assert pg.all_arm_stats() == [
        {"dimension": "Security", "arm": "focused", "pulls": 2, "mean_reward": 0.75, "expected": 0.625}
    ]
    assert json.dumps([m.public() for m in pg.recent(5)])
    pg.clear()


def test_open_store_falls_back_to_sqlite_when_postgres_is_down() -> None:
    store = open_store("postgresql://nobody@127.0.0.1:9/none")
    assert store.backend == "sqlite"
