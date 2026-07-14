from __future__ import annotations

import os
from pathlib import Path

from .models import PipelineConfig


def load_env_file(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def env_str(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def env_int(name: str) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return int(value)


def env_float(name: str) -> float:
    value = os.environ.get(name)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return float(value)


def env_bool(name: str) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def config_from_env() -> PipelineConfig:
    return PipelineConfig(
        model=env_str("OLLAMA_MODEL"),
        ollama_host=env_str("OLLAMA_HOST"),
        ollama_request_timeout_seconds=env_int("OLLAMA_REQUEST_TIMEOUT_SECONDS"),
        temperature=env_float("LLM_TEMPERATURE"),
        num_ctx=env_int("LLM_NUM_CTX"),
        max_iterations=env_int("MAX_ITERATIONS"),
        passing_score=env_int("PASSING_SCORE"),
        max_retrieved_files=env_int("MAX_RETRIEVED_FILES"),
        skip_verify=env_bool("SKIP_VERIFY"),
        command_timeout_seconds=env_int("COMMAND_TIMEOUT_SECONDS"),
    )
