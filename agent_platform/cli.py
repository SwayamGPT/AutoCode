from __future__ import annotations

import argparse
import os

from .config import config_from_env, load_env_file
from .models import PipelineConfig
from .orchestrator import Orchestrator


def print_files(files: dict[str, str]) -> None:
    print("\nFINAL FILES")
    print("=" * 84)
    for path, content in files.items():
        print(f"\n--- {path} ---")
        print(content)


def main() -> None:
    load_env_file()
    env_config = config_from_env()

    parser = argparse.ArgumentParser(description="Run a modular agentic code pipeline.")
    parser.add_argument("requirement", nargs="*", help="The code-generation requirement.")
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
    args = parser.parse_args()

    requirement = " ".join(args.requirement).strip()
    if not requirement:
        requirement = os.environ.get("APP_REQUIREMENT", "")
    if not requirement:
        requirement = input("What should the coding agent build? ").strip()

    config = PipelineConfig(
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
    )
    files = Orchestrator(config).run(requirement)
    print_files(files)


if __name__ == "__main__":
    main()
