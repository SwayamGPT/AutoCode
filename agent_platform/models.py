from __future__ import annotations

from dataclasses import dataclass
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
    mcp_config: str = ""
    a2a_peers: str = ""
    fast_mode: bool = False
    # Models are "<provider>:<model>" specs (see providers.py); empty role models fall back to `model`.
    providers_json: str = ""
    planner_model: str = ""
    coder_model: str = ""
    reviewer_model: str = ""
    critic_model: str = ""
    embedding_model: str = "ollama:nomic-embed-text"
    # Memory: empty = SQLite under AUTOCODE_HOME; postgresql://... = Postgres + pgvector.
    database_url: str = ""
    learning: bool = True


@dataclass(frozen=True)
class PatchApplyResult:
    files: dict[str, str]
    applied: list[dict[str, Any]]
    failed: list[dict[str, Any]]


@dataclass(frozen=True)
class RunResult:
    files: dict[str, str]
    plan: dict[str, Any]
    reviews: list[dict[str, Any]]
    passed: bool
