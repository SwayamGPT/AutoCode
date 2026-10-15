"""Tools for the agent mode: files, shell, git and GitHub, confined to one project folder.

Read-only tools always run. Tools that change things outside the editor's view (shell commands, commits,
pushes, pulls, GitHub writes) go through an approver, so the user stays in control the way they do with
Claude Code. AGENT_APPROVAL: "ask" (default) asks for those, "auto" allows everything, "read_only" blocks
every tool that changes something.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import setting
from .workspace import IGNORED_DIRS

TOOL_OUTPUT_CHARS = setting("TOOL_OUTPUT_CHARS", 8000)
MAX_READ_LINES = setting("TOOL_MAX_READ_LINES", 400)
MAX_SEARCH_HITS = setting("TOOL_MAX_SEARCH_HITS", 100)
GITHUB_API_URL = setting("GITHUB_API_URL", "https://api.github.com")

READ, WRITE, RISKY = "read", "write", "risky"
Approver = Callable[[str, dict[str, Any], str], bool]  # (tool, args, summary) -> approved


class ToolError(Exception):
    pass


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: dict[str, str]
    level: str  # read | write | risky


TOOLS: list[Tool] = [
    Tool("list_files", "List project files, optionally filtered by a glob like 'src/**/*.py'.", {"pattern": "glob (optional)"}, READ),
    Tool("read_file", "Read a file with line numbers.", {"path": "relative path", "start": "first line (optional)", "end": "last line (optional)"}, READ),
    Tool("search", "Search file contents with a regular expression.", {"pattern": "regex", "glob": "file glob (optional)"}, READ),
    Tool("write_file", "Create or overwrite a file with the full content.", {"path": "relative path", "content": "full file content"}, WRITE),
    Tool("edit_file", "Replace one exact, unique snippet in a file.", {"path": "relative path", "old": "exact text to replace", "new": "replacement"}, WRITE),
    Tool("delete_file", "Delete a file.", {"path": "relative path"}, RISKY),
    Tool("run_command", "Run a shell command in the project folder (tests, builds, linters).", {"command": "shell command"}, RISKY),
    Tool("git_status", "Show git status and the current branch.", {}, READ),
    Tool("git_diff", "Show the diff of uncommitted changes.", {"staged": "true for staged changes (optional)"}, READ),
    Tool("git_log", "Show recent commits.", {"count": "number of commits (optional)"}, READ),
    Tool("git_branch", "Create and switch to a branch, or switch to an existing one.", {"name": "branch name", "create": "true to create"}, WRITE),
    Tool("git_commit", "Stage files (default: all changes) and commit.", {"message": "commit message", "paths": "list of paths (optional)"}, RISKY),
    Tool("git_pull", "Pull from the remote (fast-forward only).", {}, RISKY),
    Tool("git_push", "Push the current branch to origin (sets upstream).", {}, RISKY),
    Tool("github_repo", "Show the GitHub repository for this project's origin remote.", {}, READ),
    Tool("github_list_issues", "List open issues.", {"state": "open|closed|all (optional)"}, READ),
    Tool("github_get_issue", "Read an issue or pull request with its comments.", {"number": "issue number"}, READ),
    Tool("github_list_prs", "List pull requests.", {"state": "open|closed|all (optional)"}, READ),
    Tool("github_create_pr", "Open a pull request from the current branch.", {"title": "title", "body": "description", "base": "base branch (optional)"}, RISKY),
    Tool("github_comment", "Comment on an issue or pull request.", {"number": "issue or PR number", "body": "comment"}, RISKY),
]
TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


def clip(text: str) -> str:
    return text if len(text) <= TOOL_OUTPUT_CHARS else text[:TOOL_OUTPUT_CHARS] + f"\n... ({len(text) - TOOL_OUTPUT_CHARS} more characters)"


def as_bool(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes"}


def github_slug(remote_url: str) -> str | None:
    """owner/repo from https://github.com/owner/repo(.git) or git@github.com:owner/repo(.git)."""
    match = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", remote_url.strip())
    return f"{match.group(1)}/{match.group(2)}" if match else None


class ToolKit:
    def __init__(
        self,
        root: str,
        approver: Approver,
        mode: str = "ask",
        timeout_seconds: int = 120,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ToolError(f"Project folder does not exist: {self.root}")
        self.approver = approver
        self.mode = mode
        self.timeout_seconds = timeout_seconds
        self.on_change = on_change or (lambda: None)

    # --- dispatch ---------------------------------------------------------------------------------
    def call(self, name: str, args: dict[str, Any]) -> str:
        tool = TOOLS_BY_NAME.get(name)
        if tool is None:
            raise ToolError(f"Unknown tool {name!r}. Available: {', '.join(TOOLS_BY_NAME)}")
        if tool.level != READ and self.mode == "read_only":
            raise ToolError(f"{name} is blocked: AGENT_APPROVAL=read_only")
        if tool.level == RISKY and self.mode != "auto":
            if not self.approver(name, args, self.describe(name, args)):
                raise ToolError(f"The user declined {name}. Do not retry it; continue or explain.")
        result = getattr(self, name)(**{k: v for k, v in args.items() if k in tool.params})
        if tool.level != READ:
            self.on_change()
        return clip(result)

    @staticmethod
    def describe(name: str, args: dict[str, Any]) -> str:
        if name == "run_command":
            return f"Run: {args.get('command', '')}"
        if name == "git_commit":
            return f"Commit: {args.get('message', '')}"
        if name == "github_create_pr":
            return f"Open pull request: {args.get('title', '')}"
        if name == "github_comment":
            return f"Comment on #{args.get('number')}"
        if name == "delete_file":
            return f"Delete {args.get('path')}"
        return name.replace("_", " ").capitalize()

    # --- files ------------------------------------------------------------------------------------
    def resolve(self, path: str) -> Path:
        target = (self.root / str(path)).resolve()
        if target != self.root and not target.is_relative_to(self.root):
            raise ToolError(f"{path} is outside the project folder")
        if ".git" in target.relative_to(self.root).parts:
            raise ToolError("Use the git tools instead of editing .git")
        return target

    def project_files(self) -> list[str]:
        files = []
        for path in self.root.rglob("*"):
            relative = path.relative_to(self.root)
            if path.is_file() and not any(part in IGNORED_DIRS for part in relative.parts):
                files.append(relative.as_posix())
        return sorted(files)

    def list_files(self, pattern: str = "") -> str:
        files = [f for f in self.project_files() if not pattern or fnmatch.fnmatch(f, pattern)]
        return "\n".join(files) or "No files."

    def read_file(self, path: str, start: Any = None, end: Any = None) -> str:
        target = self.resolve(path)
        if not target.is_file():
            raise ToolError(f"No such file: {path}")
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        first = max(int(start or 1), 1)
        last = min(int(end or first + MAX_READ_LINES - 1), len(lines))
        body = "\n".join(f"{n:>5}  {lines[n - 1]}" for n in range(first, last + 1))
        more = f"\n... {len(lines) - last} more lines (read with start={last + 1})" if last < len(lines) else ""
        return body + more

    def search(self, pattern: str, glob: str = "") -> str:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"Invalid regex: {exc}") from exc
        hits = []
        for relative in self.project_files():
            if glob and not fnmatch.fnmatch(relative, glob):
                continue
            try:
                text = (self.root / relative).read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    hits.append(f"{relative}:{number}: {line.strip()[:200]}")
                    if len(hits) >= MAX_SEARCH_HITS:
                        return "\n".join(hits) + "\n... more matches not shown"
        return "\n".join(hits) or "No matches."

    def write_file(self, path: str, content: str) -> str:
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        target.write_text(str(content), encoding="utf-8")
        return f"{'Updated' if existed else 'Created'} {path} ({len(str(content).splitlines())} lines)."

    def edit_file(self, path: str, old: str, new: str) -> str:
        target = self.resolve(path)
        if not target.is_file():
            raise ToolError(f"No such file: {path}")
        text = target.read_text(encoding="utf-8")
        count = text.count(old)
        if count != 1:
            raise ToolError(f"'old' must match exactly once in {path}; it matched {count} times. Read the file and copy the text exactly.")
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        return f"Edited {path}."

    def delete_file(self, path: str) -> str:
        target = self.resolve(path)
        if not target.is_file():
            raise ToolError(f"No such file: {path}")
        target.unlink()
        return f"Deleted {path}."

    # --- shell and git ----------------------------------------------------------------------------
    def shell(self, args: list[str] | str, check: bool = True) -> str:
        try:
            completed = subprocess.run(
                args, cwd=self.root, shell=isinstance(args, str), text=True, capture_output=True, timeout=self.timeout_seconds
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolError(f"Timed out after {self.timeout_seconds}s") from exc
        output = (completed.stdout + completed.stderr).strip()
        if check and completed.returncode != 0:
            raise ToolError(f"exit {completed.returncode}: {output[-2000:]}")
        return output or "(no output)"

    def run_command(self, command: str) -> str:
        try:
            return self.shell(str(command))
        except ToolError as exc:
            return f"Command failed: {exc}"  # a failing test run is information, not an agent error

    def git(self, *args: str) -> str:
        return self.shell(["git", *args])

    def git_status(self) -> str:
        return self.git("status", "--short", "--branch")

    def git_diff(self, staged: Any = False) -> str:
        return self.git("diff", "--staged") if as_bool(staged) else self.git("diff")

    def git_log(self, count: Any = 10) -> str:
        return self.git("log", "--oneline", "--decorate", f"-n{min(int(count or 10), 50)}")

    def git_branch(self, name: str, create: Any = False) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", str(name)) or str(name).startswith("-"):
            raise ToolError(f"Invalid branch name: {name}")
        return self.git("switch", "-c", name) if as_bool(create) else self.git("switch", name)

    def git_commit(self, message: str, paths: Any = None) -> str:
        if isinstance(paths, list) and paths:
            self.git("add", "--", *[str(self.resolve(p).relative_to(self.root)) for p in paths])
        else:
            self.git("add", "-A")
        return self.git("commit", "-m", str(message))

    def git_pull(self) -> str:
        return self.git("pull", "--ff-only")

    def git_push(self) -> str:
        branch = self.git("rev-parse", "--abbrev-ref", "HEAD").strip()
        return self.git("push", "--set-upstream", "origin", branch)

    # --- GitHub -----------------------------------------------------------------------------------
    def repo_slug(self) -> str:
        slug = github_slug(self.shell(["git", "remote", "get-url", "origin"]))
        if not slug:
            raise ToolError("The origin remote is not a GitHub repository")
        return slug

    def github(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            raise ToolError("Set GITHUB_TOKEN (a GitHub personal access token) to use GitHub tools")
        request = urllib.request.Request(
            GITHUB_API_URL.rstrip("/") + path,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            raise ToolError(f"GitHub returned {exc.code}: {exc.read().decode('utf-8', 'replace')[:500]}") from exc
        except urllib.error.URLError as exc:
            raise ToolError(f"GitHub unreachable: {exc.reason}") from exc

    def github_repo(self) -> str:
        repo = self.github("GET", f"/repos/{self.repo_slug()}")
        return f"{repo['full_name']} (default branch {repo['default_branch']}): {repo.get('description') or ''}"

    def github_list_issues(self, state: str = "open") -> str:
        items = self.github("GET", f"/repos/{self.repo_slug()}/issues?state={state or 'open'}&per_page=30")
        issues = [i for i in items if "pull_request" not in i]
        return "\n".join(f"#{i['number']} {i['title']} ({i['state']})" for i in issues) or "No issues."

    def github_get_issue(self, number: Any) -> str:
        slug = self.repo_slug()
        issue = self.github("GET", f"/repos/{slug}/issues/{int(number)}")
        comments = self.github("GET", f"/repos/{slug}/issues/{int(number)}/comments")
        lines = [f"#{issue['number']} {issue['title']} ({issue['state']})", issue.get("body") or ""]
        lines += [f"--- {c['user']['login']}: {c['body']}" for c in comments]
        return "\n".join(lines)

    def github_list_prs(self, state: str = "open") -> str:
        prs = self.github("GET", f"/repos/{self.repo_slug()}/pulls?state={state or 'open'}&per_page=30")
        return "\n".join(f"#{p['number']} {p['title']} ({p['head']['ref']} -> {p['base']['ref']})" for p in prs) or "No pull requests."

    def github_create_pr(self, title: str, body: str = "", base: str = "") -> str:
        slug = self.repo_slug()
        head = self.git("rev-parse", "--abbrev-ref", "HEAD").strip()
        base = base or self.github("GET", f"/repos/{slug}")["default_branch"]
        pr = self.github("POST", f"/repos/{slug}/pulls", {"title": title, "body": body, "head": head, "base": base})
        return f"Opened pull request #{pr['number']}: {pr['html_url']}"

    def github_comment(self, number: Any, body: str) -> str:
        comment = self.github("POST", f"/repos/{self.repo_slug()}/issues/{int(number)}/comments", {"body": body})
        return f"Commented: {comment['html_url']}"


def tool_catalog() -> str:
    return "\n".join(
        f"- {t.name}({', '.join(f'{k}: {v}' for k, v in t.params.items())}): {t.description}" for t in TOOLS
    )
