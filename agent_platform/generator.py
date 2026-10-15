from __future__ import annotations

import json

from .llm import OllamaClient, normalize_files

MAX_CONTEXT_CHARS = 24_000

SYSTEM_PROMPT = """
You are a senior code generator.
Create the planned project as multiple files.
If existing files are provided, return only files you create or change,
each with its complete new content. Do not return unchanged files.
Return valid JSON only. No markdown.

JSON schema:
{
  "files": {
    "relative/path.py": "full file content"
  }
}
"""


def context_files(plan: dict, existing_files: dict[str, str]) -> dict[str, str]:
    """Pick existing files the plan touches, then others, until the character budget is spent."""
    planned = [str(item.get("path", "")) for item in plan.get("files", []) if isinstance(item, dict)]
    ordered = [path for path in planned if path in existing_files]
    ordered += [path for path in sorted(existing_files) if path not in ordered]
    selected: dict[str, str] = {}
    budget = MAX_CONTEXT_CHARS
    for path in ordered:
        content = existing_files[path]
        if len(content) > budget:
            continue
        selected[path] = content
        budget -= len(content)
    return selected


class GeneratorAgent:
    def __init__(self, llm: OllamaClient) -> None:
        self.llm = llm

    def run(
        self,
        requirement: str,
        plan: dict,
        existing_files: dict[str, str] | None = None,
        context: str = "",
    ) -> dict[str, str]:
        existing_files = existing_files or {}
        user_prompt = f"""
Requirement:
{requirement}

Plan:
{json.dumps(plan, indent=2)}
"""
        if existing_files:
            user_prompt += f"\nExisting files:\n{json.dumps(context_files(plan, existing_files), indent=2)}\n"
        if context:
            user_prompt += f"\nContext gathered from tools:\n{context}\n"
        payload = self.llm.ask_json(SYSTEM_PROMPT, user_prompt, "Generator")
        return {**existing_files, **normalize_files(payload.get("files", {}))}
