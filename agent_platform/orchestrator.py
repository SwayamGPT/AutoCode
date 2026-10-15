from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

from .a2a_client import RemoteReviewer, peer_urls
from .context_agent import ContextAgent
from .dimensions import DimensionImplementer, DimensionPlanner, find_dimension_plan
from .generator import GeneratorAgent
from .llm import OllamaClient
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
        llm: OllamaClient | None = None,
    ) -> None:
        self.emit = emit
        self.llm = llm or OllamaClient(
            model=config.model,
            host=config.ollama_host,
            request_timeout_seconds=config.ollama_request_timeout_seconds,
            temperature=config.temperature,
            num_ctx=config.num_ctx,
            on_call=lambda label: emit("llm_call", {"label": label}),
            on_done=lambda metrics: emit("llm_done", metrics),
            cancel_event=cancel_event,
        )
        self.config = config
        self.memory = MemoryManager()
        self.context_agent = ContextAgent(self.llm, config.mcp_config, config.command_timeout_seconds, emit)
        self.planner = PlannerAgent(self.llm)
        self.dimension_planner = DimensionPlanner(self.llm, PERSONAS)
        self.generator = GeneratorAgent(self.llm)
        self.implementer = DimensionImplementer(self.llm)
        peers = [RemoteReviewer(url, config.ollama_request_timeout_seconds) for url in peer_urls(config)]
        self.review_coordinator = ReviewCoordinator(self.llm, PERSONAS, RetrieverAgent(), peers)
        self.local_review_coordinator = ReviewCoordinator(self.llm, PERSONAS, RetrieverAgent())
        self.patch_engine = PatchEngine(timeout_seconds=config.command_timeout_seconds)
        self.validator = Validator(timeout_seconds=config.command_timeout_seconds)

    def status(self, message: str) -> None:
        self.emit("status", {"message": message})

    def run(self, requirement: str, existing_files: dict[str, str] | None = None) -> RunResult:
        existing_files = existing_files or {}
        self.status("Checking Ollama/model availability...")
        self.llm.ensure_ready()

        context = self.context_agent.gather(requirement, existing_files)
        plan = self.make_plan(requirement, existing_files, context)

        self.status("Generating the foundation..." if not self.config.fast_mode else "Generating all dimensions...")
        focus = FAST_FOCUS if self.config.fast_mode else FOUNDATION_FOCUS
        generated = self.generator.run(requirement, plan, existing_files, context, focus)
        files = merge_files(existing_files, generated)
        self.emit("files", {"files": files, "stage": "generated"})

        if not self.config.fast_mode:
            files = self.implement_dimensions(requirement, plan, files)

        return self.review_loop(requirement, plan, files)

    def make_plan(self, requirement: str, existing_files: dict[str, str], context: str) -> dict[str, Any]:
        self.status("Planning architecture..." if not existing_files else "Planning changes to existing project...")
        plan = self.planner.run(requirement, existing_files, context)
        self.status(f"Planning {len(PERSONAS)} quality dimensions...")

        def on_step(step: int, dimension_plan: dict[str, Any]) -> None:
            self.emit("dimension_plan", {"step": step, "total": len(PERSONAS), "plan": dimension_plan})

        dimensions = self.dimension_planner.run(requirement, plan, sorted(existing_files), context, on_step)
        plan = {**plan, "dimensions": dimensions}
        self.emit("plan", {"plan": plan})
        return plan

    def implement_dimensions(self, requirement: str, plan: dict[str, Any], files: dict[str, str]) -> dict[str, str]:
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
            try:
                changes, summary = self.implementer.run(persona, requirement, plan, dimension_plan, files)
            except ValueError as exc:
                self.emit("dimension_change", {**event, "summary": f"Skipped: model output unusable ({exc})"})
                continue
            files = merge_files(files, changes)
            self.emit(
                "dimension_change",
                {**event, "skipped": False, "changed": sorted(changes), "summary": summary or "Updated files."},
            )
            if changes:
                self.emit("files", {"files": files, "stage": f"dimension:{persona.name}"})
        return files

    def review_loop(self, requirement: str, plan: dict[str, Any], files: dict[str, str]) -> RunResult:
        best: Snapshot | None = None
        for iteration in range(1, self.config.max_iterations + 1):
            self.status(f"Review iteration {iteration}/{self.config.max_iterations}")
            # Verify first so reviewers see test failures in project memory.
            verified = self.config.skip_verify or self.verify(plan, files)
            reviews = self.review_round(self.review_coordinator, requirement, plan, files, iteration)
            passed = verified and not any(is_failing(r, self.config.passing_score) for r in reviews)
            snapshot = Snapshot(iteration, files, reviews, verified, passed)
            if best is None or snapshot.rank > best.rank:
                best = snapshot
            if passed:
                self.status("All quality gates passed.")
                return RunResult(files=files, plan=plan, reviews=reviews, passed=True)
            if iteration < self.config.max_iterations:
                files = self.apply_patches(files, reviews)

        assert best is not None  # max_iterations >= 1
        if best.files is not files:
            self.emit("files", {"files": best.files, "stage": f"best:iteration {best.iteration}"})
        self.status(f"Max iterations reached. Returning the best reviewed version (iteration {best.iteration}).")
        return RunResult(files=best.files, plan=plan, reviews=best.reviews, passed=False)

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
        self.llm.ensure_ready()
        self.status("Reviewing files...")
        plan = {"summary": requirement, "files": [{"path": path} for path in files]}
        reviews = self.review_round(self.local_review_coordinator, requirement, plan, files, 1)
        return not any(is_failing(review, self.config.passing_score) for review in reviews), reviews

    def apply_patches(self, files: dict[str, str], reviews: list[dict[str, Any]]) -> dict[str, str]:
        self.status("Applying reviewer patches...")
        result = self.patch_engine.apply(files, collect_patches(reviews))
        self.emit("patches", {"applied": result.applied, "failed": result.failed})
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

    def verify(self, plan: dict[str, Any], files: dict[str, str]) -> bool:
        self.status("Verifying project...")
        verification = self.validator.run(plan, files)
        self.emit("verify", verification)
        if verification["exit_code"] == 0:
            return True
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
