# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AutoCode: a multi-agent code generator driven by a local Ollama model. It plans, generates or edits files, reviews them with 10 weighted personas (plus optional A2A peer agents), applies reviewer patches with `git apply`, and runs a validation command, looping until the gates pass or `max_iterations` is hit. It ships as a desktop app (React UI in a pywebview window, packaged with PyInstaller), a CLI, an MCP stdio server, and an A2A v1.0 JSON-RPC agent.

## Commands

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt -r requirements-desktop.txt
npm --prefix ui ci && npm --prefix ui run build    # required before `serve`/`desktop` (output is gitignored)

.venv/bin/python -m pytest                          # all backend tests (fake LLM, no Ollama needed)
.venv/bin/python -m pytest tests/test_a2a.py::test_agent_card   # single test
npm --prefix ui test                                # vitest (reducer + tree helpers)
npm --prefix ui run typecheck

python app.py desktop | serve [--port N] | mcp      # subcommands; anything else is the CLI pipeline
python app.py "req" --workspace DIR [--dry-run]     # edit an existing project
npm --prefix ui run dev                             # Vite dev server, proxies /api to 127.0.0.1:8000
./scripts/build_desktop.sh                          # PyInstaller app (+ .dmg on macOS) into dist/
```

There is no formatter config; `ruff check agent_platform tests --select F,E9,B` is clean and should stay clean.

## Configuration

`config.py` has `DEFAULTS` for every env var. `load_env_files()` reads `./.env`, then `~/.autocode/.env` (`setdefault`, so real env vars win). Adding a config option touches: `PipelineConfig` (`models.py`, with a default when it is optional), `DEFAULTS` + `config_from_env()`, the argparse flag and `PipelineConfig(...)` in `cli.py`, `.env.example`, and the `Config` type plus the settings form in `ui/src`. The UI and A2A send per-run overrides that `server.coerce_settings()` validates against the dataclass field types (bool fields must be real JSON booleans).

## Architecture

**Pipeline** (`orchestrator.py`): `ContextAgent` (MCP tools) → `PlannerAgent` → `GeneratorAgent` → loop of `review_round` → `PatchEngine` → `Validator`. The project is an in-memory `dict[str, str]` (path → content). In edit mode, the generator receives the existing files (budgeted by `generator.context_files`) and its output is merged over them. `Orchestrator.review()` is review-only mode and deliberately uses local personas only, so A2A peers can never recurse into each other.

**Events, not prints**: the orchestrator reports progress through `emit(kind, data)`. Kinds: `status`, `llm_call`, `plan`, `mcp_call`, `files`, `review`, `scorecard`, `patches`, `verify`, plus `finished` (added by `Run`). The CLI uses `console_emit`, the HTTP server stores events on a `Run` (`runs.py`) and streams them over SSE, and the MCP server turns them into `notifications/progress`. Never `print` to stdout from pipeline code, because `mcp` mode uses stdout for JSON-RPC (`serve_stdio` reroutes `sys.stdout` to stderr as a guard).

**LLM** (`llm.py`): every agent calls `ask_json`, which uses Ollama `format="json"` with one retry on a parse failure. Cancellation is a `threading.Event` checked before each call (raises `RunCancelled`). Tests inject `tests/fakes.FakeLLM` through `Orchestrator(..., llm=...)`; server tests monkeypatch `runs.Orchestrator`.

**Runs and HTTP** (`runs.py`, `server.py`): each UI or A2A request becomes a `Run` running on a daemon thread. `server.py` is stdlib `http.server` serving `/api/*` (JSON + SSE), the built UI from `agent_platform/static`, `/.well-known/agent-card.json` and `/a2a`. Always use `LocalHTTPServer`: the stock `HTTPServer.server_bind` does a reverse-DNS `getfqdn` that hangs inside the packaged macOS app. Safety checks: POSTs require `application/json` and a same-origin `Origin`, loopback binds reject foreign `Host` headers, and static paths must resolve inside `static/`.

**A2A** (`a2a.py` server, `a2a_client.py` client): v1.0 JSON shapes, with methods `SendMessage`, `GetTask` and `CancelTask`, `TASK_STATE_*` and `ROLE_*` enums, and Parts as `{text|data, filename, mediaType}`. Each peer URL in `A2A_PEERS` becomes a `RemoteReviewer` that calls the peer's `review` skill. `review_from_task` collapses the peer's reviews into one (lowest score wins). The client lives in its own module because `a2a.py → runs.py → orchestrator.py` would otherwise form an import cycle.

**MCP**: `mcp_server.py` is a hand-written stdio JSON-RPC server (tools `generate_code`, `review_code`, `list_reviewers`; each tool call runs on a thread so `notifications/cancelled` can arrive). `mcp_client.py` is a stdio-only client used by `context_agent.py`, which reads a Claude-style `{"mcpServers": {...}}` file.

**UI** (`ui/`): React 19 + TypeScript + Vite. It builds into `../agent_platform/static` with `base: "./"`. State is one reducer (`ui/src/state/run.ts`) that consumes the same event kinds as above, and `useRunStream` replays the SSE stream from the start, so reopening a run rebuilds its view. Fonts are bundled through `@fontsource` (the CSP allows `font-src 'self' data:` and no remote origins). Write `useEffect` callbacks with braces: Chrome's `scrollIntoView()` returns a Promise, which React treats as a cleanup function. The desktop folder picker comes from pywebview's `js_api` (`window.pywebview.api.pick_folder`) and only exists inside the app window.

**Desktop and packaging** (`desktop.py`, `packaging/autocode.spec`): the backend binds `127.0.0.1:0` in-process and pywebview opens a window on it. A frozen app with no arguments launches the desktop window. In frozen mode `sys.executable` is the app binary, so `validator.python_executable()` looks for `python3` on PATH. Windowed builds start with `sys.stdout/stderr = None`, which `cli.main` replaces with devnull.

## Security caveats

`Validator` runs the plan's model-written `test_command` with `shell=True` on the host (only the cwd is a temp dir). The HTTP server has no authentication, so keep it on loopback.
