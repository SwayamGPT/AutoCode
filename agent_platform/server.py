from __future__ import annotations

import dataclasses
import json
import logging
import mimetypes
import shutil
import socketserver
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .a2a import PARSE_ERROR, A2AServer, agent_card
from .a2a_client import AGENT_CARD_PATH, peer_name, peer_urls
from .config import CONFIG_HOME
from .models import PipelineConfig
from .personas import PERSONAS
from .runs import FINISHED, Run, RunStore
from .workspace import changed_files, load_workspace, write_project

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY_BYTES = 1_000_000
SSE_HEARTBEAT_SECONDS = 15
FIELD_TYPES = {"str": str, "int": int, "float": float, "bool": bool}
HEALTH_TIMEOUT_SECONDS = 3
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
RUNS_DIR = CONFIG_HOME / "runs"

http_logger = logging.getLogger("autocode.http")
MISSING_UI = b"AutoCode UI is not built. Run: npm --prefix ui install && npm --prefix ui run build"
PAGE_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "font-src 'self' data:; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def coerce_settings(base: PipelineConfig, settings: dict[str, Any]) -> PipelineConfig:
    """Apply UI overrides to the base config, coercing each value to the field's declared type."""
    overrides: dict[str, Any] = {}
    for field in dataclasses.fields(base):
        value = settings.get(field.name)
        if value is None or value == "":
            continue
        cast = FIELD_TYPES[str(field.type)]
        if cast is bool and not isinstance(value, bool):
            raise ValueError(f"Invalid value for {field.name}: {value!r}")
        try:
            overrides[field.name] = cast(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid value for {field.name}: {value!r}") from exc
    if overrides.get("max_iterations", base.max_iterations) < 1:
        raise ValueError("max_iterations must be at least 1")
    return dataclasses.replace(base, **overrides)


def check_health(config: PipelineConfig) -> dict[str, Any]:
    """What the setup banner needs: is Ollama reachable, is the model pulled, is git installed."""
    from ollama import Client

    health = {"ollama": False, "model": False, "git": shutil.which("git") is not None, "message": ""}
    client = Client(host=config.ollama_host, timeout=HEALTH_TIMEOUT_SECONDS)
    try:
        client.list()
        health["ollama"] = True
        client.show(config.model)
        health["model"] = True
    except Exception as exc:  # connection refused, 404 model not found, ...
        health["message"] = str(exc)[:300]
    return health


class LocalHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer without the reverse-DNS lookup in server_bind (getfqdn can hang for minutes,
    e.g. inside a packaged macOS app)."""

    daemon_threads = True

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = str(host), int(port)


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


def make_handler(base_config: PipelineConfig, store: RunStore) -> type[BaseHTTPRequestHandler]:
    a2a = A2AServer(base_config, store, coerce_settings)

    class Handler(BaseHTTPRequestHandler):
        server_version = "AutoCode/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            if "/events" not in self.path:
                http_logger.info("%s %s", self.address_string(), format % args)

        def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def read_body(self) -> bytes:
            # A JSON content type forces a CORS preflight (never answered) and the Origin check
            # rejects other sites, so a web page cannot drive runs or file writes on this server.
            if "application/json" not in self.headers.get("Content-Type", ""):
                raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Expected application/json.")
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                raise ApiError(HTTPStatus.FORBIDDEN, "Cross-origin request rejected.")
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid Content-Length.") from exc
            if length > MAX_BODY_BYTES:
                raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large.")
            return self.rfile.read(length)

        def read_json(self) -> dict[str, Any]:
            try:
                payload = json.loads(self.read_body() or b"{}")
            except json.JSONDecodeError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, f"Invalid JSON: {exc}") from exc
            if not isinstance(payload, dict):
                raise ApiError(HTTPStatus.BAD_REQUEST, "Expected a JSON object.")
            return payload

        def get_run(self, run_id: str) -> Run:
            run = store.get(run_id)
            if not run:
                raise ApiError(HTTPStatus.NOT_FOUND, "Run not found.")
            return run

        def check_host(self) -> None:
            # On a loopback bind, reject other Host names so a DNS-rebinding page cannot read the API.
            if self.server.server_address[0] not in LOOPBACK_HOSTS:
                return
            host = urlparse(f"//{self.headers.get('Host', '')}").hostname or ""
            if host not in LOOPBACK_HOSTS:
                raise ApiError(HTTPStatus.FORBIDDEN, "Unexpected Host header.")

        def dispatch(self, method: str) -> None:
            url = urlparse(self.path)
            parts = [part for part in url.path.split("/") if part]
            try:
                self.check_host()
                self.route(method, parts, parse_qs(url.query))
            except ApiError as exc:
                self.send_json({"error": str(exc)}, exc.status)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self) -> None:
            self.dispatch("GET")

        def do_POST(self) -> None:
            self.dispatch("POST")

        def route(self, method: str, parts: list[str], query: dict[str, list[str]]) -> None:
            if method == "GET" and parts in ([], ["index.html"]):
                return self.serve_static("index.html")
            if method == "GET" and parts[:1] == ["assets"]:
                return self.serve_static("/".join(parts))
            if method == "GET" and "/" + "/".join(parts) == AGENT_CARD_PATH:
                return self.send_json(agent_card(f"http://{self.headers.get('Host', 'localhost')}"))
            if method == "POST" and parts == ["a2a"]:
                return self.handle_a2a()
            if parts[:1] != ["api"]:
                raise ApiError(HTTPStatus.NOT_FOUND, "Not found.")
            # /api/runs/<id>/events -> ("GET", "runs", "*", "events")
            route = (method, *parts[1:2], *(["*"] if len(parts) > 2 else []), *parts[3:])
            run_id = parts[2] if len(parts) > 2 else ""

            if route == ("GET", "config"):
                reviewers = [p.name for p in PERSONAS] + [peer_name(url) for url in peer_urls(base_config)]
                return self.send_json({"config": dataclasses.asdict(base_config), "personas": reviewers})
            if route == ("GET", "health"):
                return self.send_json(check_health(base_config))
            if route == ("GET", "workspace"):
                return self.browse_workspace((query.get("path") or [""])[0])
            if route == ("GET", "runs"):
                return self.send_json({"runs": store.list()})
            if route == ("POST", "runs"):
                return self.create_run(self.read_json())
            if route == ("GET", "runs", "*"):
                return self.send_json(self.get_run(run_id).snapshot())
            if route == ("GET", "runs", "*", "events"):
                return self.stream_events(self.get_run(run_id), query)
            if route == ("POST", "runs", "*", "cancel"):
                self.read_json()
                self.get_run(run_id).cancel_event.set()
                return self.send_json({"ok": True})
            if route == ("POST", "runs", "*", "apply"):
                return self.apply_run(self.get_run(run_id), self.read_json())
            raise ApiError(HTTPStatus.NOT_FOUND, "Not found.")

        def handle_a2a(self) -> None:
            body = self.read_body()
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as exc:
                error = {"code": PARSE_ERROR, "message": f"Parse error: {exc}"}
                return self.send_json({"jsonrpc": "2.0", "id": None, "error": error})
            self.send_json(a2a.handle(payload))

        def serve_static(self, name: str) -> None:
            root = STATIC_DIR.resolve()
            path = (root / name).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                if name == "index.html":
                    self.send_response(HTTPStatus.SERVICE_UNAVAILABLE)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(MISSING_UI)))
                    self.end_headers()
                    self.wfile.write(MISSING_UI)
                    return
                raise ApiError(HTTPStatus.NOT_FOUND, "Not found.")
            body = path.read_bytes()
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type.endswith("javascript"):
                content_type += "; charset=utf-8"
            self.send_response(HTTPStatus.OK)
            for header, value in PAGE_HEADERS.items():
                self.send_header(header, value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def browse_workspace(self, path: str) -> None:
            try:
                files = load_workspace(path)
            except ValueError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
            exists = bool(path) and Path(path).expanduser().is_dir()
            self.send_json({"path": path, "exists": exists, "files": files})

        def create_run(self, payload: dict[str, Any]) -> None:
            requirement = str(payload.get("requirement", "")).strip()
            if not requirement:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Requirement is required.")
            settings = payload.get("settings") or {}
            if not isinstance(settings, dict):
                raise ApiError(HTTPStatus.BAD_REQUEST, "settings must be an object.")
            try:
                config = coerce_settings(base_config, settings)
                run = Run(requirement, str(payload.get("workspace") or "").strip(), config)
            except ValueError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
            store.start(run)
            self.send_json(run.summary(), HTTPStatus.CREATED)

        def apply_run(self, run: Run, payload: dict[str, Any]) -> None:
            if run.status not in FINISHED:
                raise ApiError(HTTPStatus.CONFLICT, "Run is still in progress.")
            target = str(payload.get("workspace") or run.workspace).strip()
            if not target:
                raise ApiError(HTTPStatus.BAD_REQUEST, "No workspace directory to write to.")
            # Same workspace: write only what changed. New directory: write the whole project.
            changes = changed_files(run.original, run.files) if target == run.workspace else run.files
            selected = payload.get("paths")
            if isinstance(selected, list):
                changes = {path: content for path, content in changes.items() if path in selected}
            root = Path(target).expanduser()
            if root.exists() and not root.is_dir():
                raise ApiError(HTTPStatus.BAD_REQUEST, f"Not a directory: {root}")
            written = write_project(root, changes)
            self.send_json({"workspace": str(root), "written": written})

        def stream_events(self, run: Run, query: dict[str, list[str]]) -> None:
            last_id = self.headers.get("Last-Event-ID")
            try:
                index = int(last_id) + 1 if last_id else int((query.get("from") or ["0"])[0])
            except ValueError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid event cursor.") from exc
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            while True:
                with run.cond:
                    if index >= len(run.events):
                        run.cond.wait(timeout=SSE_HEARTBEAT_SECONDS)
                    pending = run.events[index:]
                if not pending:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                for event in pending:
                    message = f"id: {event['id']}\nevent: {event['kind']}\ndata: {json.dumps(event)}\n\n"
                    self.wfile.write(message.encode("utf-8"))
                    if event["kind"] == "finished":
                        self.wfile.flush()
                        return
                self.wfile.flush()
                index += len(pending)

    return Handler


def serve(config: PipelineConfig, host: str, port: int) -> None:
    server = LocalHTTPServer((host, port), make_handler(config, RunStore(RUNS_DIR, config)))
    print(f"AutoCode UI running at http://{host}:{port}  (model: {config.model}, ollama: {config.ollama_host})")
    print(f"A2A agent card: http://{host}:{port}{AGENT_CARD_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
