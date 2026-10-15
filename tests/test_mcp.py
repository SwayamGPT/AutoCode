from __future__ import annotations

import dataclasses
import io
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from agent_platform import mcp_server
from agent_platform.config import config_from_env
from agent_platform.context_agent import ContextAgent
from agent_platform.mcp_client import McpClient, McpError, load_mcp_servers
from agent_platform.orchestrator import Orchestrator
from tests.fakes import FakeLLM

ROOT = Path(__file__).resolve().parents[1]
SERVER_SPEC = {"command": sys.executable, "args": [str(ROOT / "app.py"), "mcp"]}


def test_client_talks_to_autocode_mcp_server_over_stdio() -> None:
    client = McpClient("autocode", SERVER_SPEC, timeout_seconds=20)
    try:
        client.initialize()
        names = [tool["name"] for tool in client.list_tools()]
        assert names == ["generate_code", "review_code", "list_reviewers"]
        reviewers = json.loads(client.call_tool("list_reviewers", {}))
        assert len(reviewers) == 10 and reviewers[0]["name"] == "Business Logic"
        with pytest.raises(McpError, match="requirement is required"):
            client.call_tool("generate_code", {})
    finally:
        client.close()


def test_load_mcp_servers(tmp_path: Path) -> None:
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"a": {"command": "x"}, "bad": 3}}))
    assert load_mcp_servers(str(config)) == {"a": {"command": "x"}}
    assert load_mcp_servers(str(tmp_path / "missing.json")) == {}
    config.write_text("{nope")
    with pytest.raises(McpError):
        load_mcp_servers(str(config))


def test_context_agent_calls_chosen_tool(tmp_path: Path) -> None:
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"autocode": SERVER_SPEC}}))
    events: list[tuple[str, dict]] = []
    agent = ContextAgent(FakeLLM(), str(config), 20, lambda kind, data: events.append((kind, data)))
    context = agent.gather("build something", {})
    assert "autocode/list_reviewers" in context and "Business Logic" in context
    assert any(kind == "mcp_call" and data["ok"] for kind, data in events)


def test_context_agent_is_noop_without_config() -> None:
    assert ContextAgent(FakeLLM(), "", 5, lambda *_: None).gather("x", {}) == ""


class Collector(io.StringIO):
    def messages(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.getvalue().splitlines() if line.strip()]


def wait_for_reply(out: Collector, request_id: int) -> dict[str, Any]:
    deadline = time.time() + 20
    while time.time() < deadline:
        for message in out.messages():
            if message.get("id") == request_id:
                return message
        time.sleep(0.05)
    raise AssertionError("no reply")


def make_server(monkeypatch: pytest.MonkeyPatch) -> tuple[mcp_server.McpServer, Collector]:
    def fake_orchestrator(config: Any, emit: Any, cancel_event: threading.Event, **_: Any) -> Orchestrator:
        llm = FakeLLM()
        llm.cancel_event = cancel_event
        return Orchestrator(config, emit=emit, llm=llm)

    monkeypatch.setattr(mcp_server, "Orchestrator", fake_orchestrator)
    out = Collector()
    config = dataclasses.replace(config_from_env(), max_iterations=3)
    return mcp_server.McpServer(config, out), out


def test_initialize_negotiates_version(monkeypatch: pytest.MonkeyPatch) -> None:
    server, out = make_server(monkeypatch)
    server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}})
    server.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
    server.handle({"jsonrpc": "2.0", "id": 3, "method": "nope"})
    replies = {m["id"]: m for m in out.messages()}
    assert replies[1]["result"]["protocolVersion"] == "2024-11-05"
    assert replies[2]["result"]["protocolVersion"] == mcp_server.SUPPORTED_VERSIONS[0]
    assert replies[3]["error"]["code"] == mcp_server.METHOD_NOT_FOUND


def test_generate_code_tool_writes_changes_and_reports_progress(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    server, out = make_server(monkeypatch)
    params = {
        "name": "generate_code",
        "arguments": {"requirement": "adder", "workspace": str(tmp_path), "write": True},
        "_meta": {"progressToken": "tok"},
    }
    server.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": params})
    reply = wait_for_reply(out, 7)["result"]
    assert reply["isError"] is False
    assert reply["structuredContent"]["passed"] is True
    assert sorted(reply["structuredContent"]["written"]) == ["SECURITY.md", "app.py"]
    assert "Add two numbers" in (tmp_path / "app.py").read_text()
    assert any(m.get("method") == "notifications/progress" for m in out.messages())


def test_review_code_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    server, out = make_server(monkeypatch)
    params = {"name": "review_code", "arguments": {"requirement": "adder", "files": {"a.py": "x = 1\n"}}}
    server.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": params})
    reply = wait_for_reply(out, 9)["result"]
    assert len(reply["structuredContent"]["reviews"]) == 10
