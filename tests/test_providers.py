from __future__ import annotations

import dataclasses
import json
import os
import stat
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from agent_platform import settings as settings_module
from agent_platform.config import config_from_env
from agent_platform.llm import build_llms
from agent_platform.providers import (
    ANTHROPIC,
    OLLAMA,
    OPENAI_COMPATIBLE,
    OpenAICompatibleProvider,
    ProviderConfig,
    ProviderError,
    mask_key,
    parse_spec,
    provider_configs,
)
from agent_platform.settings import A2A_LOCKED, RUN_LOCKED, coerce_settings, mask_settings, save_settings


class FakeOpenAI(BaseHTTPRequestHandler):
    calls: list[dict[str, Any]] = []
    fail_first = 0

    def log_message(self, *args: Any) -> None:
        pass

    def reply(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self.reply(200, {"data": [{"id": "model-b"}, {"id": "model-a"}]})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOpenAI.calls.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
        if FakeOpenAI.fail_first:
            FakeOpenAI.fail_first -= 1
            return self.reply(503, {"error": "busy"})
        if self.path == "/v1/embeddings":
            return self.reply(200, {"data": [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1.0, 0.0]}]})
        self.reply(200, {"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}})


@pytest.fixture()
def openai_url() -> Iterator[str]:
    FakeOpenAI.calls = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAI)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


def test_specs_resolve_providers() -> None:
    configs = provider_configs(json.dumps({"mine": {"type": "openai", "base_url": "http://x/v1"}}), "http://ollama:11434")
    assert parse_spec("qwen3:4b", configs) == (configs["ollama"], "qwen3:4b")
    assert parse_spec("ollama:qwen3:4b", configs)[1] == "qwen3:4b"
    assert parse_spec("anthropic:claude-opus-5-5", configs)[0].type == ANTHROPIC
    assert parse_spec("mine:llama", configs)[0].base_url == "http://x/v1"
    assert configs["ollama"].base_url == "http://ollama:11434" and configs["ollama"].type == OLLAMA
    assert configs["gemini"].type == OPENAI_COMPATIBLE


def test_env_keys_are_picked_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key-123456")
    assert provider_configs("", "")["openai"].api_key == "sk-env-key-123456"
    assert "sk-env" not in repr(provider_configs("", "")["openai"])


def test_openai_compatible_chat_json_retry_and_embeddings(openai_url: str) -> None:
    FakeOpenAI.fail_first = 1
    provider = OpenAICompatibleProvider(ProviderConfig("local", OPENAI_COMPATIBLE, openai_url, "secret"), 5)
    result = provider.chat("m", "sys", "user", True, 0.2, 4096)
    assert result.text == '{"ok": true}' and (result.prompt_tokens, result.output_tokens) == (7, 3)
    call = FakeOpenAI.calls[-1]
    assert call["body"]["response_format"] == {"type": "json_object"} and call["auth"] == "Bearer secret"
    assert len(FakeOpenAI.calls) == 2  # one 503, then success
    assert provider.embed("e", ["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert provider.list_models() == ["model-a", "model-b"]


def test_openai_compatible_reports_auth_errors(openai_url: str) -> None:
    provider = OpenAICompatibleProvider(ProviderConfig("cloud", OPENAI_COMPATIBLE, "https://api.example.invalid/v1", ""), 1)
    with pytest.raises(ProviderError, match="No API key"):
        provider.check("m")


def test_roles_use_their_own_models(openai_url: str) -> None:
    providers = json.dumps({"local": {"type": "openai", "base_url": openai_url}})
    config = dataclasses.replace(config_from_env(), providers_json=providers, reviewer_model="local:reviewer-model")
    llms = build_llms(config)
    assert llms["reviewer"].spec == "local:reviewer-model"
    assert llms["planner"].spec == f"ollama:{config.model}"
    assert llms["reviewer"].ask_json("s", "u", "Security") == {"ok": True}


def test_run_settings_cannot_redirect_credentials() -> None:
    base = config_from_env()
    for field in RUN_LOCKED:
        with pytest.raises(ValueError, match="saved settings"):
            coerce_settings(base, {field: "http://evil.example" if field != "learning" else False}, RUN_LOCKED)
    with pytest.raises(ValueError):
        coerce_settings(base, {"a2a_peers": "http://evil.example"}, A2A_LOCKED)
    assert coerce_settings(base, {"coder_model": "openai:gpt-5-mini"}, RUN_LOCKED).coder_model == "openai:gpt-5-mini"


def test_saved_settings_mask_and_keep_api_keys(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_module, "SETTINGS_FILE", tmp_path / "settings.json")
    base = config_from_env()
    providers = {"openai": {"type": "openai", "base_url": "https://api.openai.com/v1", "api_key": "sk-real-secret-9876"}}
    saved = save_settings(base, {"providers_json": json.dumps(providers), "model": "openai:gpt-5-mini"})
    assert stat.S_IMODE(os.stat(tmp_path / "settings.json").st_mode) == 0o600

    masked = mask_settings(saved)
    assert "sk-real" not in json.dumps(masked)
    assert json.loads(masked["providers_json"])["openai"]["api_key"] == mask_key("sk-real-secret-9876")

    # The UI sends the masked key back unchanged: the stored key must survive.
    again = save_settings(saved, {"providers_json": masked["providers_json"]})
    assert json.loads(again.providers_json)["openai"]["api_key"] == "sk-real-secret-9876"
    assert settings_module.apply_saved_settings(base).model == "openai:gpt-5-mini"

    with pytest.raises(ValueError, match="base_url"):
        save_settings(base, {"providers_json": json.dumps({"x": {"base_url": "file:///etc/passwd"}})})


def test_cli_keeps_providers_and_role_models(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_platform import cli

    seen: dict[str, Any] = {}

    class Recorder:
        def __init__(self, config: Any) -> None:
            seen["config"] = config

        def run(self, requirement: str, files: dict[str, str]) -> Any:
            return type("Result", (), {"files": {}})()

    monkeypatch.setattr(cli, "Orchestrator", Recorder)
    monkeypatch.setenv("PROVIDERS_JSON", '{"fake": {"type": "openai", "base_url": "http://x/v1"}}')
    monkeypatch.setenv("REVIEWER_MODEL", "fake:r")
    cli.main(["make x", "--max-iterations", "3"])
    config = seen["config"]
    assert config.reviewer_model == "fake:r" and "fake" in config.providers_json and config.max_iterations == 3
