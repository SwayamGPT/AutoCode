from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .llm import OllamaClient, extract_json
from .models import Persona
from .retriever import RetrieverAgent


class ReviewerAgent:
    def __init__(self, llm: OllamaClient, persona: Persona) -> None:
        self.llm = llm
        self.persona = persona

    def run(self, requirement: str, project_memory: dict[str, Any], relevant_files: dict[str, str]) -> dict[str, Any]:
        persona = self.persona
        system_prompt = f"""
Role: Senior {persona.name} Reviewer

Mission:
{persona.mission}

Rules:
- Review only your domain.
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
        return extract_json(self.llm.ask(system_prompt, user_prompt, persona.name))


class ReviewCoordinator:
    def __init__(self, llm: OllamaClient, personas: tuple[Persona, ...], retriever: RetrieverAgent) -> None:
        self.llm = llm
        self.personas = personas
        self.retriever = retriever

    def run(
        self,
        requirement: str,
        plan: dict,
        files: dict[str, str],
        project_memory: dict[str, Any],
        max_retrieved_files: int,
    ) -> list[dict[str, Any]]:
        reviews: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=len(self.personas)) as executor:
            futures = {}
            for persona in self.personas:
                relevant_files = self.retriever.retrieve(
                    persona, requirement, plan, files, max_retrieved_files
                )
                agent = ReviewerAgent(self.llm, persona)
                future = executor.submit(agent.run, requirement, project_memory, relevant_files)
                futures[future] = persona.name

            for future in as_completed(futures):
                persona_name = futures[future]
                print(f"  Finished: {persona_name}")
                try:
                    reviews.append(future.result())
                except Exception as exc:
                    reviews.append(
                        {
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
                    )

        return sorted(reviews, key=lambda item: item.get("persona", ""))
