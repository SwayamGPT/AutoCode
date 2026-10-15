from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Callable

from .providers import Provider
from .config import setting

JSON_RETRIES = setting("LLM_JSON_RETRIES", 1)


class RunCancelled(Exception):
    pass


class LLMClient:
    """One model behind one provider, with the call accounting and cancellation every agent relies on."""

    def __init__(
        self,
        provider: Provider,
        model: str,
        temperature: float,
        num_ctx: int,
        on_call: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
        on_done: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.on_call = on_call or (lambda label: print(f"  LLM call: {label} ({model})", flush=True))
        self.cancel_event = cancel_event or threading.Event()
        self.on_done = on_done or (lambda metrics: None)

    @property
    def spec(self) -> str:
        return f"{self.provider.config.name}:{self.model}"

    def ensure_ready(self) -> None:
        self.provider.check(self.model)

    def ask(
        self,
        system_prompt: str,
        user_prompt: str,
        label: str,
        json_mode: bool = False,
        temperature: float | None = None,
    ) -> str:
        if self.cancel_event.is_set():
            raise RunCancelled()
        self.on_call(label)
        started = time.monotonic()
        result = self.provider.chat(
            self.model,
            system_prompt,
            user_prompt,
            json_mode,
            self.temperature if temperature is None else temperature,
            self.num_ctx,
        )
        self.on_done(
            {
                "label": label,
                "model": self.spec,
                "seconds": round(time.monotonic() - started, 2),
                "prompt_tokens": result.prompt_tokens,
                "output_tokens": result.output_tokens,
            }
        )
        return result.text

    def ask_json(
        self, system_prompt: str, user_prompt: str, label: str, temperature: float | None = None
    ) -> dict[str, Any]:
        """Ask in JSON mode; on unparseable output, retry once with the parse error appended."""
        prompt = user_prompt
        for attempt in range(JSON_RETRIES + 1):
            text = self.ask(system_prompt, prompt, label, json_mode=True, temperature=temperature)
            try:
                return extract_json(text)
            except ValueError as exc:  # json.JSONDecodeError is a ValueError
                if attempt == JSON_RETRIES:
                    raise ValueError(f"{label} returned invalid JSON: {exc}") from exc
                prompt = f"{user_prompt}\n\nYour previous reply was not valid JSON ({exc}). Reply with one JSON object only."
        raise AssertionError("unreachable")


def extract_json(text: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = fenced.group(1) if fenced else text
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(raw[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object.")
    return value


def normalize_files(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(path): str(content) for path, content in value.items()}
    if isinstance(value, list):
        files: dict[str, str] = {}
        for item in value:
            if isinstance(item, dict) and "path" in item and "content" in item:
                files[str(item["path"])] = str(item["content"])
        return files
    return {}


ROLES = ("planner", "coder", "reviewer", "critic")


def build_llms(
    config: Any,
    on_call: Callable[[str], None] | None = None,
    on_done: Callable[[dict[str, Any]], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, LLMClient]:
    """One client per role (planner, coder, reviewer, critic); an empty role model uses the default model."""
    from .providers import build_provider, parse_spec, provider_configs

    configs = provider_configs(config.providers_json, config.ollama_host)
    providers: dict[str, Provider] = {}
    clients: dict[str, LLMClient] = {}
    for role in ROLES:
        provider_config, model = parse_spec(getattr(config, f"{role}_model") or config.model, configs)
        if provider_config.name not in providers:
            providers[provider_config.name] = build_provider(provider_config, config.ollama_request_timeout_seconds)
        clients[role] = LLMClient(
            providers[provider_config.name], model, config.temperature, config.num_ctx, on_call, cancel_event, on_done
        )
    return clients
