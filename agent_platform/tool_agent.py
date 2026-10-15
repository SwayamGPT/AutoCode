"""Agent mode: a model works in a project folder by calling tools one step at a time, like Claude Code.

The loop is plain JSON (works with any provider, local or hosted, including models without native
tool calling): each turn the model returns either a tool call or a final answer.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .config import setting
from .llm import LLMClient
from .tools import ToolError, ToolKit, tool_catalog

AGENT_MAX_STEPS = setting("AGENT_MAX_STEPS", 30)
AGENT_TRANSCRIPT_CHARS = setting("AGENT_TRANSCRIPT_CHARS", 24_000)
ARGS_PREVIEW_CHARS = 2000

SYSTEM_PROMPT = """
You are AutoCode's coding agent. You work inside the user's project folder using tools, one tool per turn.
Every reply is one JSON object, either a tool call:
  {{"thought": "what you are doing and why", "tool": "tool_name", "args": {{...}}}}
or, when the task is done (or cannot be done), the final answer:
  {{"thought": "...", "final": "short summary of what you did and anything the user must know"}}

How to work:
- Explore first (list_files, search, read_file). Read a file before editing it.
- Prefer edit_file for small changes; copy the "old" text exactly from read_file output (without line numbers).
- After changing code, run the project's tests or a quick check with run_command when one exists.
- Only commit, push, pull or touch GitHub when the user asked for it. Use a branch for pushes when sensible.
- Tool results are facts; never invent them. If a tool fails, read the error and adjust.
- Some tools ask the user first. If the user declines, do not retry that call.

Tools:
{catalog}
"""


class ToolAgent:
    def __init__(self, llm: LLMClient, toolkit: ToolKit, emit: Callable[[str, dict[str, Any]], None]) -> None:
        self.llm = llm
        self.toolkit = toolkit
        self.emit = emit
        self.system = SYSTEM_PROMPT.format(catalog=tool_catalog())

    def prompt(self, task: str, steps: list[str]) -> str:
        """Task plus the most recent steps that fit the budget (older steps are summarised as omitted)."""
        kept: list[str] = []
        used = 0
        for entry in reversed(steps):
            if used + len(entry) > AGENT_TRANSCRIPT_CHARS:
                break
            kept.insert(0, entry)
            used += len(entry)
        omitted = len(steps) - len(kept)
        history = (f"({omitted} earlier steps omitted)\n\n" if omitted else "") + "\n\n".join(kept)
        return f"Task:\n{task}\n\nProject folder: {self.toolkit.root}\n\nSteps so far:\n{history or '(none yet)'}\n\nYour next JSON reply:"

    def run(self, task: str) -> tuple[bool, str]:
        """Returns (finished, final message)."""
        steps: list[str] = []
        for step in range(1, AGENT_MAX_STEPS + 1):
            try:
                action = self.llm.ask_json(self.system, self.prompt(task, steps), f"Agent step {step}")
            except ValueError as exc:
                steps.append(f"Step {step}: your reply was not valid JSON ({exc}). Reply with one JSON object.")
                continue
            thought = str(action.get("thought", ""))[:500]
            if "final" in action:
                final = str(action["final"])
                self.emit("agent_final", {"step": step, "message": final})
                return True, final
            tool = str(action.get("tool", ""))
            args = action.get("args") if isinstance(action.get("args"), dict) else {}
            preview = json.dumps(args)[:ARGS_PREVIEW_CHARS]
            self.emit("tool_call", {"step": step, "tool": tool, "args": preview, "thought": thought})
            try:
                result, ok = self.toolkit.call(tool, args), True
            except ToolError as exc:
                result, ok = f"Error: {exc}", False
            except (TypeError, ValueError) as exc:  # bad argument names or types from the model
                result, ok = f"Error: bad arguments for {tool}: {exc}", False
            self.emit("tool_result", {"step": step, "tool": tool, "ok": ok, "result": result})
            steps.append(f"Step {step}: {thought}\nCalled {tool} {preview}\nResult{'' if ok else ' (failed)'}:\n{result}")
        message = f"Stopped after {AGENT_MAX_STEPS} steps without finishing. Raise AGENT_MAX_STEPS or narrow the task."
        self.emit("agent_final", {"step": AGENT_MAX_STEPS, "message": message})
        return False, message
