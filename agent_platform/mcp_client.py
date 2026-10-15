"""Minimal MCP client over stdio (JSON-RPC 2.0, newline-delimited)."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from pathlib import Path
from typing import Any

MCP_PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "autocode", "version": "1.0.0"}
METHOD_NOT_FOUND = -32601


class McpError(Exception):
    pass


def load_mcp_servers(path: str) -> dict[str, dict[str, Any]]:
    """Read a Claude/Cursor-style config: {"mcpServers": {"name": {"command", "args", "env"}}}."""
    if not path or not Path(path).expanduser().is_file():
        return {}
    try:
        payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise McpError(f"Invalid MCP config {path}: {exc}") from exc
    servers = payload.get("mcpServers", {}) if isinstance(payload, dict) else {}
    if not isinstance(servers, dict):
        raise McpError(f"Invalid MCP config {path}: mcpServers must be an object")
    return {str(name): spec for name, spec in servers.items() if isinstance(spec, dict)}


class McpClient:
    # ponytail: stdio transport only; add streamable HTTP when a remote server is needed.
    def __init__(self, name: str, spec: dict[str, Any], timeout_seconds: float) -> None:
        command = spec.get("command")
        if not command:
            raise McpError(f"MCP server {name!r} has no command (only stdio servers are supported)")
        self.name = name
        self.timeout_seconds = timeout_seconds
        self.next_id = 0
        self.write_lock = threading.Lock()
        self.responses: queue.Queue[dict[str, Any]] = queue.Queue()
        self.proc = subprocess.Popen(
            [str(command), *[str(arg) for arg in spec.get("args", [])]],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env={**os.environ, **{str(k): str(v) for k, v in (spec.get("env") or {}).items()}},
        )
        threading.Thread(target=self.read_loop, name=f"mcp-{name}", daemon=True).start()

    def read_loop(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue  # servers sometimes log to stdout; ignore non-protocol lines
            if not isinstance(message, dict):
                continue
            if "method" in message and "id" in message:
                # Server-to-client request (roots, sampling, ...): not supported by this client.
                self.send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": METHOD_NOT_FOUND, "message": "Not supported"}})
            elif "id" in message:
                self.responses.put(message)

    def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        try:
            with self.write_lock:
                self.proc.stdin.write(json.dumps(message) + "\n")
                self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise McpError(f"MCP server {self.name!r} exited") from exc

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.next_id += 1
        request_id = self.next_id
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        while True:
            try:
                message = self.responses.get(timeout=self.timeout_seconds)
            except queue.Empty as exc:
                raise McpError(f"MCP server {self.name!r} timed out on {method}") from exc
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise McpError(f"{self.name}.{method}: {message['error'].get('message', message['error'])}")
            return message.get("result") or {}

    def initialize(self) -> None:
        self.request(
            "initialize",
            {"protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO},
        )
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor = None
        while True:
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(tool for tool in result.get("tools", []) if isinstance(tool, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, tool: str, arguments: dict[str, Any]) -> str:
        result = self.request("tools/call", {"name": tool, "arguments": arguments})
        texts = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                texts.append(str(item.get("text", "")))
            elif item.get("type") == "resource" and isinstance(item.get("resource"), dict):
                texts.append(str(item["resource"].get("text", "")))
            else:
                texts.append(f"[{item.get('type', 'unknown')} content omitted]")
        if not texts and "structuredContent" in result:
            texts.append(json.dumps(result["structuredContent"]))
        text = "\n".join(texts)
        if result.get("isError"):
            raise McpError(f"{self.name}.{tool} failed: {text}")
        return text

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
