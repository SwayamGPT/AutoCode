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
    def fake_orchestrator(config: Any, emit: Any, cancel_event: threading.Event) -> Orchestrator:
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
    assert snapshot["status"] == "done" and list(snapshot["diffs"]) == ["app.py"]

    status, applied = request(f"{base_url}/api/runs/{run['id']}/apply", {})
    assert status == 200 and applied["written"] == ["app.py"]
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
