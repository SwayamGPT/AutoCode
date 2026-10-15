"""Ten-step dimension pipeline.

Planning: each quality dimension (personas.py, in order) writes its own plan, seeing the base plan and the
decisions of every earlier dimension. Implementation: each applicable dimension then makes its own code
changes on top of the previous ones. Reviewers later score each dimension against its plan.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .generator import context_files
from .llm import OllamaClient, normalize_files
from .models import Persona

MAX_ITEMS = 8
MAX_ITEM_CHARS = 300
IMPLEMENT_CONTEXT_CHARS = 20_000

PLAN_SYSTEM_PROMPT = """
You are the {name} lead on a software team. Your dimension: {mission}
Checklist: {checklist}

Plan what this project needs for your dimension only. Be concrete and proportionate to the project:
a small script does not need a database or alerting. If your dimension genuinely does not apply,
set "applicable" to false and explain why. Do not undo earlier dimensions' decisions.
Acceptance checks must be verifiable by reading the code (e.g. "passwords hashed with bcrypt in auth.py").
Return valid JSON only.

JSON schema:
{{
  "applicable": true,
  "reason": "why it does not apply (only when applicable is false)",
  "requirements": ["concrete requirement"],
  "decisions": ["design decision and the reason"],
  "files": [{{"path": "relative/path.py", "change": "what changes here for this dimension"}}],
  "acceptance_checks": ["check a reviewer can confirm in the code"]
}}
"""

IMPLEMENT_SYSTEM_PROMPT = """
You are the {name} engineer. Your dimension: {mission}
Implement your dimension's plan in the existing code. Change only what your dimension needs,
keep everything earlier dimensions built working, and keep the code consistent in style.
Return only files you create or change, each with its complete new content.
Return valid JSON only. No markdown.

JSON schema:
{{
  "summary": "one sentence on what you changed",
  "files": {{"relative/path.py": "full file content"}}
}}
"""


def string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:MAX_ITEM_CHARS] for item in value if str(item).strip()][:MAX_ITEMS]


def normalize_dimension_plan(persona: Persona, raw: dict[str, Any]) -> dict[str, Any]:
    files = raw.get("files") if isinstance(raw.get("files"), list) else []
    applicable = raw.get("applicable") is not False
    return {
        "dimension": persona.name,
        "applicable": applicable,
        "reason": "" if applicable else str(raw.get("reason") or "Not applicable to this project.")[:MAX_ITEM_CHARS],
        "requirements": string_list(raw.get("requirements")),
        "decisions": string_list(raw.get("decisions")),
        "files": [
            {"path": str(item["path"]), "change": str(item.get("change", ""))[:MAX_ITEM_CHARS]}
            for item in files
            if isinstance(item, dict) and item.get("path")
        ][:MAX_ITEMS],
        "acceptance_checks": string_list(raw.get("acceptance_checks")),
    }


def fallback_plan(persona: Persona, error: str) -> dict[str, Any]:
    """Keep the pipeline moving when a planning call fails: fall back to the persona's checklist."""
    return {
        "dimension": persona.name,
        "applicable": True,
        "reason": "",
        "requirements": list(persona.checklist),
        "decisions": [],
        "files": [],
        "acceptance_checks": list(persona.checklist),
        "planning_error": error[:MAX_ITEM_CHARS],
    }


def prior_decisions(plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact view of earlier dimensions so later steps stay consistent without blowing the context."""
    return [
        {"dimension": plan["dimension"], "applicable": plan["applicable"], "decisions": plan["decisions"]}
        for plan in plans
    ]


def find_dimension_plan(plan: dict[str, Any], name: str) -> dict[str, Any] | None:
    for dimension in plan.get("dimensions", []):
        if isinstance(dimension, dict) and dimension.get("dimension") == name:
            return dimension
    return None


class DimensionPlanner:
    def __init__(self, llm: OllamaClient, personas: tuple[Persona, ...]) -> None:
        self.llm = llm
        self.personas = personas

    def run(
        self,
        requirement: str,
        base_plan: dict[str, Any],
        existing_paths: list[str],
        context: str,
        on_step: Callable[[int, dict[str, Any]], None],
    ) -> list[dict[str, Any]]:
        plans: list[dict[str, Any]] = []
        for step, persona in enumerate(self.personas, start=1):
            system_prompt = PLAN_SYSTEM_PROMPT.format(
                name=persona.name, mission=persona.mission, checklist=", ".join(persona.checklist)
            )
            user_prompt = (
                f"Requirement:\n{requirement}\n\n"
                f"Base plan:\n{json.dumps(base_plan, indent=2)}\n\n"
                f"Existing files:\n{json.dumps(existing_paths)}\n\n"
                f"Earlier dimensions:\n{json.dumps(prior_decisions(plans), indent=2)}\n"
            )
            if context:
                user_prompt += f"\nContext gathered from tools:\n{context}\n"
            try:
                raw = self.llm.ask_json(system_prompt, user_prompt, f"Plan: {persona.name}")
                dimension_plan = normalize_dimension_plan(persona, raw)
            except ValueError as exc:
                dimension_plan = fallback_plan(persona, str(exc))
            plans.append(dimension_plan)
            on_step(step, dimension_plan)
        return plans


class DimensionImplementer:
    def __init__(self, llm: OllamaClient) -> None:
        self.llm = llm

    def run(
        self,
        persona: Persona,
        requirement: str,
        plan: dict[str, Any],
        dimension_plan: dict[str, Any],
        files: dict[str, str],
    ) -> tuple[dict[str, str], str]:
        """Returns (changed files, summary). Raises ValueError when the model output is unusable."""
        priority = [item["path"] for item in dimension_plan["files"]]
        user_prompt = (
            f"Requirement:\n{requirement}\n\n"
            f"Project summary:\n{plan.get('summary', '')}\n{plan.get('architecture', '')}\n\n"
            f"Your dimension plan:\n{json.dumps(dimension_plan, indent=2)}\n\n"
            f"Current files:\n{json.dumps(context_files(priority, files, IMPLEMENT_CONTEXT_CHARS), indent=2)}\n"
        )
        system_prompt = IMPLEMENT_SYSTEM_PROMPT.format(name=persona.name, mission=persona.mission)
        payload = self.llm.ask_json(system_prompt, user_prompt, f"Implement: {persona.name}")
        changed = {
            path: content
            for path, content in normalize_files(payload.get("files", {})).items()
            if files.get(path) != content
        }
        return changed, str(payload.get("summary", ""))[:MAX_ITEM_CHARS]
