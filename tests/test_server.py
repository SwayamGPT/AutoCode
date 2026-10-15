from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from agent_platform import runs as runs_module
from agent_platform import server as server_module
from agent_platform.config import config_from_env
from agent_platform.orchestrator import Orchestrator
from tests.fakes import FakeLLM


@pytest.fixture()
def base_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    def fake_orchestrator(config: Any, emit: Any, cancel_event: threading.Event, **_: Any) -> Orchestrator:
        llm = FakeLLM()
        llm.cancel_event = cancel_event
        return Orchestrator(config, emit=emit, llm=llm)

    monkeypatch.setattr(runs_module, "Orchestrator", fake_orchestrator)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_module.make_handler(config_from_env(), server_module.RunStore()))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def request(url: str, body: Any = None, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def read_events(url: str) -> list[dict[str, Any]]:
    events = []
    with urllib.request.urlopen(url, timeout=30) as response:
        for raw in response:
            line = raw.decode().rstrip("\n")
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
                if events[-1]["kind"] == "finished":
                    break
    return events


def test_index_and_config(base_url: str) -> None:
    # 200 with the built React app, or 503 with build instructions when ui/ has not been built.
    try:
        with urllib.request.urlopen(base_url + "/", timeout=5) as response:
            assert b"AutoCode" in response.read()
    except urllib.error.HTTPError as exc:
        assert exc.code == 503 and b"npm --prefix ui" in exc.read()
    status, payload = request(base_url + "/api/config")
    assert status == 200 and len(payload["personas"]) == 10


def test_full_run_stream_and_apply(base_url: str, tmp_path: Path) -> None:
    workspace = tmp_path / "proj"
    workspace.mkdir()
    (workspace / "README.md").write_text("hello\n")

    status, run = request(base_url + "/api/runs", {"requirement": "adder", "workspace": str(workspace)})
    assert status == 201

    events = read_events(f"{base_url}/api/runs/{run['id']}/events")
    assert events[-1]["data"] == {"status": "done", "passed": True, "error": ""}
    assert any(event["kind"] == "files" and "app.py" in event["data"]["diffs"] for event in events)

    status, snapshot = request(f"{base_url}/api/runs/{run['id']}")
    assert snapshot["status"] == "done" and sorted(snapshot["diffs"]) == ["SECURITY.md", "app.py"]

    status, applied = request(f"{base_url}/api/runs/{run['id']}/apply", {})
    assert status == 200 and sorted(applied["written"]) == ["SECURITY.md", "app.py"]
    assert "Add two numbers" in (workspace / "app.py").read_text()

    # Replaying from a cursor only returns later events.
    replay = read_events(f"{base_url}/api/runs/{run['id']}/events?from={len(events) - 1}")
    assert [event["kind"] for event in replay] == ["finished"]


def test_rejects_foreign_host_and_path_traversal(base_url: str) -> None:
    assert request(base_url + "/api/config", headers={"Host": "evil.example"})[0] == 403
    status, _ = request(base_url + "/assets/../../server.py")
    assert status == 404


def test_rejects_bad_requests(base_url: str) -> None:
    assert request(base_url + "/api/runs", {"requirement": " "})[0] == 400
    assert request(base_url + "/api/runs", {"requirement": "x", "settings": {"max_iterations": 0}})[0] == 400
    assert request(base_url + "/api/runs/nope")[0] == 404
    cross_origin = request(base_url + "/api/runs", {"requirement": "x"}, {"Origin": "http://evil.example"})
    assert cross_origin[0] == 403
    form_post = request(base_url + "/api/runs", {"requirement": "x"}, {"Content-Type": "text/plain"})
    assert form_post[0] == 415


def test_settings_round_trip_masks_keys(base_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from agent_platform import settings as settings_module

    monkeypatch.setattr(settings_module, "SETTINGS_FILE", tmp_path / "settings.json")
    providers = {"openai": {"type": "openai", "base_url": "https://api.openai.com/v1", "api_key": "sk-test-secret-4321"}}
    status, saved = request(
        base_url + "/api/settings",
        {"providers_json": json.dumps(providers), "model": "openai:gpt-5-mini", "reviewer_model": "ollama:qwen3:4b"},
    )
    assert status == 200
    assert "sk-test-secret" not in json.dumps(saved)
    assert saved["config"]["model"] == "openai:gpt-5-mini"
    assert saved["providers"]["openai"]["has_key"] is True
    _, config = request(base_url + "/api/config")
    assert "sk-test-secret" not in json.dumps(config)
    assert request(base_url + "/api/settings", {"providers_json": "{bad"})[0] == 400
    # Per-run settings may not redirect where API keys are sent.
    assert request(base_url + "/api/runs", {"requirement": "x", "settings": {"providers_json": "{}"}})[0] == 400


def test_memory_and_learning_endpoints(base_url: str, tmp_path: Path) -> None:
    status, run = request(base_url + "/api/runs", {"requirement": "make an adder library"})
    read_events(f"{base_url}/api/runs/{run['id']}/events")
    _, memory = request(base_url + "/api/memory?limit=100")
    assert memory["backend"] == "sqlite" and memory["count"] >= 1
    lesson = next(item for item in memory["items"] if item["kind"] == "lesson")
    _, found = request(base_url + "/api/memory?q=adder%20library")
    assert found["items"] and found["items"][0]["similarity"] > 0
    assert request(base_url + "/api/memory/delete", {"id": lesson["id"]})[0] == 200
    _, after = request(base_url + "/api/memory?limit=200")
    assert lesson["id"] not in {item["id"] for item in after["items"]}

    _, learning = request(base_url + "/api/learning")
    assert set(learning["strategies"]) == {"focused", "thorough", "test_first"}
    assert any(row["pulls"] >= 1 for row in learning["stats"])
    assert request(base_url + "/api/memory?limit=x")[0] == 400


def test_models_and_health_endpoints(base_url: str) -> None:
    assert request(base_url + "/api/models?provider=nope")[0] == 400
    assert request(base_url + "/api/models/pull", {"model": "openai:gpt-5-mini"})[0] == 400
    assert request(base_url + "/api/models/pull", {})[0] == 400
    status, health = request(base_url + "/api/health")
    assert status == 200 and {"ok", "git", "models", "memory", "embedding"} <= set(health)
    assert health["models"][0]["roles"] == ["planner", "coder", "reviewer", "critic"]


def test_agent_run_waits_for_approval(base_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_tools import ScriptedLLM

    actions = [
        {"thought": "write", "tool": "write_file", "args": {"path": "hello.py", "content": "print('hi')\n"}},
        {"thought": "run", "tool": "run_command", "args": {"command": "echo approved-run"}},
        {"thought": "done", "final": "Wrote hello.py."},
    ]

    class Ready(ScriptedLLM):
        def ensure_ready(self) -> None:
            pass

    monkeypatch.setattr(runs_module, "build_llms", lambda *a, **k: {"coder": Ready(list(actions))})
    assert request(base_url + "/api/runs", {"requirement": "x", "mode": "agent", "workspace": str(tmp_path / "nope")})[0] == 400

    status, run = request(base_url + "/api/runs", {"requirement": "say hi", "mode": "agent", "workspace": str(tmp_path)})
    assert status == 201
    kinds = []
    with urllib.request.urlopen(f"{base_url}/api/runs/{run['id']}/events", timeout=30) as response:
        for raw in response:
            line = raw.decode().strip()
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            kinds.append(event["kind"])
            if event["kind"] == "approval_request":
                assert event["data"]["summary"] == "Run: echo approved-run"
                ok = request(f"{base_url}/api/runs/{run['id']}/approve", {"id": event["data"]["id"], "approved": True})
                assert ok[0] == 200
            if event["kind"] == "finished":
                break
    assert (tmp_path / "hello.py").read_text() == "print('hi')\n"
    assert "files" in kinds and "approval_result" in kinds and kinds[-2] == "agent_final"
    _, snapshot = request(f"{base_url}/api/runs/{run['id']}")
    assert snapshot["status"] == "done" and snapshot["mode"] == "agent"
    assert request(f"{base_url}/api/runs/{run['id']}/approve", {"id": "zzz", "approved": True})[0] == 404
