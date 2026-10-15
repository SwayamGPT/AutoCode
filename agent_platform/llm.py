from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Callable

from ollama import Client

JSON_RETRIES = 1


class RunCancelled(Exception):
    pass


class OllamaClient:
    def __init__(
        self,
        model: str,
        host: str,
        request_timeout_seconds: int,
        temperature: float,
        num_ctx: int,
        on_call: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
        on_done: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.model = model
        self.host = host
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.on_call = on_call or (lambda label: print(f"  LLM call: {label} ({model})", flush=True))
        self.cancel_event = cancel_event or threading.Event()
        self.on_done = on_done or (lambda metrics: None)
        self.client = Client(host=host, timeout=request_timeout_seconds)

    def ensure_ready(self) -> None:
        try:
            self.client.show(self.model)
        except Exception as exc:
            raise RuntimeError(
                "Ollama is not ready or the configured model is missing. "
                f"Host: {self.host}. Model: {self.model}. "
                f"Run `ollama pull {self.model}` locally, or `docker compose up ollama-pull` for Docker."
            ) from exc

    def ask(self, system_prompt: str, user_prompt: str, label: str, json_mode: bool = False) -> str:
        if self.cancel_event.is_set():
            raise RunCancelled()
        self.on_call(label)
        started = time.monotonic()
        response = self.client.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            format="json" if json_mode else None,
            options={"temperature": self.temperature, "num_ctx": self.num_ctx},
        )
        self.on_done(
            {
                "label": label,
                "seconds": round(time.monotonic() - started, 2),
                "prompt_tokens": int(getattr(response, "prompt_eval_count", 0) or 0),
                "output_tokens": int(getattr(response, "eval_count", 0) or 0),
            }
        )
        return response.message.content or ""

    def ask_json(self, system_prompt: str, user_prompt: str, label: str) -> dict[str, Any]:
        """Ask in JSON mode; on unparseable output, retry once with the parse error appended."""
        prompt = user_prompt
        for attempt in range(JSON_RETRIES + 1):
            text = self.ask(system_prompt, prompt, label, json_mode=True)
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
