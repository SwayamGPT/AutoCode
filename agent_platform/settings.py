"""Settings saved from the UI (~/.autocode/settings.json), layered over environment and defaults.

API keys live inside providers_json. They are written with 0600 permissions, never sent back to the
UI in full (mask_settings), and a masked value coming back from the UI keeps the stored key.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any

from .config import CONFIG_HOME
from .models import PipelineConfig
from .providers import mask_key

SETTINGS_FILE = CONFIG_HOME / "settings.json"
FIELD_TYPES = {"str": str, "int": int, "float": float, "bool": bool}
MASK_PREFIX = "••••"

# Settings a single run (UI or A2A request) may not change: they decide where credentials are sent,
# which commands MCP spawns, and where memory lives. Change them through saved settings instead.
RUN_LOCKED = {"providers_json", "ollama_host", "mcp_config", "database_url", "embedding_model", "learning"}
# Remote agents additionally may not make this server call other URLs.
A2A_LOCKED = RUN_LOCKED | {"a2a_peers"}


def coerce_settings(
    base: PipelineConfig,
    settings: dict[str, Any],
    locked: frozenset[str] | set[str] = frozenset(),
    allow_empty: bool = False,
) -> PipelineConfig:
    """Apply overrides to the base config, coercing each value to the field's declared type."""
    overrides: dict[str, Any] = {}
    for field in dataclasses.fields(base):
        if field.name not in settings:
            continue
        value = settings[field.name]
        if value is None or (value == "" and not allow_empty):
            continue
        if field.name in locked:
            raise ValueError(f"{field.name} can only be changed in saved settings")
        cast = FIELD_TYPES[str(field.type)]
        if cast is bool and not isinstance(value, bool):
            raise ValueError(f"Invalid value for {field.name}: {value!r}")
        try:
            overrides[field.name] = cast(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid value for {field.name}: {value!r}") from exc
    if overrides.get("max_iterations", base.max_iterations) < 1:
        raise ValueError("max_iterations must be at least 1")
    if "providers_json" in overrides:
        validate_providers(overrides["providers_json"])
    return dataclasses.replace(base, **overrides)


def validate_providers(providers_json: str) -> None:
    if not providers_json:
        return
    try:
        providers = json.loads(providers_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid providers JSON: {exc}") from exc
    if not isinstance(providers, dict) or not all(isinstance(v, dict) for v in providers.values()):
        raise ValueError("Providers must be an object of {name: {type, base_url, api_key}}")
    for name, provider in providers.items():
        url = str(provider.get("base_url", ""))
        if url and not url.startswith(("http://", "https://")):
            raise ValueError(f"Provider {name}: base_url must start with http:// or https://")


def load_saved() -> dict[str, Any]:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def apply_saved_settings(config: PipelineConfig) -> PipelineConfig:
    """Saved UI settings override environment defaults. A broken file is ignored, not fatal."""
    try:
        return coerce_settings(config, load_saved(), allow_empty=True)
    except ValueError:
        return config


def unmask_providers(incoming: str, stored: str) -> str:
    """Keep stored API keys where the UI sent back a masked placeholder."""
    if not incoming:
        return incoming
    new = json.loads(incoming)
    old = json.loads(stored) if stored else {}
    for name, provider in new.items():
        key = str(provider.get("api_key", ""))
        if key.startswith(MASK_PREFIX):
            provider["api_key"] = old.get(name, {}).get("api_key", "")
    return json.dumps(new)


def save_settings(current: PipelineConfig, updates: dict[str, Any]) -> PipelineConfig:
    """Validate and persist UI changes; returns the new effective config."""
    updates = dict(updates)
    if "providers_json" in updates and updates["providers_json"]:
        validate_providers(str(updates["providers_json"]))
        updates["providers_json"] = unmask_providers(str(updates["providers_json"]), current.providers_json)
    config = coerce_settings(current, updates, allow_empty=True)
    known = {field.name for field in dataclasses.fields(PipelineConfig)}
    saved = {**load_saved(), **{k: v for k, v in dataclasses.asdict(config).items() if k in updates and k in known}}
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = SETTINGS_FILE.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(saved, handle, indent=2)
    os.replace(temp, SETTINGS_FILE)
    return config


def mask_settings(config: PipelineConfig) -> dict[str, Any]:
    """Config as the UI may see it: every API key masked."""
    data = dataclasses.asdict(config)
    if config.providers_json:
        try:
            providers = json.loads(config.providers_json)
            for provider in providers.values():
                if provider.get("api_key"):
                    provider["api_key"] = mask_key(str(provider["api_key"]))
            data["providers_json"] = json.dumps(providers)
        except (json.JSONDecodeError, AttributeError):
            data["providers_json"] = ""
    return data


def settings_path() -> Path:
    return SETTINGS_FILE
