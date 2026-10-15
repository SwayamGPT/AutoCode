"""Agent2Agent (A2A v1.0) server binding.

Other agents discover AutoCode at /.well-known/agent-card.json and call SendMessage / GetTask /
CancelTask (JSON-RPC) on /a2a. Each A2A task is a Run. The client side lives in a2a_client.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from .a2a_client import A2A_PATH, A2A_PROTOCOL_VERSION
from .models import PipelineConfig
from .runs import Run, RunStore

TASK_STATES = {
    "running": "TASK_STATE_WORKING",
    "done": "TASK_STATE_COMPLETED",
    "error": "TASK_STATE_FAILED",
    "cancelled": "TASK_STATE_CANCELED",
}
# JSON-RPC and A2A error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
TASK_NOT_FOUND = -32001
TASK_NOT_CANCELABLE = -32002


class RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def agent_card(base_url: str) -> dict[str, Any]:
    return {
        "name": "AutoCode",
        "description": "Local multi-agent code generator: plans in ten steps (one per quality dimension, from "
        "business logic to observability), makes per-dimension code changes, then reviews, patches and tests "
        "until the code passes.",
        "version": "1.0.0",
        "supportedInterfaces": [
            {"url": base_url + A2A_PATH, "protocolBinding": "JSONRPC", "protocolVersion": A2A_PROTOCOL_VERSION}
        ],
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "skills": [
            {
                "id": "generate",
                "name": "Generate code",
                "description": "Build or change a project from a plain-language requirement. "
                "Send the requirement as text; optionally a data part {\"workspace\": path, \"settings\": {...}}. "
                "Returns one artifact per changed file plus a review artifact.",
                "tags": ["code-generation", "refactoring"],
                "examples": ["Build a FastAPI endpoint that validates and stores user signups"],
            },
            {
                "id": "review",
                "name": "Review code",
                "description": "Score files with ten weighted reviewer personas and return issues and unified-diff "
                "patches. Send a data part {\"skill\": \"review\", \"files\": {path: content}}.",
                "tags": ["code-review", "security", "testing"],
                "examples": ["Review this module for security and reliability issues"],
            },
        ],
    }


def iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def text_message(role: str, text: str, task_id: str, context_id: str) -> dict[str, Any]:
    return {
        "messageId": uuid.uuid4().hex,
        "role": role,
        "parts": [{"text": text}],
        "taskId": task_id,
        "contextId": context_id,
    }


def task_json(run: Run) -> dict[str, Any]:
    status: dict[str, Any] = {"state": TASK_STATES[run.status], "timestamp": iso(run.created_at)}
    if run.error:
        status["message"] = text_message("ROLE_AGENT", run.error, run.id, run.context_id)
    artifacts: list[dict[str, Any]] = []
    if run.status == "done":
        if run.mode == "generate":
            for index, (path, content) in enumerate(sorted(run.changed().items())):
                artifacts.append(
                    {
                        "artifactId": f"file-{index}",
                        "name": path,
                        "parts": [{"text": content, "filename": path, "mediaType": "text/plain"}],
                    }
                )
        artifacts.append(
            {
                "artifactId": "review",
                "name": "review",
                "parts": [
                    {"data": {"passed": run.passed, "reviews": run.reviews}, "mediaType": "application/json"}
                ],
            }
        )
    return {
        "id": run.id,
        "contextId": run.context_id,
        "status": status,
        "artifacts": artifacts,
        "history": [text_message("ROLE_USER", run.requirement, run.id, run.context_id)],
    }


def parse_message(message: Any) -> tuple[str, dict[str, Any]]:
    """Split an A2A message into the requirement text and merged data-part options."""
    if not isinstance(message, dict) or not isinstance(message.get("parts"), list):
        raise RpcError(INVALID_PARAMS, "params.message.parts is required")
    texts: list[str] = []
    options: dict[str, Any] = {}
    for part in message["parts"]:
        if not isinstance(part, dict):
            continue
        if isinstance(part.get("text"), str):
            texts.append(part["text"])
        elif isinstance(part.get("data"), dict):
            options.update(part["data"])
    if isinstance(message.get("metadata"), dict):
        options = {**message["metadata"], **options}
    return "\n".join(texts).strip(), options


class A2AServer:
    def __init__(self, base_config: PipelineConfig, store: RunStore, settings_parser: Any) -> None:
        self.base_config = base_config
        self.store = store
        self.settings_parser = settings_parser

    def handle(self, payload: Any) -> dict[str, Any]:
        request_id = payload.get("id") if isinstance(payload, dict) else None
        try:
            if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0" or "method" not in payload:
                raise RpcError(INVALID_REQUEST, "Invalid JSON-RPC request")
            params = payload.get("params") or {}
            if not isinstance(params, dict):
                raise RpcError(INVALID_PARAMS, "params must be an object")
            method = payload["method"]
            if method == "SendMessage":
                result = self.send_message(params)
            elif method == "GetTask":
                result = task_json(self.find(params))
            elif method == "CancelTask":
                result = self.cancel(params)
            else:
                raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except RpcError as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": exc.code, "message": str(exc)}}

    def send_message(self, params: dict[str, Any]) -> dict[str, Any]:
        message = params.get("message")
        requirement, options = parse_message(message)
        skill = str(options.get("skill") or "generate")
        if skill not in {"generate", "review"}:
            raise RpcError(INVALID_PARAMS, f"Unknown skill: {skill}")
        files = options.get("files")
        if skill == "review" and not isinstance(files, dict):
            raise RpcError(INVALID_PARAMS, "The review skill needs a data part with files: {path: content}")
        if not requirement and skill == "generate":
            raise RpcError(INVALID_PARAMS, "Send the requirement as a text part")
        settings = options.get("settings") or {}
        try:
            config = self.settings_parser(self.base_config, settings if isinstance(settings, dict) else {})
        except ValueError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from exc
        run = Run(
            requirement or "Review these files.",
            str(options.get("workspace") or "") if skill == "generate" else "",
            config,
            mode=skill,
            files={str(k): str(v) for k, v in files.items()} if skill == "review" else None,
            context_id=str(message.get("contextId") or ""),
        )
        self.store.start(run)
        configuration = params.get("configuration") or {}
        if not configuration.get("returnImmediately"):
            run.wait()
        return {"task": task_json(run)}

    def find(self, params: dict[str, Any]) -> Run:
        run = self.store.get(str(params.get("id", "")))
        if not run:
            raise RpcError(TASK_NOT_FOUND, "Task not found")
        return run

    def cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        run = self.find(params)
        if run.status != "running":
            raise RpcError(TASK_NOT_CANCELABLE, "Task is not running")
        run.cancel_event.set()
        run.wait()
        return task_json(run)
