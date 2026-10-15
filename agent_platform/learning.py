"""How AutoCode gets better with use.

- Memory (deep learning): lessons and run episodes are embedded with a neural embedding model and stored
  in Postgres/pgvector (or SQLite). Before each step, the most similar lessons are recalled into the prompt.
- Reflection: after a run, a critic agent looks at what each step planned, changed and scored, and writes
  down what could have been done better at that step. Those lessons are what memory recalls next time.
- Reinforcement learning: each dimension's implementation step picks a strategy (focused / thorough /
  test-first) with Thompson sampling over a Beta posterior per (dimension, strategy). The reward is that
  dimension's final review score, so strategies that score well for a dimension get picked more often.

What this is not: the language model's weights are never trained or fine-tuned. Learning happens in the
memory and in the strategy policy around the model, which works with any local or hosted model.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from typing import Any, Callable

from .llm import LLMClient
from .memory_store import HASH_EMBEDDING, Memory, MemoryStore, hash_embedding
from .providers import build_provider, parse_spec, provider_configs

logger = logging.getLogger("autocode.learning")

STRATEGIES: dict[str, dict[str, Any]] = {
    "focused": {
        "temperature": 0.1,
        "instruction": "Make the smallest change that fully meets the plan. Do not refactor unrelated code.",
    },
    "thorough": {
        "temperature": 0.3,
        "instruction": "Be thorough: meet every requirement and acceptance check, including edge cases and error paths.",
    },
    "test_first": {
        "temperature": 0.2,
        "instruction": "Start from verification: add or update tests for each acceptance check, then write code that passes them.",
    },
}
GENERAL = "General"
RECALL_PER_DIMENSION = 3
MIN_SIMILARITY = 0.25
MAX_LESSONS = 20
MAX_LESSON_CHARS = 400
DUPLICATE_SCAN = 20

REFLECT_PROMPT = """
You are the retrospective critic for an AI code generation pipeline that works in steps per quality dimension:
plan (each dimension writes requirements and acceptance checks), implement (each dimension changes the code),
review (each dimension scores the code), fix (repair failing tests).

Given what happened in this run, write concrete, reusable lessons about what should be done better at each
step next time. A lesson must be specific enough to change behaviour on a similar task
(good: "For REST APIs, Security should plan rate limiting on auth endpoints; the reviewer flagged it twice").
Skip anything that went well and needs no change. Return valid JSON only.

JSON schema:
{
  "summary": "one sentence on how the run went",
  "lessons": [
    {"dimension": "one of the dimension names, or General", "step": "plan | implement | review | fix",
     "lesson": "what to do differently next time"}
  ]
}
"""


class Embedder:
    """Neural embeddings from the configured model; falls back to hashed bag-of-words if it is unreachable."""

    def __init__(self, spec: str, providers_json: str, ollama_host: str, timeout_seconds: float) -> None:
        self.spec = spec
        self.provider = None
        self.model = ""
        if spec and spec != "hash":
            configs = provider_configs(providers_json, ollama_host)
            provider_config, self.model = parse_spec(spec, configs)
            self.provider = build_provider(provider_config, timeout_seconds)
        self.fell_back = False

    def embed(self, texts: list[str]) -> tuple[list[list[float]], str]:
        if self.provider is not None and not self.fell_back:
            try:
                return self.provider.embed(self.model, texts), self.spec
            except Exception as exc:  # model not pulled, provider down, no embeddings API
                logger.warning("embedding model %s unavailable (%s); using %s", self.spec, exc, HASH_EMBEDDING)
                self.fell_back = True
        return [hash_embedding(text) for text in texts], HASH_EMBEDDING


class StrategyPolicy:
    """Thompson sampling: sample each strategy's Beta(alpha, beta) posterior and pick the highest draw."""

    def __init__(self, store: MemoryStore, rng: random.Random | None = None) -> None:
        self.store = store
        self.rng = rng or random.Random()

    def choose(self, dimension: str) -> tuple[str, dict[str, float]]:
        stats = self.store.arm_stats(dimension)
        draws = {
            arm: self.rng.betavariate(*(stats[arm][:2] if arm in stats else (1.0, 1.0))) for arm in STRATEGIES
        }
        return max(draws, key=lambda arm: draws[arm]), {arm: round(value, 3) for arm, value in draws.items()}

    def reward(self, dimension: str, arm: str, reward: float) -> None:
        self.store.update_arm(dimension, arm, min(1.0, max(0.0, reward)))


@dataclass
class RunTrace:
    """What the orchestrator did, for reflection and policy updates."""

    requirement: str
    run_id: str = ""
    plans: list[dict[str, Any]] = field(default_factory=list)
    changes: dict[str, str] = field(default_factory=dict)  # dimension -> change summary
    strategies: dict[str, str] = field(default_factory=dict)  # dimension -> strategy arm
    fixes: list[str] = field(default_factory=list)
    failed_patches: int = 0
    verification_failures: list[str] = field(default_factory=list)


class Learner:
    def __init__(
        self,
        store: MemoryStore,
        embedder: Embedder,
        critic: LLMClient,
        emit: Callable[[str, dict[str, Any]], None],
        rng: random.Random | None = None,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.critic = critic
        self.emit = emit
        self.policy = StrategyPolicy(store, rng)

    def recall(self, requirement: str, dimensions: list[str]) -> dict[str, list[str]]:
        """Lessons from similar past tasks, per dimension (plus General)."""
        try:
            [query], model = self.embedder.embed([requirement])
            recalled: dict[str, list[str]] = {}
            for dimension in [GENERAL, *dimensions]:
                # Over-fetch: run-outcome episodes share the index but are not lessons to replay.
                hits = self.store.search(query, model, dimension, RECALL_PER_DIMENSION * 4)
                lessons = [
                    f"[{m.step}] {m.content}" for m in hits if m.kind == "lesson" and m.similarity >= MIN_SIMILARITY
                ][:RECALL_PER_DIMENSION]
                if lessons:
                    recalled[dimension] = lessons
        except Exception as exc:  # memory must never break a run
            logger.warning("memory recall failed: %s", exc)
            self.emit("status", {"message": f"Memory recall skipped: {exc}"})
            return {}
        self.emit(
            "memory_recall",
            {"backend": self.store.backend, "total": sum(map(len, recalled.values())), "lessons": recalled},
        )
        return recalled

    def choose_strategy(self, dimension: str) -> dict[str, Any]:
        arm, draws = self.policy.choose(dimension)
        self.emit("strategy", {"dimension": dimension, "strategy": arm, "draws": draws})
        return {"name": arm, **STRATEGIES[arm]}

    def learn(self, trace: RunTrace, reviews: list[dict[str, Any]], passed: bool) -> None:
        """After a run: reward the strategies, then reflect and store lessons. Never raises."""
        scores = {str(r.get("persona")): score_of(r) for r in reviews}
        updates = []
        for dimension, arm in trace.strategies.items():
            if dimension in scores:
                reward = scores[dimension] / 100
                self.policy.reward(dimension, arm, reward)
                updates.append({"dimension": dimension, "strategy": arm, "reward": round(reward, 3)})
        self.emit("learning", {"policy_updates": updates})
        try:
            self.reflect(trace, reviews, passed)
        except Exception as exc:
            logger.warning("reflection failed: %s", exc)
            self.emit("status", {"message": f"Reflection skipped: {exc}"})

    def already_known(self, lesson: dict[str, str], vector: list[float], model: str) -> bool:
        """Skip a lesson memory already holds for a similar task, so repeated runs do not pile up duplicates."""
        hits = self.store.search(vector, model, lesson["dimension"], DUPLICATE_SCAN)
        return any(normalized(hit.content) == normalized(lesson["lesson"]) for hit in hits if hit.kind == "lesson")

    def reflect(self, trace: RunTrace, reviews: list[dict[str, Any]], passed: bool) -> None:
        self.emit("status", {"message": "Reflecting on what each step could have done better..."})
        evidence = {
            "requirement": trace.requirement,
            "passed": passed,
            "dimension_plans": [
                {"dimension": p["dimension"], "applicable": p["applicable"], "requirements": p["requirements"][:4]}
                for p in trace.plans
            ],
            "strategies": trace.strategies,
            "changes": trace.changes,
            "fixes": trace.fixes,
            "failed_patches": trace.failed_patches,
            "verification_failures": [f[-500:] for f in trace.verification_failures[-2:]],
            "final_reviews": [
                {
                    "dimension": r.get("persona"),
                    "score": score_of(r),
                    "status": r.get("status"),
                    "issues": [str(i.get("description", ""))[:200] for i in r.get("issues", [])[:3] if isinstance(i, dict)],
                }
                for r in reviews
            ],
        }
        payload = self.critic.ask_json(REFLECT_PROMPT, json.dumps(evidence, indent=2), "Reflect")
        raw_lessons = payload.get("lessons") if isinstance(payload.get("lessons"), list) else []
        lessons = [
            {
                "dimension": str(item.get("dimension") or GENERAL),
                "step": str(item.get("step") or "plan"),
                "lesson": str(item["lesson"])[:MAX_LESSON_CHARS],
            }
            for item in raw_lessons
            if isinstance(item, dict) and str(item.get("lesson", "")).strip()
        ][:MAX_LESSONS]
        summary = str(payload.get("summary", ""))[:MAX_LESSON_CHARS]
        average = sum(score_of(r) for r in reviews) / len(reviews) if reviews else 0.0
        # Lessons are keyed by the task they came from: recall embeds the new requirement and finds lessons
        # from the most similar past tasks.
        [vector], model = self.embedder.embed([trace.requirement])
        lessons = [item for item in lessons if not self.already_known(item, vector, model)]
        memories = [
            Memory("lesson", item["lesson"], trace.requirement, vector, model, item["dimension"], item["step"], average, trace.run_id)
            for item in lessons
        ]
        outcome = summary or f"Run {'passed' if passed else 'did not pass'}."
        memories.append(Memory("episode", outcome, trace.requirement, vector, model, GENERAL, "overall", average, trace.run_id))
        self.store.add(memories)
        self.emit("reflection", {"summary": summary, "lessons": lessons, "stored": len(memories)})


def normalized(text: str) -> str:
    return " ".join(text.lower().split()).rstrip(".")


def score_of(review: dict[str, Any]) -> int:
    try:
        return int(review.get("score", 0))
    except (TypeError, ValueError):
        return 0


def format_lessons(lessons: list[str]) -> str:
    return "\n".join(f"- {lesson}" for lesson in lessons)
