from __future__ import annotations

import threading
from typing import Any

from agent_platform.llm import RunCancelled

PATCH = """--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
 def add(a, b):
+    \"\"\"Add two numbers.\"\"\"
     return a + b
"""


def is_review_call(label: str) -> bool:
    return label not in {"Planner", "Generator", "Context Agent"} and not label.startswith(("Plan: ", "Implement: "))


def dimension_plan(name: str) -> dict[str, Any]:
    if name == "Data Management":
        return {"applicable": False, "reason": "No persistent data."}
    files = [{"path": "SECURITY.md", "change": "document threat model"}] if name == "Security" else []
    return {
        "applicable": True,
        "requirements": [f"{name} requirement"],
        "decisions": [f"{name} decision"],
        "files": files,
        "acceptance_checks": [f"{name} check"],
    }


def implementation(name: str) -> dict[str, Any]:
    if name == "Security":
        return {"summary": "Documented the threat model.", "files": {"SECURITY.md": "# Threat model\n"}}
    if name == "Scalability":
        return {"summary": "Tried to escape.", "files": {"../escape.py": "x"}}
    return {"summary": "Nothing to change.", "files": {}}


class FakeLLM:
    """Scripted stand-in for OllamaClient: plans one file, every reviewer passes after one patch round."""

    def __init__(self, review_score: int = 90, patch: bool = True) -> None:
        self.review_score = review_score
        self.patch = patch
        self.calls: list[str] = []
        self.prompts: dict[str, str] = {}
        self.cancel_event = threading.Event()
        self.lock = threading.Lock()

    def ensure_ready(self) -> None:
        pass

    def ask_json(self, system_prompt: str, user_prompt: str, label: str) -> dict[str, Any]:
        if self.cancel_event.is_set():
            raise RunCancelled()
        with self.lock:
            self.calls.append(label)
            self.prompts[label] = user_prompt
            reviews_so_far = sum(1 for call in self.calls if is_review_call(call))
        if label.startswith("Plan: "):
            return dimension_plan(label.removeprefix("Plan: "))
        if label.startswith("Implement: "):
            return implementation(label.removeprefix("Implement: "))
        if label == "Context Agent":
            return {"calls": [{"server": "autocode", "tool": "list_reviewers", "arguments": {}}]}
        if label == "Planner":
            return {
                "summary": "adder",
                "files": [{"path": "app.py", "purpose": "math"}],
                "test_command": "",
            }
        if label == "Generator":
            return {"files": {"app.py": "def add(a, b):\n    return a + b\n"}}
        first_round = reviews_so_far <= 10
        if first_round and self.patch and label == "Maintainability":
            return {"score": 50, "status": "needs_work", "patches": [{"file": "app.py", "unified_diff": PATCH}]}
        if first_round and self.patch:
            return {"score": 50, "status": "needs_work", "patches": []}
        return {"score": self.review_score, "status": "pass", "patches": []}
