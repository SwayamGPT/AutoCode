from __future__ import annotations

import json

from .llm import OllamaClient, extract_json, normalize_files


class GeneratorAgent:
    def __init__(self, llm: OllamaClient) -> None:
        self.llm = llm

    def run(self, requirement: str, plan: dict) -> dict[str, str]:
        system_prompt = """
You are a senior code generator.
Create the planned project as multiple files.
Return valid JSON only. No markdown.

JSON schema:
{
  "files": {
    "relative/path.py": "full file content"
  }
}
"""
        user_prompt = f"""
Requirement:
{requirement}

Plan:
{json.dumps(plan, indent=2)}
"""
        payload = extract_json(self.llm.ask(system_prompt, user_prompt, "Generator"))
        return normalize_files(payload.get("files", {}))
