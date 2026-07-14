from __future__ import annotations

import json
import re
from typing import Any

from ollama import Client


class OllamaClient:
    def __init__(
        self,
        model: str,
        host: str,
        request_timeout_seconds: int,
        temperature: float,
        num_ctx: int,
    ) -> None:
        self.model = model
        self.host = host
        self.temperature = temperature
        self.num_ctx = num_ctx
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

    def ask(self, system_prompt: str, user_prompt: str, label: str) -> str:
        print(f"  LLM call: {label} ({self.model})", flush=True)
        response = self.client.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            options={"temperature": self.temperature, "num_ctx": self.num_ctx},
        )
        return response.message.content


def extract_json(text: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = fenced.group(1) if fenced else text
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start : end + 1])
        raise


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
