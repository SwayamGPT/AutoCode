from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Protocol

from .dimensions import find_dimension_plan
from .llm import OllamaClient, RunCancelled
from .models import Persona
from .retriever import RetrieverAgent


class ReviewerAgent:
    def __init__(self, llm: OllamaClient, persona: Persona) -> None:
        self.llm = llm
        self.persona = persona

    def run(
        self,
        requirement: str,
        project_memory: dict[str, Any],
        relevant_files: dict[str, str],
        dimension_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        persona = self.persona
        system_prompt = f"""
Role: Senior {persona.name} Reviewer

Mission:
{persona.mission}

Rules:
- Review only your domain.
- If a dimension plan is given, score mainly on whether its requirements and acceptance checks are met.
- If the plan marks your dimension not applicable and the code does not need it, pass it.
- Prefer minimal patches over broad rewrites.
- Patches must target specific files.
- Every unified_diff must be directly applicable by git apply.
- Use standard headers: --- a/path and +++ b/path.
- Include enough context lines for each hunk.
- If no change is needed, return an empty patches array.
- Return valid JSON only.

Checklist:
{chr(10).join(f"- {item}" for item in persona.checklist)}

Weighted scoring dimensions:
{json.dumps(persona.weights, indent=2)}

JSON schema:
{{
  "persona": "{persona.name}",
  "score": 0,
  "weighted_scores": {{"dimension": 0}},
  "status": "pass | needs_work",
  "confidence": "low | medium | high",
  "issues": [
    {{
      "severity": "low | medium | high | critical",
      "file": "relative/path.py",
      "description": "specific problem",
      "suggested_fix": "specific fix"
    }}
  ],
  "patches": [
    {{
      "file": "relative/path.py",
      "reason": "why this change is needed",
      "unified_diff": "--- a/relative/path.py\\n+++ b/relative/path.py\\n@@ ...",
      "risk": "low | medium | high"
    }}
  ],
  "requires_rereview": false
}}
"""
        user_prompt = f"""
Requirement:
{requirement}

Project memory:
{json.dumps(project_memory, indent=2)}

Relevant files:
{json.dumps(relevant_files, indent=2)}
"""
        if dimension_plan:
            user_prompt += f"\nYour dimension plan:\n{json.dumps(dimension_plan, indent=2)}\n"
        return self.llm.ask_json(system_prompt, user_prompt, persona.name)


class Reviewer(Protocol):
    name: str

    def run(
        self,
        requirement: str,
        project_memory: dict[str, Any],
        relevant_files: dict[str, str],
        dimension_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class ReviewCoordinator:
    def __init__(
        self,
        llm: OllamaClient,
        personas: tuple[Persona, ...],
        retriever: RetrieverAgent,
        remote_reviewers: list[Reviewer] | None = None,
    ) -> None:
        self.llm = llm
        self.personas = personas
        self.retriever = retriever
        self.remote_reviewers = remote_reviewers or []

    def run(
        self,
        requirement: str,
        plan: dict,
        files: dict[str, str],
        project_memory: dict[str, Any],
        max_retrieved_files: int,
        on_review: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[dict[str, Any]]:
        reviews: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=len(self.personas) + len(self.remote_reviewers)) as executor:
            futures = {}
            for persona in self.personas:
                dimension_plan = find_dimension_plan(plan, persona.name)
                priority = [item["path"] for item in dimension_plan["files"]] if dimension_plan else []
                relevant_files = self.retriever.retrieve(
                    persona, requirement, plan, files, max_retrieved_files, priority
                )
                agent = ReviewerAgent(self.llm, persona)
                future = executor.submit(agent.run, requirement, project_memory, relevant_files, dimension_plan)
                futures[future] = persona.name
            for remote in self.remote_reviewers:
                # Peers run their own retrieval, so they get every file.
                futures[executor.submit(remote.run, requirement, project_memory, files)] = remote.name

            for future in as_completed(futures):
                persona_name = futures[future]
                try:
                    review = future.result()
                    review["persona"] = persona_name
                except RunCancelled:
                    raise
                except Exception as exc:
                    review = {
                        "persona": persona_name,
                        "score": 0,
                        "status": "needs_work",
                        "confidence": "low",
                        "issues": [
                            {
                                "severity": "high",
                                "file": "",
                                "description": f"Reviewer failed: {exc}",
                                "suggested_fix": "Retry with smaller context or stricter JSON output.",
                            }
                        ],
                        "patches": [],
                        "requires_rereview": True,
                    }
                reviews.append(review)
                if on_review:
                    on_review(review)

        return sorted(reviews, key=lambda item: item.get("persona", ""))
