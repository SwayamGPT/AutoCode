"""Fixer agent: when the validation command fails, read the failure and repair the code before review."""

from __future__ import annotations

import json
from typing import Any

from .generator import context_files
from .llm import LLMClient, normalize_files
from .config import setting

FIX_CONTEXT_CHARS = setting("FIX_CONTEXT_CHARS", 20_000)
MAX_OUTPUT_CHARS = setting("FIX_OUTPUT_CHARS", 4_000)
MENTIONED_PATH_LIMIT = 20

SYSTEM_PROMPT = """
You are a debugging engineer. The project's validation command failed.
Find the root cause from the output and fix it with the smallest correct change.
Fix the code, not the tests, unless a test itself is wrong.
Return only files you change, each with its complete new content.
Return valid JSON only.

JSON schema:
{
  "diagnosis": "one sentence root cause",
  "files": {"relative/path.py": "full file content"}
}
"""


def mentioned_paths(output: str, files: dict[str, str]) -> list[str]:
    """Files named in the failure output go first in the context."""
    return [path for path in files if path in output][:MENTIONED_PATH_LIMIT]


class FixerAgent:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def run(
        self, requirement: str, plan: dict[str, Any], files: dict[str, str], verification: dict[str, Any], lessons: str = ""
    ) -> tuple[dict[str, str], str]:
        output = str(verification.get("output", ""))[-MAX_OUTPUT_CHARS:]
        priority = mentioned_paths(output, files)
        user_prompt = (
            f"Requirement:\n{requirement}\n\n"
            f"Command: {verification.get('command')}\nExit code: {verification.get('exit_code')}\n"
            f"Output:\n{output}\n\n"
            f"Test command from the plan: {plan.get('test_command', '')}\n\n"
            f"Files:\n{json.dumps(context_files(priority, files, FIX_CONTEXT_CHARS), indent=2)}\n"
        )
        if lessons:
            user_prompt += f"\nLessons from earlier runs:\n{lessons}\n"
        payload = self.llm.ask_json(SYSTEM_PROMPT, user_prompt, "Fix")
        changed = {path: content for path, content in normalize_files(payload.get("files", {})).items() if files.get(path) != content}
        return changed, str(payload.get("diagnosis", ""))[:300]
