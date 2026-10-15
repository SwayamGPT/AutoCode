# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AutoCode: a multi-agent code generator driven by a local Ollama model, organised around ten quality dimensions (`personas.py`: Business Logic, Security, UI/UX, Performance, Reliability & Availability, Maintainability, Scalability, Data Management, Testing & QA, Observability & Monitoring). Each run plans in ten steps (one per dimension), builds the Business Logic foundation, lets each other dimension make its own code changes, then loops validate → ten dimension reviewers (plus optional A2A peers) → `git apply` patches until the gates pass or `max_iterations` is hit. It ships as a desktop app (React UI in a pywebview window, packaged with PyInstaller), a CLI, an MCP stdio server, and an A2A v1.0 JSON-RPC agent.

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

There is no formatter config; `ruff check agent_platform tests --select F,E9,B` is clean and should stay clean. CI (`.github/workflows/ci.yml`) runs ruff and pytest on Python 3.11 and 3.13, plus the UI typecheck, vitest, and build. `tests/fakes.FakeLLM` scripts every agent by call label (`Planner`, `Plan: <dimension>`, `Generator`, `Implement: <dimension>`, persona names for reviews).

## Configuration

`config.py` has `DEFAULTS` for every env var. `load_env_files()` reads `./.env`, then `~/.autocode/.env` (`setdefault`, so real env vars win). Adding a config option touches: `PipelineConfig` (`models.py`, with a default when it is optional), `DEFAULTS` + `config_from_env()`, the argparse flag and `PipelineConfig(...)` in `cli.py`, `.env.example`, and the `Config` type plus the settings form in `ui/src`. The UI and A2A send per-run overrides that `server.coerce_settings()` validates against the dataclass field types (bool fields must be real JSON booleans).

## Architecture

**Pipeline** (`orchestrator.py`): `ContextAgent` (MCP tools) → `PlannerAgent` (base plan) → `DimensionPlanner` (`dimensions.py`, sequential, each step sees earlier steps' decisions; writes `plan["dimensions"]`) → `GeneratorAgent` (foundation) → `DimensionImplementer` for dimensions 2–10 (skipped when `applicable` is false; skipped entirely in `fast_mode`) → `review_loop`: `verify` first, then `review_round`, then `apply_patches` (not on the last iteration). The loop returns the best `Snapshot` (ranked by passed, then verified, then average score). The project is an in-memory `dict[str, str]` (path → content), and all model-proposed files go through `merge_files`, which drops unsafe paths. Reviewers get their own dimension plan (`find_dimension_plan`), and retrieval boosts the files that plan lists. Persona order matters: it is the planning and implementation order, and the UI's step numbering. `Orchestrator.review()` is review-only mode and deliberately uses local personas only, so A2A peers can never recurse into each other.

**Events, not prints**: the orchestrator reports progress through `emit(kind, data)`. Kinds: `status`, `llm_call`, `llm_done` (duration and tokens), `plan`, `dimension_plan`, `dimension_change`, `mcp_call`, `files`, `review`, `scorecard`, `patches`, `verify`, plus `finished` (added by `Run`). When adding a kind, add it to `EVENT_KINDS` in `ui/src/state/run.ts`; `tests/test_runs.py` fails if the UI would miss one. The CLI uses `console_emit`, the HTTP server stores events on a `Run` (`runs.py`) and streams them over SSE, and the MCP server turns them into `notifications/progress`. Never `print` to stdout from pipeline code, because `mcp` mode uses stdout for JSON-RPC (`serve_stdio` reroutes `sys.stdout` to stderr as a guard).

**LLM** (`llm.py`): every agent calls `ask_json`, which uses Ollama `format="json"` with one retry on a parse failure. Cancellation is a `threading.Event` checked before each call (raises `RunCancelled`). Tests inject `tests/fakes.FakeLLM` through `Orchestrator(..., llm=...)`; server tests monkeypatch `runs.Orchestrator`.

**Runs and HTTP** (`runs.py`, `server.py`): each UI or A2A request becomes a `Run` running on a daemon thread. `Run` aggregates `llm_done` metrics and logs to the `autocode.*` loggers (`logs.py` attaches a rotating file under `CONFIG_HOME/logs`). `RunStore(persist_dir, config)` saves finished runs as `CONFIG_HOME/runs/<id>.json` (atomic replace, only changed files, pruned to 50) and reloads them on start. `CONFIG_HOME` is `$AUTOCODE_HOME` or `~/.autocode`; `tests/conftest.py` points it at a temp dir. `server.py` is stdlib `http.server` serving `/api/*` (JSON + SSE), the built UI from `agent_platform/static`, `/.well-known/agent-card.json` and `/a2a`. Always use `LocalHTTPServer`: the stock `HTTPServer.server_bind` does a reverse-DNS `getfqdn` that hangs inside the packaged macOS app. Safety checks: POSTs require `application/json` and a same-origin `Origin`, loopback binds reject foreign `Host` headers, and static paths must resolve inside `static/`.

**A2A** (`a2a.py` server, `a2a_client.py` client): v1.0 JSON shapes, with methods `SendMessage`, `GetTask` and `CancelTask`, `TASK_STATE_*` and `ROLE_*` enums, and Parts as `{text|data, filename, mediaType}`. Each peer URL in `A2A_PEERS` becomes a `RemoteReviewer` that calls the peer's `review` skill. `review_from_task` collapses the peer's reviews into one (lowest score wins). The client lives in its own module because `a2a.py → runs.py → orchestrator.py` would otherwise form an import cycle.

**MCP**: `mcp_server.py` is a hand-written stdio JSON-RPC server (tools `generate_code`, `review_code`, `list_reviewers`; each tool call runs on a thread so `notifications/cancelled` can arrive). `mcp_client.py` is a stdio-only client used by `context_agent.py`, which reads a Claude-style `{"mcpServers": {...}}` file.

**UI** (`ui/`): React 19 + TypeScript + Vite. It builds into `../agent_platform/static` with `base: "./"`. State is one reducer (`ui/src/state/run.ts`) that consumes the same event kinds as above, and `useRunStream` replays the SSE stream from the start, so reopening a run rebuilds its view. Fonts are bundled through `@fontsource` (the CSP allows `font-src 'self' data:` and no remote origins). Write `useEffect` callbacks with braces: Chrome's `scrollIntoView()` returns a Promise, which React treats as a cleanup function. The desktop folder picker comes from pywebview's `js_api` (`window.pywebview.api.pick_folder`) and only exists inside the app window.

**Desktop and packaging** (`desktop.py`, `packaging/autocode.spec`): the backend binds `127.0.0.1:0` in-process and pywebview opens a window on it. A frozen app with no arguments launches the desktop window. In frozen mode `sys.executable` is the app binary, so `validator.python_executable()` looks for `python3` on PATH. Windowed builds start with `sys.stdout/stderr = None`, which `cli.main` replaces with devnull.

## Security caveats

`Validator` runs the plan's model-written `test_command` with `shell=True` on the host (only the cwd is a temp dir). The HTTP server has no authentication, so keep it on loopback.

## Models, memory and learning

- **Providers** (`providers.py`): model specs are `"<provider>:<model>"`; an unknown prefix means an Ollama model (`qwen3:4b`). `build_llms(config)` (`llm.py`) returns one `LLMClient` per role (`planner`, `coder`, `reviewer`, `critic`); empty `*_model` fields fall back to `model`. Anthropic uses the official SDK (beta messages + `fallbacks: "default"`, checks `stop_reason == "refusal"`); everything else OpenAI-compatible is plain `urllib` with retries on 408/409/429/5xx.
- **Settings** (`settings.py`): precedence is defaults < env/.env < `CONFIG_HOME/settings.json` (saved from the UI, 0600). `RUN_LOCKED` fields (providers, base URLs, MCP config, database, embedding model, learning) cannot be changed per run; `A2A_LOCKED` additionally blocks `a2a_peers`. Masked keys sent back by the UI keep the stored key (`unmask_providers`).
- **Memory** (`memory_store.py`): `open_store(url)` returns `PgVectorStore` (pgvector, untyped `vector` column filtered by `embedding_model`) or `SqliteStore`; `""` tries the compose Postgres at `AUTO_DATABASE_URL` then SQLite, `"sqlite"` forces SQLite (tests set this). Keep `PG_COLUMNS` spelled out — a string replace once turned `embedding_model` into `embedding::text_model`.
- **Learning** (`learning.py`): `Learner.recall` before planning (lessons per dimension + `General`), `choose_strategy` (Thompson sampling over `STRATEGIES`) before each implementation step, `learn` after the run (reward = dimension review score / 100, then the `Reflect` critic writes lessons, de-duplicated). Learning failures are logged and never fail a run. `FixerAgent` (`fixer.py`) runs once whenever validation fails, before review.
- New events: `memory_recall`, `strategy`, `fix`, `learning`, `reflection` (all in `EVENT_KINDS`). `FakeLLM` also answers `Fix` and `Reflect`; tests run with `EMBEDDING_MODEL=hash` and `DATABASE_URL=sqlite`. The pgvector test runs only with `TEST_DATABASE_URL` (CI provides a pgvector service).
- **Docker**: `docker compose up` needs no `.env` (all `${VAR:-default}`): Ollama + pulls of the chat and embedding models, `pgvector/pgvector:pg17`, and the app with `DATABASE_URL` set and `/root/.autocode` on a volume. `requirements.txt` must keep `psycopg[binary]` and `anthropic` — the image only installs that file, and a missing `psycopg` silently falls back to SQLite (the health check now flags it).

## Control flow (LangGraph)

`graph.py` compiles a `StateGraph(PipelineState)`; `Orchestrator.run()` only checks models, creates `self.trace` and calls `run_pipeline`. Nodes wrap orchestrator methods (`make_plan`, `build_foundation`, `implement_dimensions`, `verify`, `fix`, `review_round`, `apply_patches`) and emit a `node` event first. Branching lives only in the conditional edges (fast mode, failed verify → `fix`, review → `patch` or `finish`). `PipelineState` hints are evaluated at runtime by LangGraph, so do not use `TYPE_CHECKING`-only types there. The recursion limit is `FIXED_STEPS + 4 * max_iterations`. Tests assert the exact node path (`tests/test_orchestrator.py`).

## Environment settings

`config.py` loads `./.env` then `AUTOCODE_HOME/.env` at import time (`setdefault`, so real env vars win), before anything reads the environment; `agent_platform/__init__` importing anything pulls it in. Module-level limits use `setting("NAME", default)`, which casts to the default's type (bools via `parse_bool`) and treats empty values as unset. When adding a tunable, use `setting(...)` and document it in `.env.example` (grouped by area). Preset base URLs can be overridden with `<NAME>_BASE_URL`.
