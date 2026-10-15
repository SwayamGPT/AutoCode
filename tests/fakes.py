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


class FakeLLM:
    """Scripted stand-in for OllamaClient: plans one file, every reviewer passes after one patch round."""

    def __init__(self, review_score: int = 90, patch: bool = True) -> None:
        self.review_score = review_score
        self.patch = patch
        self.calls: list[str] = []
        self.cancel_event = threading.Event()
        self.lock = threading.Lock()

    def ensure_ready(self) -> None:
        pass

    def ask_json(self, system_prompt: str, user_prompt: str, label: str) -> dict[str, Any]:
        if self.cancel_event.is_set():
            raise RunCancelled()
        with self.lock:
            self.calls.append(label)
            reviews_so_far = sum(1 for call in self.calls if call not in {"Planner", "Generator", "Context Agent"})
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
