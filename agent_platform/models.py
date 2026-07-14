from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Persona:
    name: str
    mission: str
    checklist: tuple[str, ...]
    weights: dict[str, int]


@dataclass(frozen=True)
class PipelineConfig:
    model: str
    ollama_host: str
    ollama_request_timeout_seconds: int
    temperature: float
    num_ctx: int
    max_iterations: int
    passing_score: int
    max_retrieved_files: int
    skip_verify: bool
    command_timeout_seconds: int


@dataclass(frozen=True)
class PatchApplyResult:
    files: dict[str, str]
    applied: list[dict[str, Any]]
    failed: list[dict[str, Any]]


@dataclass
class ProjectState:
    requirement: str
    plan: dict[str, Any]
    files: dict[str, str]
    reviews: list[dict[str, Any]] = field(default_factory=list)
    memory_events: list[dict[str, Any]] = field(default_factory=list)
