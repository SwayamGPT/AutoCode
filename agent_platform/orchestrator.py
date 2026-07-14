from __future__ import annotations

from .generator import GeneratorAgent
from .llm import OllamaClient
from .memory import MemoryManager
from .models import PipelineConfig
from .patch_engine import PatchEngine, collect_patches
from .personas import PERSONAS
from .planner import PlannerAgent
from .retriever import RetrieverAgent
from .reviewer import ReviewCoordinator
from .validator import Validator


class Orchestrator:
    def __init__(self, config: PipelineConfig) -> None:
        llm = OllamaClient(
            model=config.model,
            host=config.ollama_host,
            request_timeout_seconds=config.ollama_request_timeout_seconds,
            temperature=config.temperature,
            num_ctx=config.num_ctx,
        )
        retriever = RetrieverAgent()
        self.config = config
        self.memory = MemoryManager()
        self.planner = PlannerAgent(llm)
        self.generator = GeneratorAgent(llm)
        self.review_coordinator = ReviewCoordinator(llm, PERSONAS, retriever)
        self.patch_engine = PatchEngine(timeout_seconds=config.command_timeout_seconds)
        self.validator = Validator(timeout_seconds=config.command_timeout_seconds)

    def run(self, requirement: str) -> dict[str, str]:
        print("Checking Ollama/model availability...")
        self.planner.llm.ensure_ready()

        print("Planning architecture...")
        plan = self.planner.run(requirement)

        print("Generating planned files...")
        files = self.generator.run(requirement, plan)

        for iteration in range(1, self.config.max_iterations + 1):
            print(f"\nReview iteration {iteration}/{self.config.max_iterations}")
            project_memory = self.memory.project_summary(plan, files)
            reviews = self.review_coordinator.run(
                requirement=requirement,
                plan=plan,
                files=files,
                project_memory=project_memory,
                max_retrieved_files=self.config.max_retrieved_files,
            )
            self.memory.remember_reviews(reviews)
            print_scorecard(reviews)

            failing_reviews = [
                review
                for review in reviews
                if review.get("status") != "pass"
                or int(review.get("score", 0)) < self.config.passing_score
            ]

            if failing_reviews:
                print("\nApplying reviewer patches...")
                patch_result = self.patch_engine.apply(files, collect_patches(reviews))
                files = patch_result.files
                print(f"  Applied patches: {len(patch_result.applied)}")
                print(f"  Failed patches: {len(patch_result.failed)}")
                if patch_result.failed:
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
                                for patch in patch_result.failed
                            ],
                            "patches": [],
                        }
                    )

            if not self.config.skip_verify:
                print("\nVerifying project...")
                verification = self.validator.run(plan, files)
                print(f"  Command: {verification['command'] or 'none'}")
                print(f"  Exit code: {verification['exit_code']}")
                if verification["output"]:
                    print(verification["output"])
                if verification["exit_code"] != 0:
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
                    continue

            if not failing_reviews:
                print("\nAll quality gates passed.")
                return files

            if iteration == self.config.max_iterations:
                print("\nMax iterations reached. Returning best available files.")
                return files

        return files


def print_scorecard(reviews: list[dict]) -> None:
    print("\nQUALITY SCORECARD")
    print("-" * 84)
    print(f"{'Persona':30} {'Score':>5}  {'Status':12} {'Confidence':10} {'Patches':>7}")
    print("-" * 84)
    for review in reviews:
        print(
            f"{review.get('persona', 'Unknown')[:30]:30} "
            f"{int(review.get('score', 0)):>5}  "
            f"{str(review.get('status', 'unknown'))[:12]:12} "
            f"{str(review.get('confidence', 'unknown'))[:10]:10} "
            f"{len(review.get('patches', [])):>7}"
        )
