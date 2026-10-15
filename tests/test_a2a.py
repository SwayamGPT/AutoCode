from __future__ import annotations

import json
import threading
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from typing import Any

import pytest

from agent_platform import runs as runs_module
from agent_platform import server as server_module
from agent_platform.a2a_client import A2AClient, RemoteReviewer, review_from_task
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
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def rpc(base_url: str, method: str, params: Any, raw: bytes | None = None) -> dict[str, Any]:
    body = raw if raw is not None else json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(base_url + "/a2a", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def message(text: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    parts: list[dict[str, Any]] = [{"text": text}]
    if data is not None:
        parts.append({"data": data})
    return {"messageId": "m1", "role": "ROLE_USER", "parts": parts}


def test_agent_card(base_url: str) -> None:
    card = A2AClient(base_url, 5).fetch_card()
    assert card["name"] == "AutoCode"
    assert card["supportedInterfaces"][0] == {
        "url": base_url + "/a2a",
        "protocolBinding": "JSONRPC",
        "protocolVersion": "1.0",
    }
    assert {skill["id"] for skill in card["skills"]} == {"generate", "review"}


def test_generate_skill_returns_file_artifacts(base_url: str) -> None:
    task = rpc(base_url, "SendMessage", {"message": message("make an adder")})["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    names = [artifact["name"] for artifact in task["artifacts"]]
    assert names == ["SECURITY.md", "app.py", "review"]
    assert task["history"][0]["role"] == "ROLE_USER"

    fetched = rpc(base_url, "GetTask", {"id": task["id"]})["result"]
    assert fetched["id"] == task["id"]
    cancel = rpc(base_url, "CancelTask", {"id": task["id"]})
    assert cancel["error"]["code"] == -32002


def test_return_immediately_gives_working_task(base_url: str) -> None:
    params = {"message": message("make an adder"), "configuration": {"returnImmediately": True}}
    task = rpc(base_url, "SendMessage", params)["result"]["task"]
    assert task["status"]["state"] in {"TASK_STATE_WORKING", "TASK_STATE_COMPLETED"}


def test_review_skill_and_remote_reviewer(base_url: str) -> None:
    review = RemoteReviewer(base_url, 30).run("adder", {}, {"app.py": "def add(a, b):\n    return a + b\n"})
    assert review["persona"].startswith("Peer 127.0.0.1")
    assert len(review["peer_reviews"]) == 10
    assert review["status"] in {"pass", "needs_work"}


def test_rpc_errors(base_url: str) -> None:
    assert rpc(base_url, "Nope", {})["error"]["code"] == -32601
    assert rpc(base_url, "GetTask", {"id": "missing"})["error"]["code"] == -32001
    assert rpc(base_url, "SendMessage", {"message": message("x", {"skill": "review"})})["error"]["code"] == -32602
    assert rpc(base_url, "SendMessage", {"message": {"parts": "bad"}})["error"]["code"] == -32602
    assert rpc(base_url, "", None, raw=b"{bad")["error"]["code"] == -32700


def test_review_from_task_takes_lowest_score() -> None:
    task = {
        "status": {"state": "TASK_STATE_COMPLETED"},
        "artifacts": [{"parts": [{"data": {"reviews": [
            {"persona": "A", "score": 90, "status": "pass", "patches": [{"file": "x"}]},
            {"persona": "B", "score": 40, "status": "needs_work", "issues": [{"description": "bug"}]},
        ]}}]}],
    }
    review = review_from_task("Peer x", task)
    assert review["score"] == 40 and review["status"] == "needs_work"
    assert review["patches"] == [{"file": "x"}] and review["issues"] == [{"description": "bug"}]
    with pytest.raises(RuntimeError):
        review_from_task("Peer x", {"status": {"state": "TASK_STATE_FAILED"}})
