"""Backend services behind the settings, setup and learning screens: health checks, model downloads,
model lists, and read/clear access to memory and learned strategies."""

from __future__ import annotations

import shutil
import threading
from typing import Any

from .learning import STRATEGIES, Embedder
from .memory_store import open_store
from .models import PipelineConfig
from .providers import OLLAMA, PRESETS, OllamaProvider, build_provider, parse_spec, provider_configs

HEALTH_TIMEOUT_SECONDS = 3
PULL_TIMEOUT_SECONDS = 3600
ROLE_FIELDS = {"planner": "planner_model", "coder": "coder_model", "reviewer": "reviewer_model", "critic": "critic_model"}


def model_specs(config: PipelineConfig) -> dict[str, list[str]]:
    """spec -> roles using it, so each distinct model is checked once."""
    specs: dict[str, list[str]] = {}
    for role, field in ROLE_FIELDS.items():
        specs.setdefault(getattr(config, field) or config.model, []).append(role)
    return specs


def check_model(config: PipelineConfig, spec: str) -> dict[str, Any]:
    configs = provider_configs(config.providers_json, config.ollama_host)
    provider_config, model = parse_spec(spec, configs)
    status = {"spec": spec, "provider": provider_config.name, "model": model, "ok": False, "message": "", "pullable": False}
    try:
        build_provider(provider_config, HEALTH_TIMEOUT_SECONDS).check(model)
        status["ok"] = True
    except Exception as exc:  # unreachable, missing key, model not pulled, ...
        status["message"] = str(exc)[:300]
        status["pullable"] = provider_config.type == OLLAMA and "not pulled" in status["message"]
        if provider_config.type == OLLAMA and status["pullable"]:
            status["pullable"] = ollama_reachable(provider_config.base_url)
    return status


def ollama_reachable(base_url: str) -> bool:
    try:
        from ollama import Client

        Client(host=base_url, timeout=HEALTH_TIMEOUT_SECONDS).list()
        return True
    except Exception:
        return False


def check_health(config: PipelineConfig) -> dict[str, Any]:
    models = []
    for spec, roles in model_specs(config).items():
        models.append({**check_model(config, spec), "roles": roles})
    embedding = check_model(config, config.embedding_model) if config.embedding_model not in ("", "hash") else None
    try:
        store = open_store(config.database_url)
        memory = {"backend": store.backend, "count": store.count(), "ok": True}
        wants_postgres = config.database_url.startswith(("postgres://", "postgresql://"))
        if wants_postgres and store.backend != "postgres":
            memory = {**memory, "ok": False, "message": "DATABASE_URL is set but Postgres is unreachable; using a local file."}
    except Exception as exc:
        memory = {"backend": "none", "count": 0, "ok": False, "message": str(exc)[:300]}
    git = shutil.which("git") is not None
    return {
        "ok": git and all(m["ok"] for m in models),
        "git": git,
        "models": models,
        "embedding": embedding,
        "memory": memory,
        "learning": config.learning,
    }


def list_models(config: PipelineConfig, provider_name: str) -> list[str]:
    configs = provider_configs(config.providers_json, config.ollama_host)
    if provider_name not in configs:
        raise ValueError(f"Unknown provider {provider_name!r}")
    return build_provider(configs[provider_name], HEALTH_TIMEOUT_SECONDS * 3).list_models()


def presets_view(config: PipelineConfig) -> dict[str, Any]:
    configs = provider_configs(config.providers_json, config.ollama_host)
    return {
        name: {
            "type": configs[name].type,
            "base_url": configs[name].base_url,
            "example": PRESETS.get(name, {}).get("example", ""),
            "key_env": PRESETS.get(name, {}).get("key_env", ""),
            "has_key": bool(configs[name].api_key),
        }
        for name in configs
    }


class ModelPulls:
    """Background `ollama pull` with progress the UI can poll; one pull per model at a time."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.progress: dict[str, dict[str, Any]] = {}

    def start(self, config: PipelineConfig, spec: str) -> dict[str, Any]:
        configs = provider_configs(config.providers_json, config.ollama_host)
        provider_config, model = parse_spec(spec, configs)
        if provider_config.type != OLLAMA:
            raise ValueError("Only Ollama models can be downloaded from here")
        with self.lock:
            current = self.progress.get(spec)
            if current and not current["done"]:
                return current
            self.progress[spec] = {"spec": spec, "status": "starting", "completed": 0, "total": 0, "done": False, "error": ""}
        provider = OllamaProvider(provider_config, PULL_TIMEOUT_SECONDS)
        threading.Thread(target=self.run, args=(provider, spec, model), name=f"pull-{model}", daemon=True).start()
        return self.progress[spec]

    def run(self, provider: OllamaProvider, spec: str, model: str) -> None:
        try:
            for update in provider.pull(model):
                with self.lock:
                    self.progress[spec] = {
                        **self.progress[spec],
                        "status": str(getattr(update, "status", "") or ""),
                        "completed": int(getattr(update, "completed", 0) or 0),
                        "total": int(getattr(update, "total", 0) or 0),
                    }
            final = {"status": "success", "done": True}
        except Exception as exc:
            final = {"status": "failed", "done": True, "error": str(exc)[:300]}
        with self.lock:
            self.progress[spec] = {**self.progress[spec], **final}

    def snapshot(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.progress.values())


def memory_view(config: PipelineConfig, query: str, limit: int, offset: int) -> dict[str, Any]:
    store = open_store(config.database_url)
    if query:
        embedder = Embedder(config.embedding_model, config.providers_json, config.ollama_host, HEALTH_TIMEOUT_SECONDS * 3)
        [vector], model = embedder.embed([query])
        items = store.search(vector, model, None, limit)
    else:
        items = store.recent(limit, offset)
    return {"backend": store.backend, "count": store.count(), "items": [m.public() for m in items]}


def learning_view(config: PipelineConfig) -> dict[str, Any]:
    store = open_store(config.database_url)
    return {
        "backend": store.backend,
        "strategies": {name: strategy["instruction"] for name, strategy in STRATEGIES.items()},
        "stats": store.all_arm_stats(),
    }
