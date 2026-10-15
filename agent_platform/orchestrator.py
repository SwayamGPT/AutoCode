from __future__ import annotations

import threading
from typing import Any, Callable

from .a2a_client import RemoteReviewer, peer_urls
from .context_agent import ContextAgent
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

Emit = Callable[[str, dict[str, Any]], None]


def console_emit(kind: str, data: dict[str, Any]) -> None:
    if kind == "status":
        print(data["message"], flush=True)
    elif kind == "llm_call":
        print(f"  LLM call: {data['label']}", flush=True)
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


def is_failing(review: dict[str, Any], passing_score: int) -> bool:
    try:
        score = int(review.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    return review.get("status") != "pass" or score < passing_score


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
            cancel_event=cancel_event,
        )
        self.config = config
        self.memory = MemoryManager()
        self.context_agent = ContextAgent(self.llm, config.mcp_config, config.command_timeout_seconds, emit)
        self.planner = PlannerAgent(self.llm)
        self.generator = GeneratorAgent(self.llm)
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

        self.status("Planning architecture..." if not existing_files else "Planning changes to existing project...")
        plan = self.planner.run(requirement, existing_files, context)
        self.emit("plan", {"plan": plan})

        self.status("Generating planned files...")
        files = self.generator.run(requirement, plan, existing_files, context)
        self.emit("files", {"files": files, "stage": "generated"})

        reviews: list[dict[str, Any]] = []
        for iteration in range(1, self.config.max_iterations + 1):
            self.status(f"Review iteration {iteration}/{self.config.max_iterations}")
            reviews = self.review_round(self.review_coordinator, requirement, plan, files, iteration)

            failing = [review for review in reviews if is_failing(review, self.config.passing_score)]
            if failing:
                files = self.apply_patches(files, reviews)

            verified = self.config.skip_verify or self.verify(plan, files)
            if not failing and verified:
                self.status("All quality gates passed.")
                return RunResult(files=files, plan=plan, reviews=reviews, passed=True)

        self.status("Max iterations reached. Returning best available files.")
        return RunResult(files=files, plan=plan, reviews=reviews, passed=False)

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
