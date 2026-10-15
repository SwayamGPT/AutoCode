"""Context agent: lets the model call configured MCP tools before planning."""

from __future__ import annotations

import json
from typing import Any, Callable

from .llm import LLMClient
from .mcp_client import McpClient, McpError, load_mcp_servers

MAX_TOOL_CALLS = 3
MAX_RESULT_CHARS = 4000
MAX_DESCRIPTION_CHARS = 300

SYSTEM_PROMPT = """
You are a context-gathering agent for a code generator.
You may call the listed tools to fetch information that helps implement the requirement
(docs, existing code, issues, schemas). Call nothing if the tools are not useful.
Return valid JSON only.

JSON schema:
{
  "calls": [
    {"server": "server name", "tool": "tool name", "arguments": {}, "reason": "why"}
  ]
}
"""


class ContextAgent:
    def __init__(
        self,
        llm: LLMClient,
        config_path: str,
        timeout_seconds: float,
        emit: Callable[[str, dict[str, Any]], None],
    ) -> None:
        self.llm = llm
        self.config_path = config_path
        self.timeout_seconds = timeout_seconds
        self.emit = emit

    def gather(self, requirement: str, existing_files: dict[str, str]) -> str:
        try:
            specs = load_mcp_servers(self.config_path)
        except McpError as exc:
            self.emit("status", {"message": f"MCP config skipped: {exc}"})
            return ""
        if not specs:
            return ""
        clients: dict[str, McpClient] = {}
        try:
            catalog = self.connect(specs, clients)
            if not catalog:
                return ""
            calls = self.choose_calls(requirement, existing_files, catalog)
            return self.execute(calls, clients, catalog)
        finally:
            for client in clients.values():
                client.close()

    def connect(self, specs: dict[str, dict[str, Any]], clients: dict[str, McpClient]) -> list[dict[str, Any]]:
        catalog: list[dict[str, Any]] = []
        for name, spec in specs.items():
            try:
                client = McpClient(name, spec, self.timeout_seconds)
                clients[name] = client
                client.initialize()
                for tool in client.list_tools():
                    catalog.append(
                        {
                            "server": name,
                            "tool": tool.get("name"),
                            "description": str(tool.get("description", ""))[:MAX_DESCRIPTION_CHARS],
                            "input_schema": tool.get("inputSchema", {}),
                        }
                    )
            except (McpError, OSError) as exc:
                self.emit("status", {"message": f"MCP server {name} unavailable: {exc}"})
        self.emit("status", {"message": f"MCP: {len(catalog)} tools from {len(clients)} servers"})
        return catalog

    def choose_calls(
        self, requirement: str, existing_files: dict[str, str], catalog: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        user_prompt = (
            f"Requirement:\n{requirement}\n\n"
            f"Existing files:\n{json.dumps(sorted(existing_files))}\n\n"
            f"Tools:\n{json.dumps(catalog, indent=2)}\n\n"
            f"Choose at most {MAX_TOOL_CALLS} calls."
        )
        try:
            calls = self.llm.ask_json(SYSTEM_PROMPT, user_prompt, "Context Agent").get("calls", [])
        except ValueError as exc:
            self.emit("status", {"message": f"Context agent skipped: {exc}"})
            return []
        return [call for call in calls if isinstance(call, dict)][:MAX_TOOL_CALLS] if isinstance(calls, list) else []

    def execute(
        self, calls: list[dict[str, Any]], clients: dict[str, McpClient], catalog: list[dict[str, Any]]
    ) -> str:
        known = {(entry["server"], entry["tool"]) for entry in catalog}
        sections: list[str] = []
        for call in calls:
            server, tool = str(call.get("server", "")), str(call.get("tool", ""))
            arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
            if (server, tool) not in known:
                self.emit("status", {"message": f"Context agent asked for unknown tool {server}/{tool}; skipped"})
                continue
            try:
                result = clients[server].call_tool(tool, arguments)[:MAX_RESULT_CHARS]
                ok = True
            except McpError as exc:
                result, ok = str(exc), False
            self.emit("mcp_call", {"server": server, "tool": tool, "arguments": arguments, "ok": ok, "result": result})
            if ok:
                sections.append(f"## {server}/{tool} {json.dumps(arguments)}\n{result}")
        return "\n\n".join(sections)
