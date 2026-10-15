from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from pathlib import Path

from .config import config_from_env, load_env_files
from .logs import setup_logging
from .settings import apply_saved_settings
from .models import PipelineConfig
from .orchestrator import Orchestrator
from .workspace import changed_files, file_diff, load_workspace, write_project


def print_files(files: dict[str, str]) -> None:
    print("\nFINAL FILES")
    print("=" * 84)
    for path, content in files.items():
        print(f"\n--- {path} ---")
        print(content)


def build_parser(env_config: PipelineConfig) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a modular agentic code pipeline. Subcommands: `desktop` (app window), `serve` (UI + A2A over HTTP), `mcp` (MCP stdio server)."
    )
    parser.add_argument("requirement", nargs="*", help="The code-generation requirement.")
    parser.add_argument(
        "--workspace",
        help="Project directory to edit. Existing files are loaded as context and results are written back.",
    )
    parser.add_argument("--dry-run", action="store_true", help="With --workspace: print diffs, do not write.")
    parser.add_argument("--model", default=env_config.model, help="Ollama model name.")
    parser.add_argument("--ollama-host", default=env_config.ollama_host, help="Ollama HTTP host.")
    parser.add_argument(
        "--ollama-request-timeout-seconds",
        type=int,
        default=env_config.ollama_request_timeout_seconds,
    )
    parser.add_argument("--temperature", type=float, default=env_config.temperature)
    parser.add_argument("--num-ctx", type=int, default=env_config.num_ctx)
    parser.add_argument("--max-iterations", type=int, default=env_config.max_iterations)
    parser.add_argument("--passing-score", type=int, default=env_config.passing_score)
    parser.add_argument("--max-retrieved-files", type=int, default=env_config.max_retrieved_files)
    parser.add_argument("--skip-verify", action=argparse.BooleanOptionalAction, default=env_config.skip_verify)
    parser.add_argument("--command-timeout-seconds", type=int, default=env_config.command_timeout_seconds)
    parser.add_argument(
        "--mcp-config", default=env_config.mcp_config, help="MCP servers file (Claude-style mcpServers JSON)."
    )
    parser.add_argument(
        "--fast",
        action=argparse.BooleanOptionalAction,
        default=env_config.fast_mode,
        help="One generation pass instead of ten per-dimension implementation passes.",
    )
    parser.add_argument(
        "--a2a-peers", default=env_config.a2a_peers, help="Comma-separated A2A agent URLs to use as extra reviewers."
    )
    return parser


def build_serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.py serve", description="Start the AutoCode web UI.")
    parser.add_argument("--host", default=os.environ.get("UI_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("UI_PORT", "8000")))
    return parser


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    # Windowed builds (no console) start with sys.stdout/sys.stderr set to None.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - lives for the process
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    load_env_files()
    setup_logging()
    # Environment and .env give the defaults; settings saved from the app override them.
    env_config = apply_saved_settings(config_from_env())

    # The packaged app (and `python app.py desktop`) opens the desktop window.
    if argv[:1] == ["desktop"] or (not argv and getattr(sys, "frozen", False)):
        from .desktop import run_desktop

        run_desktop(env_config)
        return

    if argv[:1] == ["mcp"]:
        from .mcp_server import serve_stdio

        serve_stdio(env_config)
        return

    if argv[:1] == ["serve"]:
        from .server import serve

        args = build_serve_parser().parse_args(argv[1:])
        serve(env_config, args.host, args.port)
        return

    args = build_parser(env_config).parse_args(argv)
    requirement = " ".join(args.requirement).strip()
    if not requirement:
        requirement = os.environ.get("APP_REQUIREMENT", "")
    if not requirement:
        requirement = input("What should the coding agent build? ").strip()
    if not requirement:
        raise SystemExit("No requirement given.")

    # Flags override only what they name; everything else (providers, role models, memory, learning)
    # comes from env and saved settings.
    config = dataclasses.replace(
        env_config,
        model=args.model,
        ollama_host=args.ollama_host,
        ollama_request_timeout_seconds=args.ollama_request_timeout_seconds,
        temperature=args.temperature,
        num_ctx=args.num_ctx,
        max_iterations=args.max_iterations,
        passing_score=args.passing_score,
        max_retrieved_files=args.max_retrieved_files,
        skip_verify=args.skip_verify,
        command_timeout_seconds=args.command_timeout_seconds,
        mcp_config=args.mcp_config,
        a2a_peers=args.a2a_peers,
        fast_mode=args.fast,
    )
    original = load_workspace(args.workspace)
    result = Orchestrator(config).run(requirement, original)

    if not args.workspace:
        print_files(result.files)
        return

    changes = changed_files(original, result.files)
    for path, content in changes.items():
        print(file_diff(path, original.get(path), content))
    if args.dry_run:
        print(f"\nDry run: {len(changes)} file(s) would change.")
        return
    written = write_project(Path(args.workspace).expanduser(), changes)
    print(f"\nWrote {len(written)} file(s) to {args.workspace}")


if __name__ == "__main__":
    main()
