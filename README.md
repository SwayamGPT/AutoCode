# AutoCode — Autonomous Local Coding Agent

AutoCode is a multi-agent, self-verifying code generator that runs entirely against a **local LLM via Ollama**, with no external API keys. Give it a plain-language requirement, or point it at an existing project, and it plans, writes, critiques, patches, and runs the code in a closed loop until it passes or hits a retry limit.

It ships as an **installable desktop app** (a native window, not a website), a CLI, an **MCP server** that Claude Code or Cursor can call, and an **A2A agent** that other agents can delegate to.

## Architecture

AutoCode is a pipeline of specialized agents:

1. **Context Agent**: if MCP servers are configured, it picks up to three tool calls (docs, issues, schemas) and passes the results to the planner and generator.
2. **Planner Agent**: turns the requirement into a plan (files, structure, test command). With an existing project, it plans a change instead of a rewrite.
3. **Generator Agent**: writes the files. In edit mode, it sees the relevant existing files and returns only what changed.
4. **Review Coordinator**: runs **10 weighted expert personas** in parallel (Business Logic, Security, UI/UX, Performance, Reliability & Availability, Maintainability, Scalability, Data Management, Testing & QA, Observability). Each persona scores against weighted sub-criteria and returns unified-diff patches. **A2A peers**, such as a second AutoCode on a different model, join as extra reviewers.
5. **Patch Engine**: applies reviewer patches with `git apply` in a scratch repo and records which ones failed.
6. **Validator**: runs the plan's test command (or a compile check) in a temp dir. Failures feed into the next iteration.
7. **Memory Manager**: carries findings across iterations, so later reviewers build on earlier ones.

```
requirement → [context via MCP] → plan → generate → [review (10 personas + A2A peers) → patch → validate] → repeat until:
    - every reviewer passes AND validation succeeds, or
    - max_iterations is reached
```

## Desktop app

Download or build `AutoCode.dmg` (macOS) or the `AutoCode` folder (Windows/Linux) and open it. The app runs its backend on a private loopback port and shows the UI in a native window (WKWebView, WebView2, or WebKitGTK).

- Choose a project folder, describe the change, and press **Ctrl/Cmd+Enter**.
- Watch the reviewer gauge fill in live, read per-file diffs, then **Write files** to disk.
- If Ollama, the model, or Git is missing, a banner says exactly what to install.

Build it yourself:

```bash
./scripts/build_desktop.sh          # macOS: dist/AutoCode.app + dist/AutoCode.dmg; Linux/Windows: dist/AutoCode/
```

Requirements on the user's machine: [Ollama](https://ollama.com) with the model pulled (`ollama pull qwen3:4b`) and Git. Settings for the packaged app live in `~/.autocode/.env` (same keys as `.env.example`), and MCP servers in `~/.autocode/mcp.json`. Crashes are logged to `~/.autocode/desktop.log`.

## Running from source

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-desktop.txt
npm --prefix ui ci && npm --prefix ui run build

python app.py desktop                              # native window
python app.py serve                                # same UI at http://127.0.0.1:8000 (+ A2A endpoint)
python app.py "Build a FastAPI signup endpoint"    # CLI, prints the files
python app.py "Add input validation" --workspace ~/code/api            # edit a project in place
python app.py "Add input validation" --workspace ~/code/api --dry-run  # print diffs only
```

UI development with hot reload: run `python app.py serve`, then `npm --prefix ui run dev` (Vite proxies `/api` to port 8000).

## MCP

**AutoCode as an MCP server.** Claude Code, Cursor, or any MCP client can call it:

```bash
claude mcp add autocode -- python /path/to/AutoCode/app.py mcp
```

Tools: `generate_code` (with an optional `workspace` and `write`), `review_code` (files or a workspace), and `list_reviewers`. Long runs send `notifications/progress` and honour cancellation.

**AutoCode as an MCP client.** Put servers in `~/.autocode/mcp.json` (or set `MCP_CONFIG`), in the same format Claude Code and Cursor use:

```json
{ "mcpServers": { "docs": { "command": "npx", "args": ["-y", "@some/mcp-server"], "env": {} } } }
```

Only stdio servers are supported.

## Agent-to-agent (A2A v1.0)

`python app.py serve` (and the Docker image) publishes an agent card at `/.well-known/agent-card.json` and a JSON-RPC endpoint at `/a2a` with `SendMessage`, `GetTask`, and `CancelTask`. It offers two skills:

- `generate`: send the requirement as a text part, plus an optional data part `{"workspace": "...", "settings": {...}}`. You get one artifact per changed file and a review artifact.
- `review`: send a data part `{"skill": "review", "files": {"path": "content"}}`. You get the ten persona reviews.

Set `A2A_PEERS=http://127.0.0.1:8001,...` (or use the UI settings) to add other A2A agents as reviewers. For example, run a second AutoCode with a different model on port 8001. Review-only tasks never call peers, so two instances cannot loop.

## Configuration

Every option can be set with an environment variable (see `.env.example`), a CLI flag, or the UI settings panel:

| Flag | Purpose |
|---|---|
| `--model` | Ollama model to use |
| `--ollama-host` | Ollama server address |
| `--max-iterations` | Max review → patch → validate cycles |
| `--passing-score` | Minimum per-persona score to accept the code |
| `--max-retrieved-files` | Files each persona sees |
| `--skip-verify` | Skip running the validation command |
| `--command-timeout-seconds` | Timeout for validation commands and MCP calls |
| `--mcp-config` | MCP servers file for the context agent |
| `--a2a-peers` | Comma-separated A2A agent URLs used as extra reviewers |

## Security notes

- The Validator runs the **model-written test command** with `shell=True` in a temp directory on your machine. Use Docker if you need isolation.
- The HTTP server binds to `127.0.0.1` by default. It rejects cross-origin writes and foreign `Host` headers (DNS rebinding). It has no authentication, so do not expose it to a network.
- Patch and write paths are confined to the project folder (no absolute paths or `..`).

## Docker

```bash
cp .env.example .env
docker compose up          # Ollama + model pull + AutoCode UI/A2A at http://127.0.0.1:8000
```

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest           # backend, MCP, A2A, HTTP API (uses a scripted fake LLM; no Ollama needed)
npm --prefix ui test       # UI state reducer
```

## Project structure

```
AutoCode/
├── app.py                    # Entry point
├── agent_platform/
│   ├── cli.py                # CLI + subcommands: desktop, serve, mcp
│   ├── orchestrator.py       # context → plan → generate → review → patch → validate loop
│   ├── planner.py / generator.py / reviewer.py / personas.py / retriever.py
│   ├── context_agent.py      # MCP tool selection before planning
│   ├── patch_engine.py / validator.py / memory.py / workspace.py
│   ├── llm.py                # Ollama client (JSON mode, retry, cancellation)
│   ├── runs.py               # Background runs + event log
│   ├── server.py             # HTTP API, SSE events, static UI, A2A routes
│   ├── a2a.py / a2a_client.py
│   ├── mcp_server.py / mcp_client.py
│   └── desktop.py            # Native window (pywebview)
├── ui/                       # React + TypeScript (Vite) desktop UI
├── packaging/autocode.spec   # PyInstaller spec
├── scripts/build_desktop.sh
└── tests/
```
