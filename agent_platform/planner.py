from __future__ import annotations

from .llm import OllamaClient

SYSTEM_PROMPT = """
You are a practical software architect.
Plan a small runnable project for the requirement.
If an existing project is listed, plan a change to it: list only files to create or modify,
and keep its existing tech stack and conventions.
Return valid JSON only.

JSON schema:
{
  "summary": "short project summary",
  "tech_stack": ["python", "fastapi"],
  "architecture": "short architecture description",
  "coding_standards": ["standard"],
  "files": [
    {"path": "relative/file.py", "purpose": "why this file exists or changes"}
  ],
  "test_command": "python -m pytest",
  "run_command": "python app.py"
}
"""


class PlannerAgent:
    def __init__(self, llm: OllamaClient) -> None:
        self.llm = llm

    def run(self, requirement: str, existing_files: dict[str, str] | None = None, context: str = "") -> dict:
        user_prompt = f"Requirement:\n{requirement}\n"
        if existing_files:
            tree = "\n".join(f"- {path}" for path in sorted(existing_files))
            user_prompt += f"\nExisting project files:\n{tree}\n"
        if context:
            user_prompt += f"\nContext gathered from tools:\n{context}\n"
        return self.llm.ask_json(SYSTEM_PROMPT, user_prompt, "Planner")
