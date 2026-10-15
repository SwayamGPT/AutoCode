from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, TypeVar

from .models import PipelineConfig

T = TypeVar("T")

# AUTOCODE_HOME relocates settings, logs and run history (tests point it at a temp dir).
CONFIG_HOME = Path(os.environ.get("AUTOCODE_HOME") or Path.home() / ".autocode")

DEFAULTS = {
    "OLLAMA_MODEL": "qwen3:4b",
    "OLLAMA_HOST": "http://localhost:11434",
    "OLLAMA_REQUEST_TIMEOUT_SECONDS": "600",
    "LLM_TEMPERATURE": "0.2",
    "LLM_NUM_CTX": "8192",
    "MAX_ITERATIONS": "2",
    "PASSING_SCORE": "85",
    "MAX_RETRIEVED_FILES": "5",
    "SKIP_VERIFY": "false",
    "COMMAND_TIMEOUT_SECONDS": "60",
    "MCP_CONFIG": str(CONFIG_HOME / "mcp.json"),
    "A2A_PEERS": "",
    "FAST_MODE": "false",
}


def load_env_files() -> None:
    """Project .env first, then ~/.autocode/.env (used by the packaged app). Real env vars win over both."""
    load_env_file(".env")
    load_env_file(str(CONFIG_HOME / ".env"))


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


def env_value(name: str, parse: Callable[[str], T]) -> T:
    value = os.environ.get(name) or DEFAULTS[name]
    try:
        return parse(value)
    except ValueError as exc:
        raise RuntimeError(f"Invalid value for {name}: {value!r}") from exc


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def config_from_env() -> PipelineConfig:
    return PipelineConfig(
        model=env_value("OLLAMA_MODEL", str),
        ollama_host=env_value("OLLAMA_HOST", str),
        ollama_request_timeout_seconds=env_value("OLLAMA_REQUEST_TIMEOUT_SECONDS", int),
        temperature=env_value("LLM_TEMPERATURE", float),
        num_ctx=env_value("LLM_NUM_CTX", int),
        max_iterations=env_value("MAX_ITERATIONS", int),
        passing_score=env_value("PASSING_SCORE", int),
        max_retrieved_files=env_value("MAX_RETRIEVED_FILES", int),
        skip_verify=env_value("SKIP_VERIFY", parse_bool),
        command_timeout_seconds=env_value("COMMAND_TIMEOUT_SECONDS", int),
        mcp_config=env_value("MCP_CONFIG", str),
        a2a_peers=env_value("A2A_PEERS", str),
        fast_mode=env_value("FAST_MODE", parse_bool),
    )
