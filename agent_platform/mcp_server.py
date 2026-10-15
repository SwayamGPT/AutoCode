"""AutoCode as an MCP server over stdio, so Claude Code, Cursor, or any MCP client can call it.

Register with Claude Code:  claude mcp add autocode -- python /path/to/app.py mcp
"""

from __future__ import annotations

import dataclasses
import json
import sys
import threading
from pathlib import Path
from typing import Any, TextIO

from .llm import RunCancelled
from .models import PipelineConfig
from .orchestrator import Orchestrator
from .personas import PERSONAS
from .workspace import changed_files, file_diff, load_workspace, write_project

SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "autocode", "title": "AutoCode", "version": "1.0.0"}
MAX_DIFF_CHARS = 20_000
INVALID_PARAMS = -32602
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603

TOOLS: list[dict[str, Any]] = [
    {
        "name": "generate_code",
        "title": "Generate or change code",
        "description": "Plan, write, review (ten weighted personas), patch, and test code for a requirement using "
        "a local Ollama model. With a workspace, existing files are used as context and only changes are "
        "returned; set write=true to write them to disk.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "requirement": {"type": "string", "description": "What to build or change."},
                "workspace": {"type": "string", "description": "Absolute path of the project to edit."},
                "write": {"type": "boolean", "description": "Write changed files into the workspace.", "default": False},
                "max_iterations": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["requirement"],
        },
    },
    {
        "name": "review_code",
        "title": "Review code",
        "description": "Score files with ten weighted reviewer personas (security, reliability, testing, ...) "
        "and return issues plus unified-diff patch suggestions. Pass files directly or a workspace path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "requirement": {"type": "string", "description": "What the code is supposed to do."},
                "files": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Map of relative path to file content.",
                },
                "workspace": {"type": "string", "description": "Project path to review instead of files."},
            },
            "required": ["requirement"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "list_reviewers",
        "title": "List reviewer personas",
        "description": "List the reviewer personas with their checklists and scoring weights.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
]


class ToolError(Exception):
    pass


class McpServer:
    def __init__(self, config: PipelineConfig, out: TextIO) -> None:
        self.config = config
        self.out = out
        self.write_lock = threading.Lock()
        self.cancel_events: dict[Any, threading.Event] = {}

    def send(self, message: dict[str, Any]) -> None:
        with self.write_lock:
            self.out.write(json.dumps(message) + "\n")
            self.out.flush()

    def serve(self, stream: TextIO) -> None:
        for line in stream:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self.send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            if isinstance(message, dict):
                self.handle(message)

    def handle(self, message: dict[str, Any]) -> None:
        method, request_id, params = message.get("method"), message.get("id"), message.get("params") or {}
        if method is None:
            return  # a response to something we never send
        if request_id is None:
            if method == "notifications/cancelled":
                event = self.cancel_events.get(params.get("requestId"))
                if event:
                    event.set()
            return
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
            return self.reply(
                request_id,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                    "instructions": "Use generate_code to build or change code with a local model and a ten-persona "
                    "review loop; use review_code for a second opinion on existing files.",
                },
            )
        if method == "ping":
            return self.reply(request_id, {})
        if method == "tools/list":
            return self.reply(request_id, {"tools": TOOLS})
        if method == "tools/call":
            # Tool calls can take minutes; run them off the read loop so cancellations still arrive.
            cancel_event = threading.Event()
            self.cancel_events[request_id] = cancel_event
            threading.Thread(target=self.call_tool, args=(request_id, params, cancel_event), daemon=True).start()
            return
        self.send({"jsonrpc": "2.0", "id": request_id, "error": {"code": METHOD_NOT_FOUND, "message": f"Unknown method {method}"}})

    def reply(self, request_id: Any, result: dict[str, Any]) -> None:
        self.send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def call_tool(self, request_id: Any, params: dict[str, Any], cancel_event: threading.Event) -> None:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        progress_token = (params.get("_meta") or {}).get("progressToken")
        try:
            if name == "generate_code":
                result = self.generate(arguments, cancel_event, progress_token)
            elif name == "review_code":
                result = self.review(arguments, cancel_event, progress_token)
            elif name == "list_reviewers":
                result = tool_result(json.dumps([dataclasses.asdict(p) for p in PERSONAS], indent=2))
            else:
                self.send({"jsonrpc": "2.0", "id": request_id, "error": {"code": INVALID_PARAMS, "message": f"Unknown tool {name}"}})
                return
        except (ToolError, ValueError) as exc:
            result = tool_result(str(exc), is_error=True)
        except RunCancelled:
            return  # the client cancelled; MCP says not to answer
        except Exception as exc:  # report pipeline failures (e.g. Ollama down) as tool errors
            result = tool_result(f"AutoCode failed: {exc}", is_error=True)
        finally:
            self.cancel_events.pop(request_id, None)
        self.reply(request_id, result)

    def orchestrator(self, overrides: dict[str, Any], cancel_event: threading.Event, progress_token: Any) -> Orchestrator:
        config = dataclasses.replace(self.config, **overrides)
        progress = {"count": 0}

        def emit(kind: str, data: dict[str, Any]) -> None:
            if kind == "status":
                print(data["message"], file=sys.stderr, flush=True)
            if progress_token is not None and kind in {"status", "llm_call"}:
                progress["count"] += 1
                message = data.get("message") or f"Asking model: {data.get('label')}"
                self.send(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/progress",
                        "params": {"progressToken": progress_token, "progress": progress["count"], "message": message},
                    }
                )

        return Orchestrator(config, emit=emit, cancel_event=cancel_event)

    def generate(self, arguments: dict[str, Any], cancel_event: threading.Event, progress_token: Any) -> dict[str, Any]:
        requirement = str(arguments.get("requirement") or "").strip()
        if not requirement:
            raise ToolError("requirement is required")
        workspace = str(arguments.get("workspace") or "").strip()
        overrides = {}
        if arguments.get("max_iterations") is not None:
            overrides["max_iterations"] = max(1, int(arguments["max_iterations"]))
        original = load_workspace(workspace)
        result = self.orchestrator(overrides, cancel_event, progress_token).run(requirement, original)
        changes = changed_files(original, result.files)
        written: list[str] = []
        if arguments.get("write"):
            if not workspace:
                raise ToolError("write=true needs a workspace path")
            written = write_project(Path(workspace).expanduser(), changes)
        diff = "".join(file_diff(path, original.get(path), content) for path, content in changes.items())
        if len(diff) > MAX_DIFF_CHARS:
            diff = diff[:MAX_DIFF_CHARS] + "\n... diff truncated ...\n"
        summary = (
            f"{'All quality gates passed' if result.passed else 'Finished with failing checks'}. "
            f"{len(changes)} file(s) changed" + (f", {len(written)} written to {workspace}." if written else ".")
        )
        return tool_result(
            f"{summary}\n\n{diff}",
            structured={
                "passed": result.passed,
                "changed_files": sorted(changes),
                "written": written,
                "scores": {r.get("persona"): r.get("score") for r in result.reviews},
            },
        )

    def review(self, arguments: dict[str, Any], cancel_event: threading.Event, progress_token: Any) -> dict[str, Any]:
        requirement = str(arguments.get("requirement") or "").strip() or "Review these files."
        files = arguments.get("files")
        if not isinstance(files, dict) or not files:
            files = load_workspace(str(arguments.get("workspace") or ""))
        if not files:
            raise ToolError("Pass files or a workspace that contains readable files")
        orchestrator = self.orchestrator({}, cancel_event, progress_token)
        passed, reviews = orchestrator.review(requirement, {str(k): str(v) for k, v in files.items()})
        lines = [f"{'PASS' if passed else 'NEEDS WORK'}"]
        for review in reviews:
            lines.append(f"\n## {review.get('persona')}: {review.get('score')} ({review.get('status')})")
            for issue in review.get("issues", []):
                lines.append(f"- [{issue.get('severity')}] {issue.get('file', '')} {issue.get('description', '')}")
        return tool_result("\n".join(lines), structured={"passed": passed, "reviews": reviews})


def tool_result(text: str, structured: dict[str, Any] | None = None, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": is_error}
    if structured is not None:
        result["structuredContent"] = structured
    return result


def serve_stdio(config: PipelineConfig) -> None:
    protocol_out = sys.stdout
    sys.stdout = sys.stderr  # stray prints must never corrupt the JSON-RPC stream
    McpServer(config, protocol_out).serve(sys.stdin)
