"""Model providers: local (Ollama, LM Studio, vLLM) and online (OpenAI-compatible APIs, Anthropic).

A model is chosen with a spec string "<provider>:<model>", e.g. "openai:gpt-5-mini",
"anthropic:claude-opus-5-5" or "ollama:qwen3:4b". A spec without a known provider prefix is an
Ollama model ("qwen3:4b"), which keeps older configs working.
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

OLLAMA = "ollama"
OPENAI_COMPATIBLE = "openai"
ANTHROPIC = "anthropic"

HTTP_RETRIES = 3
RETRY_BASE_SECONDS = 1.0
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
ANTHROPIC_MAX_TOKENS = 16_000
ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# name -> (type, base_url, api-key env var, example model). Users can override any of these, or add
# their own OpenAI-compatible endpoints, in settings.
PRESETS: dict[str, dict[str, str]] = {
    "ollama": {"type": OLLAMA, "base_url": "http://localhost:11434", "key_env": "", "example": "qwen3:4b"},
    "lmstudio": {"type": OPENAI_COMPATIBLE, "base_url": "http://localhost:1234/v1", "key_env": "", "example": "local-model"},
    "openai": {"type": OPENAI_COMPATIBLE, "base_url": "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY", "example": "gpt-5-mini"},
    "anthropic": {"type": ANTHROPIC, "base_url": "", "key_env": "ANTHROPIC_API_KEY", "example": "claude-opus-5-5"},
    "gemini": {
        "type": OPENAI_COMPATIBLE,
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "key_env": "GEMINI_API_KEY",
        "example": "gemini-2.5-flash",
    },
    "groq": {"type": OPENAI_COMPATIBLE, "base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY", "example": "llama-3.3-70b-versatile"},
    "openrouter": {"type": OPENAI_COMPATIBLE, "base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY", "example": "openai/gpt-5-mini"},
    "deepseek": {"type": OPENAI_COMPATIBLE, "base_url": "https://api.deepseek.com/v1", "key_env": "DEEPSEEK_API_KEY", "example": "deepseek-chat"},
    "mistral": {"type": OPENAI_COMPATIBLE, "base_url": "https://api.mistral.ai/v1", "key_env": "MISTRAL_API_KEY", "example": "mistral-large-latest"},
    "together": {"type": OPENAI_COMPATIBLE, "base_url": "https://api.together.xyz/v1", "key_env": "TOGETHER_API_KEY", "example": "meta-llama/Llama-3.3-70B-Instruct-Turbo"},
}


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChatResult:
    text: str
    prompt_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    type: str
    base_url: str
    api_key: str = field(default="", repr=False)


class Provider(Protocol):
    config: ProviderConfig

    def chat(self, model: str, system: str, user: str, json_mode: bool, temperature: float, num_ctx: int) -> ChatResult: ...

    def embed(self, model: str, texts: list[str]) -> list[list[float]]: ...

    def check(self, model: str) -> None: ...

    def list_models(self) -> list[str]: ...


def provider_configs(providers_json: str, ollama_host: str) -> dict[str, ProviderConfig]:
    """Presets, plus API keys from the environment, plus user overrides/additions from settings."""
    try:
        overrides = json.loads(providers_json) if providers_json else {}
    except json.JSONDecodeError as exc:
        raise ProviderError(f"Invalid providers configuration: {exc}") from exc
    if not isinstance(overrides, dict):
        raise ProviderError("Providers configuration must be a JSON object")
    configs: dict[str, ProviderConfig] = {}
    for name in sorted(set(PRESETS) | set(overrides)):
        preset = PRESETS.get(name, {"type": OPENAI_COMPATIBLE, "base_url": "", "key_env": ""})
        override = overrides.get(name) if isinstance(overrides.get(name), dict) else {}
        base_url = str(override.get("base_url") or (ollama_host if name == OLLAMA else preset["base_url"]))
        api_key = str(override.get("api_key") or (os.environ.get(preset["key_env"], "") if preset["key_env"] else ""))
        configs[name] = ProviderConfig(name, str(override.get("type") or preset["type"]), base_url.rstrip("/"), api_key)
    return configs


def parse_spec(spec: str, configs: dict[str, ProviderConfig]) -> tuple[ProviderConfig, str]:
    """'openai:gpt-5-mini' -> (openai config, 'gpt-5-mini'); 'qwen3:4b' -> (ollama config, 'qwen3:4b')."""
    prefix, _, rest = spec.partition(":")
    if rest and prefix in configs:
        return configs[prefix], rest
    return configs[OLLAMA], spec


def build_provider(config: ProviderConfig, timeout_seconds: float) -> Provider:
    if config.type == OLLAMA:
        return OllamaProvider(config, timeout_seconds)
    if config.type == ANTHROPIC:
        return AnthropicProvider(config, timeout_seconds)
    if config.type == OPENAI_COMPATIBLE:
        return OpenAICompatibleProvider(config, timeout_seconds)
    raise ProviderError(f"Unknown provider type {config.type!r} for {config.name}")


def mask_key(key: str) -> str:
    return f"••••{key[-4:]}" if len(key) > 8 else ("••••" if key else "")


# --- Ollama ------------------------------------------------------------------------------------------
class OllamaProvider:
    def __init__(self, config: ProviderConfig, timeout_seconds: float) -> None:
        from ollama import Client

        self.config = config
        self.client = Client(host=config.base_url, timeout=timeout_seconds)

    def chat(self, model: str, system: str, user: str, json_mode: bool, temperature: float, num_ctx: int) -> ChatResult:
        response = self.client.chat(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            format="json" if json_mode else None,
            options={"temperature": temperature, "num_ctx": num_ctx},
        )
        return ChatResult(
            response.message.content or "",
            int(getattr(response, "prompt_eval_count", 0) or 0),
            int(getattr(response, "eval_count", 0) or 0),
        )

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        return [list(vector) for vector in self.client.embed(model=model, input=texts).embeddings]

    def check(self, model: str) -> None:
        try:
            self.client.show(model)
        except Exception as exc:
            raise ProviderError(
                f"Ollama at {self.config.base_url} is not reachable or {model!r} is not pulled. "
                f"Open Settings and download the model, or run `ollama pull {model}`."
            ) from exc

    def list_models(self) -> list[str]:
        return sorted(str(item.model) for item in self.client.list().models)

    def pull(self, model: str) -> Any:
        return self.client.pull(model, stream=True)


# --- OpenAI-compatible (OpenAI, Gemini, Groq, OpenRouter, DeepSeek, Mistral, LM Studio, vLLM, ...) -----
class OpenAICompatibleProvider:
    def __init__(self, config: ProviderConfig, timeout_seconds: float) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        for attempt in range(HTTP_RETRIES + 1):
            request = urllib.request.Request(self.config.base_url + path, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:500]
                if exc.code in RETRYABLE_STATUS and attempt < HTTP_RETRIES:
                    time.sleep(retry_delay(attempt, exc.headers.get("retry-after")))
                    continue
                raise ProviderError(f"{self.config.name} returned HTTP {exc.code}: {detail}") from exc
            except urllib.error.URLError as exc:
                if attempt < HTTP_RETRIES:
                    time.sleep(retry_delay(attempt, None))
                    continue
                raise ProviderError(f"{self.config.name} is unreachable at {self.config.base_url}: {exc.reason}") from exc
        raise AssertionError("unreachable")

    def chat(self, model: str, system: str, user: str, json_mode: bool, temperature: float, num_ctx: int) -> ChatResult:
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        payload = self.request("POST", "/chat/completions", body)
        choices = payload.get("choices") or []
        if not choices:
            raise ProviderError(f"{self.config.name} returned no choices")
        usage = payload.get("usage") or {}
        return ChatResult(
            str(choices[0].get("message", {}).get("content") or ""),
            int(usage.get("prompt_tokens", 0) or 0),
            int(usage.get("completion_tokens", 0) or 0),
        )

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        payload = self.request("POST", "/embeddings", {"model": model, "input": texts})
        rows = sorted(payload.get("data", []), key=lambda row: row.get("index", 0))
        return [list(row["embedding"]) for row in rows]

    def check(self, model: str) -> None:
        if "localhost" not in self.config.base_url and "127.0.0.1" not in self.config.base_url and not self.config.api_key:
            raise ProviderError(f"No API key for {self.config.name}. Add one in Settings.")
        self.list_models()

    def list_models(self) -> list[str]:
        return sorted(str(item.get("id")) for item in self.request("GET", "/models").get("data", []))


# --- Anthropic (official SDK) ----------------------------------------------------------------------
class AnthropicProvider:
    def __init__(self, config: ProviderConfig, timeout_seconds: float) -> None:
        import anthropic

        self.config = config
        kwargs: dict[str, Any] = {"timeout": timeout_seconds, "max_retries": HTTP_RETRIES}
        if config.api_key:
            kwargs["api_key"] = config.api_key
        if config.base_url:
            kwargs["base_url"] = config.base_url
        self.client = anthropic.Anthropic(**kwargs)

    def chat(self, model: str, system: str, user: str, json_mode: bool, temperature: float, num_ctx: int) -> ChatResult:
        # Current Claude models take no sampling parameters, so temperature is not sent. JSON comes from the
        # prompt (every agent prompt demands a JSON object) and is parsed tolerantly by extract_json.
        # Server-side fallbacks re-run a safety-declined request on another model inside the same call.
        response = self.client.beta.messages.create(
            model=model,
            max_tokens=ANTHROPIC_MAX_TOKENS,
            system=system + ("\nRespond with a single JSON object and nothing else." if json_mode else ""),
            messages=[{"role": "user", "content": user}],
            betas=[ANTHROPIC_FALLBACK_BETA],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise ProviderError(f"{model} declined the request.")
        text = "".join(block.text for block in response.content if block.type == "text")
        return ChatResult(text, int(response.usage.input_tokens or 0), int(response.usage.output_tokens or 0))

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        raise ProviderError("Anthropic has no embeddings API; pick an Ollama or OpenAI-compatible embedding model.")

    def check(self, model: str) -> None:
        if not self.config.api_key and not os.environ.get("ANTHROPIC_API_KEY"):
            raise ProviderError("No Anthropic API key. Add one in Settings.")
        self.client.models.retrieve(model)

    def list_models(self) -> list[str]:
        return sorted(model.id for model in self.client.models.list())


def retry_delay(attempt: int, retry_after: str | None) -> float:
    if retry_after and retry_after.isdigit():
        return float(retry_after)
    return RETRY_BASE_SECONDS * (2**attempt) + random.uniform(0, 0.5)
