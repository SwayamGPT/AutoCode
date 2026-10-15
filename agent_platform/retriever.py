from __future__ import annotations

import json
import re

from .models import Persona

PRIORITY_BOOST = 25  # files the persona's own dimension plan touches


class RetrieverAgent:
    def file_tokens(self, path: str, content: str) -> set[str]:
        words = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", path.lower()))
        words.update(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", content.lower()))
        return words

    def retrieve(
        self,
        persona: Persona,
        requirement: str,
        plan: dict,
        files: dict[str, str],
        max_files: int,
        priority_paths: list[str] | None = None,
    ) -> dict[str, str]:
        priority = set(priority_paths or [])
        query = " ".join(
            [
                persona.name,
                persona.mission,
                " ".join(persona.checklist),
                requirement,
                json.dumps(plan.get("files", [])),
            ]
        ).lower()
        query_tokens = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", query))

        scored: list[tuple[int, str]] = []
        for path, content in files.items():
            score = len(query_tokens & self.file_tokens(path, content))
            if path in priority:
                score += PRIORITY_BOOST
            if path.endswith((".env", ".sql", ".json", ".yaml", ".yml", ".toml")):
                score += 2
            if persona.name == "Testing & Quality Assurance" and "test" in path.lower():
                score += 10
            if persona.name == "Data Management" and any(
                token in path.lower() for token in ("db", "data", "schema", "model")
            ):
                score += 10
            scored.append((score, path))

        selected = [path for _, path in sorted(scored, reverse=True)[:max_files]]
        return {path: files[path] for path in selected}

