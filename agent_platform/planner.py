from __future__ import annotations

from .llm import OllamaClient, extract_json


class PlannerAgent:
    def __init__(self, llm: OllamaClient) -> None:
        self.llm = llm

    def run(self, requirement: str) -> dict:
        system_prompt = """
You are a practical software architect.
Plan a small runnable project for the requirement.
Return valid JSON only.

JSON schema:
{
  "summary": "short project summary",
  "tech_stack": ["python", "fastapi"],
  "architecture": "short architecture description",
  "coding_standards": ["standard"],
  "files": [
    {"path": "relative/file.py", "purpose": "why this file exists"}
  ],
  "test_command": "python -m pytest",
  "run_command": "python app.py"
}
"""
        return extract_json(self.llm.ask(system_prompt, requirement, "Planner"))
