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


def planned_paths(items: object) -> list[str]:
    """Paths from a plan's `files` list ([{"path": ...}, ...]); tolerant of malformed model output."""
    if not isinstance(items, list):
        return []
    return [str(item.get("path", "")) for item in items if isinstance(item, dict) and item.get("path")]


def context_files(
    priority_paths: list[str], existing_files: dict[str, str], budget: int = MAX_CONTEXT_CHARS
) -> dict[str, str]:
    """Pick priority files first, then the rest, until the character budget is spent."""
    ordered = [path for path in priority_paths if path in existing_files]
    ordered += [path for path in sorted(existing_files) if path not in ordered]
    selected: dict[str, str] = {}
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
        focus: str = "",
    ) -> dict[str, str]:
        existing_files = existing_files or {}
        user_prompt = f"""
Requirement:
{requirement}

Plan:
{json.dumps(plan, indent=2)}
"""
        if focus:
            user_prompt += f"\nFocus:\n{focus}\n"
        if existing_files:
            selected = context_files(planned_paths(plan.get("files")), existing_files)
            user_prompt += f"\nExisting files:\n{json.dumps(selected, indent=2)}\n"
        if context:
            user_prompt += f"\nContext gathered from tools:\n{context}\n"
        payload = self.llm.ask_json(SYSTEM_PROMPT, user_prompt, "Generator")
        return {**existing_files, **normalize_files(payload.get("files", {}))}
