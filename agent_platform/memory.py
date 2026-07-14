from __future__ import annotations

from typing import Any


class MemoryManager:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def remember_reviews(self, reviews: list[dict[str, Any]]) -> None:
        self.events.extend(reviews)

    def remember_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def project_summary(self, plan: dict[str, Any], files: dict[str, str]) -> dict[str, Any]:
        return {
            "summary": plan.get("summary", ""),
            "architecture": plan.get("architecture", ""),
            "tech_stack": plan.get("tech_stack", []),
            "coding_standards": plan.get("coding_standards", []),
            "files": [
                {"path": path, "line_count": content.count("\n") + 1}
                for path, content in files.items()
            ],
            "recent_memory": self.events[-20:],
        }

