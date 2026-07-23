# AutoCode — Autonomous Local Coding Agent

AutoCode is a multi-agent, self-verifying code-generation pipeline that runs entirely against a **local LLM via Ollama** — no external API keys required. Give it a plain-language requirement, and it plans, writes, critiques, patches, and executes the resulting code in a closed loop until it passes or hits a retry limit.

## Architecture

AutoCode isn't a single prompt → single response tool. It's built as a pipeline of specialized agents:

1. **Planner Agent** — turns your requirement into an architecture plan (what files, what structure).
2. **Generator Agent** — writes the actual code from that plan.
3. **Review Coordinator** — runs the generated code past **10 weighted expert personas** (Business Logic, Security, UI/UX, Performance, Reliability & Availability, Maintainability, Scalability, Data Management, Testing & QA, Observability). Each persona scores against its own weighted sub-criteria — e.g. Security weighs injection risk at 30%, secrets at 20%, auth boundaries at 25%, input validation at 25% — and returns concrete patch suggestions, not just a score.
4. **Patch Engine** — automatically applies the reviewers' suggested patches to the codebase, tracking which patches applied cleanly and which failed.
5. **Validator** — actually **executes real commands** against the project (build/test/run), not just an LLM's opinion of correctness. Failures here are fed straight back into the next iteration.
6. **Memory Manager** — carries a running project summary and the history of past review findings across iterations, so later passes build on what earlier reviewers already flagged instead of re-discovering the same issues.

## The loop

```
requirement → plan → generate → [review (10 personas) → patch → validate] → repeat until:
    - every persona passes AND validation succeeds, or
    - max_iterations is reached
```

This is closer to a **self-healing CI loop** than a chatbot wrapper: code gets generated, critiqued from ten angles by domain-specific weighted rubrics, automatically patched, then actually run to confirm the patches worked — and only then is it accepted.

## Usage

```bash
# Interactive
python app.py

# Direct requirement
python app.py "Build a FastAPI endpoint that validates and stores user signups"

# With custom model/config
python app.py "..." --model llama3 --max-iterations 5 --passing-score 80
```

## Configuration

All options can be set via CLI flags or environment variables (see `.env.example`):

| Flag | Purpose |
|---|---|
| `--model` | Ollama model to use |
| `--ollama-host` | Ollama server address |
| `--max-iterations` | Max generate → review → patch → validate cycles |
| `--passing-score` | Minimum per-persona score to accept generated code |
| `--max-retrieved-files` | Cap on context files pulled in during review |
| `--skip-verify` | Skip the command-execution validation step |
| `--command-timeout-seconds` | Timeout for any commands the Validator runs |

## Requirements

- Python 3.7+
- [Ollama](https://ollama.com) running locally (or reachable at `--ollama-host`)
- Docker (optional — see `Dockerfile` / `docker-compose.yml` for containerized runs)

## Project Structure

```
AutoCode/
├── app.py                 # Entry point
├── agent_platform/
│   ├── cli.py              # Argument parsing + pipeline wiring
│   ├── config.py           # Env/config loading
│   ├── models.py           # PipelineConfig, Persona, and data models
│   ├── personas.py         # 10 weighted expert review personas
│   ├── planner.py          # Planner Agent
│   ├── generator.py        # Generator Agent
│   ├── reviewer.py         # Review Coordinator (runs all personas)
│   ├── patch_engine.py      # Applies reviewer-suggested patches
│   ├── validator.py         # Executes real commands to verify output
│   ├── memory.py            # Cross-iteration project + review memory
│   ├── retriever.py         # Pulls relevant existing files into context
│   ├── llm.py                # Ollama client
│   └── orchestrator.py      # Wires the full plan → generate → review → patch → validate loop
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

## Status

Personal project, actively evolving. Feedback and issues welcome.
