from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

from .a2a_client import RemoteReviewer, peer_urls
from .context_agent import ContextAgent
from .dimensions import DimensionImplementer, DimensionPlanner, find_dimension_plan
from .fixer import FixerAgent
from .generator import GeneratorAgent
from .learning import GENERAL, Embedder, Learner, RunTrace, format_lessons
from .llm import ROLES, LLMClient, build_llms
from .memory_store import open_store
from .memory import MemoryManager
from .models import PipelineConfig, RunResult
from .patch_engine import PatchEngine, collect_patches
from .personas import PERSONAS
from .planner import PlannerAgent
from .retriever import RetrieverAgent
from .reviewer import ReviewCoordinator
from .validator import Validator
from .workspace import is_safe_relative_path

Emit = Callable[[str, dict[str, Any]], None]

logger = logging.getLogger("autocode.orchestrator")

FOUNDATION_FOCUS = (
    "Build the foundation: implement the Business Logic dimension plan completely and keep the code ready for "
    "the other dimensions, which later steps add one by one."
)
FAST_FOCUS = "Implement every applicable dimension plan in this single pass."


def console_emit(kind: str, data: dict[str, Any]) -> None:
    if kind == "status":
        print(data["message"], flush=True)
    elif kind == "llm_call":
        print(f"  LLM call: {data['label']}", flush=True)
    elif kind == "dimension_plan":
        dimension = data["plan"]
        detail = f"{len(dimension['requirements'])} requirements" if dimension["applicable"] else "not applicable"
        print(f"  [{data['step']}/{data['total']}] {dimension['dimension']}: {detail}", flush=True)
    elif kind == "dimension_change":
        print(f"  [{data['step']}/{data['total']}] {data['dimension']}: {data['summary']}", flush=True)
    elif kind == "review":
        print(f"  Finished: {data['review'].get('persona', 'Unknown')}", flush=True)
    elif kind == "scorecard":
        print_scorecard(data["reviews"])
    elif kind == "patches":
        print(f"  Applied patches: {len(data['applied'])}")
        print(f"  Failed patches: {len(data['failed'])}")
    elif kind == "verify":
        print(f"  Command: {data['command'] or 'none'}")
        print(f"  Exit code: {data['exit_code']}")
        if data["output"]:
            print(data["output"])


def review_score(review: dict[str, Any]) -> int:
    try:
        return int(review.get("score", 0))
    except (TypeError, ValueError):
        return 0


def is_failing(review: dict[str, Any], passing_score: int) -> bool:
    return review.get("status") != "pass" or review_score(review) < passing_score


def merge_files(files: dict[str, str], changes: dict[str, str]) -> dict[str, str]:
    """Apply model-proposed files, dropping any path that would escape the project."""
    return {**files, **{path: content for path, content in changes.items() if is_safe_relative_path(path)}}


@dataclass(frozen=True)
class Snapshot:
    """A reviewed version of the project; the best one is returned when the gates never all pass."""

    iteration: int
    files: dict[str, str]
    reviews: list[dict[str, Any]]
    verified: bool
    passed: bool

    @property
    def rank(self) -> tuple[bool, bool, float]:
        average = sum(review_score(r) for r in self.reviews) / len(self.reviews) if self.reviews else 0.0
        return (self.passed, self.verified, average)


class Orchestrator:
    def __init__(
        self,
        config: PipelineConfig,
        emit: Emit = console_emit,
        cancel_event: threading.Event | None = None,
        llm: LLMClient | None = None,
        run_id: str = "",
    ) -> None:
        self.emit = emit
        self.run_id = run_id
        self.last_verification: dict[str, Any] = {}
        # One client per role, so planning, coding, reviewing and reflection can use different models
        # (e.g. a hosted planner with local reviewers). Tests pass a single scripted `llm` for every role.
        self.llms = (
            {role: llm for role in ROLES}
            if llm is not None
            else build_llms(
                config,
                on_call=lambda label: emit("llm_call", {"label": label}),
                on_done=lambda metrics: emit("llm_done", metrics),
                cancel_event=cancel_event,
            )
        )
        self.config = config
        self.memory = MemoryManager()
        planner_llm, coder_llm, reviewer_llm = self.llms["planner"], self.llms["coder"], self.llms["reviewer"]
        self.context_agent = ContextAgent(planner_llm, config.mcp_config, config.command_timeout_seconds, emit)
        self.planner = PlannerAgent(planner_llm)
        self.dimension_planner = DimensionPlanner(planner_llm, PERSONAS)
        self.generator = GeneratorAgent(coder_llm)
        self.implementer = DimensionImplementer(coder_llm)
        self.fixer = FixerAgent(coder_llm)
        peers = [RemoteReviewer(url, config.ollama_request_timeout_seconds) for url in peer_urls(config)]
        self.review_coordinator = ReviewCoordinator(reviewer_llm, PERSONAS, RetrieverAgent(), peers)
        self.local_review_coordinator = ReviewCoordinator(reviewer_llm, PERSONAS, RetrieverAgent())
        self.learner = self.build_learner() if config.learning else None
        self.trace = RunTrace("", run_id=run_id)
        self.patch_engine = PatchEngine(timeout_seconds=config.command_timeout_seconds)
        self.validator = Validator(timeout_seconds=config.command_timeout_seconds)

    def status(self, message: str) -> None:
        self.emit("status", {"message": message})

    def build_learner(self) -> Learner | None:
        try:
            store = open_store(self.config.database_url)
            embedder = Embedder(
                self.config.embedding_model,
                self.config.providers_json,
                self.config.ollama_host,
                self.config.ollama_request_timeout_seconds,
            )
        except Exception as exc:  # learning is an enhancement; a broken store must not stop code generation
            logger.warning("learning disabled for this run: %s", exc)
            return None
        return Learner(store, embedder, self.llms["critic"], self.emit)

    def ensure_ready(self) -> None:
        self.status("Checking models...")
        seen: set[int] = set()
        for client in self.llms.values():
            if id(client) not in seen:
                seen.add(id(client))
                client.ensure_ready()

    def run(self, requirement: str, existing_files: dict[str, str] | None = None) -> RunResult:
        """Run the full pipeline; control flow lives in the LangGraph state machine (graph.py)."""
        from .graph import run_pipeline

        self.ensure_ready()
        self.trace = RunTrace(requirement, run_id=self.run_id)
        return run_pipeline(self, requirement, existing_files or {})

    def build_foundation(
        self, requirement: str, plan: dict[str, Any], existing_files: dict[str, str], context: str
    ) -> dict[str, str]:
        self.status("Generating the foundation..." if not self.config.fast_mode else "Generating all dimensions...")
        strategy = self.choose_strategy(PERSONAS[0].name, self.trace)
        focus = FAST_FOCUS if self.config.fast_mode else FOUNDATION_FOCUS
        if strategy:
            focus += f"\nApproach: {strategy['instruction']}"
        generated = self.generator.run(
            requirement, plan, existing_files, context, focus, strategy["temperature"] if strategy else None
        )
        files = merge_files(existing_files, generated)
        self.emit("files", {"files": files, "stage": "generated"})
        return files

    def choose_strategy(self, dimension: str, trace: RunTrace) -> dict[str, Any] | None:
        if not self.learner:
            return None
        strategy = self.learner.choose_strategy(dimension)
        trace.strategies[dimension] = strategy["name"]
        return strategy

    def make_plan(
        self, requirement: str, existing_files: dict[str, str], context: str, lessons: dict[str, list[str]]
    ) -> dict[str, Any]:
        self.status("Planning architecture..." if not existing_files else "Planning changes to existing project...")
        plan = self.planner.run(requirement, existing_files, context)
        self.status(f"Planning {len(PERSONAS)} quality dimensions...")

        def on_step(step: int, dimension_plan: dict[str, Any]) -> None:
            self.emit("dimension_plan", {"step": step, "total": len(PERSONAS), "plan": dimension_plan})

        dimensions = self.dimension_planner.run(requirement, plan, sorted(existing_files), context, on_step, lessons)
        plan = {**plan, "dimensions": dimensions}
        self.emit("plan", {"plan": plan})
        return plan

    def implement_dimensions(
        self,
        requirement: str,
        plan: dict[str, Any],
        files: dict[str, str],
        lessons: dict[str, list[str]],
        trace: RunTrace,
    ) -> dict[str, str]:
        """Step 1 (Business Logic) is the foundation build; steps 2..10 each layer their own changes on top."""
        total = len(PERSONAS)
        for step, persona in enumerate(PERSONAS, start=1):
            if step == 1:
                continue
            dimension_plan = find_dimension_plan(plan, persona.name)
            event = {"step": step, "total": total, "dimension": persona.name, "changed": [], "skipped": True}
            if not dimension_plan or not dimension_plan["applicable"]:
                reason = dimension_plan["reason"] if dimension_plan else "No plan."
                self.emit("dimension_change", {**event, "summary": f"Skipped: {reason}"})
                continue
            self.status(f"Implementing {persona.name} ({step}/{total})...")
            strategy = self.choose_strategy(persona.name, trace)
            try:
                changes, summary = self.implementer.run(
                    persona, requirement, plan, dimension_plan, files, strategy, format_lessons(lessons.get(persona.name, []))
                )
            except ValueError as exc:
                self.emit("dimension_change", {**event, "summary": f"Skipped: model output unusable ({exc})"})
                continue
            files = merge_files(files, changes)
            trace.changes[persona.name] = summary or "Updated files."
            self.emit(
                "dimension_change",
                {**event, "skipped": False, "changed": sorted(changes), "summary": summary or "Updated files."},
            )
            if changes:
                self.emit("files", {"files": files, "stage": f"dimension:{persona.name}"})
        return files

    def review_round(
        self,
        coordinator: ReviewCoordinator,
        requirement: str,
        plan: dict[str, Any],
        files: dict[str, str],
        iteration: int,
    ) -> list[dict[str, Any]]:
        reviews = coordinator.run(
            requirement=requirement,
            plan=plan,
            files=files,
            project_memory=self.memory.project_summary(plan, files),
            max_retrieved_files=self.config.max_retrieved_files,
            on_review=lambda review: self.emit("review", {"review": review}),
        )
        self.memory.remember_reviews(reviews)
        self.emit("scorecard", {"iteration": iteration, "reviews": reviews})
        return reviews

    def review(self, requirement: str, files: dict[str, str]) -> tuple[bool, list[dict[str, Any]]]:
        """Review-only mode (A2A/MCP review skill). Local personas only, so two peers never call each other in a loop."""
        self.llms["reviewer"].ensure_ready()
        self.status("Reviewing files...")
        plan = {"summary": requirement, "files": [{"path": path} for path in files]}
        reviews = self.review_round(self.local_review_coordinator, requirement, plan, files, 1)
        return not any(is_failing(review, self.config.passing_score) for review in reviews), reviews

    def fix(
        self,
        requirement: str,
        plan: dict[str, Any],
        files: dict[str, str],
        lessons: dict[str, list[str]],
        trace: RunTrace,
    ) -> tuple[dict[str, str], bool]:
        """Agentic repair: diagnose the failing command, patch the code, and verify again."""
        self.status("Validation failed. Diagnosing and fixing...")
        relevant = [lesson for lesson in lessons.get(GENERAL, []) if lesson.startswith("[fix]")]
        try:
            changes, diagnosis = self.fixer.run(requirement, plan, files, self.last_verification, format_lessons(relevant))
        except ValueError as exc:
            self.emit("fix", {"diagnosis": f"Fixer output unusable: {exc}", "changed": [], "verified": False})
            return files, False
        if not changes:
            self.emit("fix", {"diagnosis": diagnosis or "No change proposed.", "changed": [], "verified": False})
            return files, False
        files = merge_files(files, changes)
        self.emit("files", {"files": files, "stage": "fixed"})
        verified = self.verify(plan, files, trace)
        trace.fixes.append(f"{diagnosis} ({'fixed' if verified else 'still failing'})")
        self.emit("fix", {"diagnosis": diagnosis, "changed": sorted(changes), "verified": verified})
        return files, verified

    def apply_patches(
        self, files: dict[str, str], reviews: list[dict[str, Any]], trace: RunTrace | None = None
    ) -> dict[str, str]:
        self.status("Applying reviewer patches...")
        result = self.patch_engine.apply(files, collect_patches(reviews))
        self.emit("patches", {"applied": result.applied, "failed": result.failed})
        if trace is not None:
            trace.failed_patches += len(result.failed)
        if result.applied:
            self.emit("files", {"files": result.files, "stage": "patched"})
        if result.failed:
            self.memory.remember_event(
                {
                    "persona": "Patch Engine",
                    "score": 0,
                    "status": "needs_work",
                    "issues": [
                        {
                            "severity": "high",
                            "file": str(patch.get("file", "")),
                            "description": "Patch failed to apply mechanically.",
                            "suggested_fix": str(patch.get("output", ""))[:1000],
                        }
                        for patch in result.failed
                    ],
                    "patches": [],
                }
            )
        return result.files

    def verify(self, plan: dict[str, Any], files: dict[str, str], trace: RunTrace | None = None) -> bool:
        self.status("Verifying project...")
        verification = self.validator.run(plan, files)
        self.last_verification = verification
        self.emit("verify", verification)
        if verification["exit_code"] == 0:
            return True
        if trace is not None:
            trace.verification_failures.append(str(verification["output"]))
        self.memory.remember_event(
            {
                "persona": "Validator",
                "score": 0,
                "status": "needs_work",
                "issues": [
                    {
                        "severity": "critical",
                        "description": "Verification command failed.",
                        "suggested_fix": verification["output"],
                    }
                ],
                "patches": [],
            }
        )
        return False


def print_scorecard(reviews: list[dict]) -> None:
    print("\nQUALITY SCORECARD")
    print("-" * 84)
    print(f"{'Persona':30} {'Score':>5}  {'Status':12} {'Confidence':10} {'Patches':>7}")
    print("-" * 84)
    for review in reviews:
        print(
            f"{str(review.get('persona', 'Unknown'))[:30]:30} "
            f"{str(review.get('score', 0))[:5]:>5}  "
            f"{str(review.get('status', 'unknown'))[:12]:12} "
            f"{str(review.get('confidence', 'unknown'))[:10]:10} "
            f"{len(review.get('patches', [])):>7}"
        )
