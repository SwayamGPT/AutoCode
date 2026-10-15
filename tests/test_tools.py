from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from agent_platform import tools as tools_module
from agent_platform.tool_agent import ToolAgent
from agent_platform.tools import ToolError, ToolKit, github_slug


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "user.email", "t@example.com")
    git(work, "config", "user.name", "Test")
    (work / "app.py").write_text("def add(a, b):\n    return a + b\n")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "init")
    git(work, "remote", "add", "origin", str(remote))
    git(work, "push", "-q", "-u", "origin", "main")
    return work


def kit(root: Path, approve: bool = True, mode: str = "ask") -> tuple[ToolKit, list[str]]:
    asked: list[str] = []

    def approver(tool: str, args: dict[str, Any], summary: str) -> bool:
        asked.append(summary)
        return approve

    return ToolKit(str(root), approver, mode, 30), asked


def test_file_tools_and_path_safety(repo: Path) -> None:
    toolkit, asked = kit(repo)
    assert "app.py" in toolkit.call("list_files", {})
    assert "    1  def add(a, b):" in toolkit.call("read_file", {"path": "app.py"})
    assert toolkit.call("search", {"pattern": r"return a \+ b"}) == "app.py:2: return a + b"
    toolkit.call("edit_file", {"path": "app.py", "old": "return a + b", "new": "return a + b  # sum"})
    assert "# sum" in (repo / "app.py").read_text()
    toolkit.call("write_file", {"path": "pkg/new.py", "content": "x = 1\n"})
    assert (repo / "pkg/new.py").read_text() == "x = 1\n"
    with pytest.raises(ToolError, match="outside"):
        toolkit.call("read_file", {"path": "../secret"})
    with pytest.raises(ToolError, match="git tools"):
        toolkit.call("write_file", {"path": ".git/config", "content": "x"})
    with pytest.raises(ToolError, match="exactly once"):
        toolkit.call("edit_file", {"path": "app.py", "old": "nope", "new": "x"})
    assert asked == []  # reads and edits inside the project never ask


def test_git_commit_branch_push_and_pull(repo: Path, tmp_path: Path) -> None:
    toolkit, asked = kit(repo)
    toolkit.call("git_branch", {"name": "feature/x", "create": True})
    toolkit.call("write_file", {"path": "b.py", "content": "y = 2\n"})
    assert "?? b.py" in toolkit.call("git_status", {})
    toolkit.call("git_commit", {"message": "add b"})
    toolkit.call("git_push", {})
    assert "feature/x" in git(tmp_path / "remote.git", "branch")
    assert asked == ["Commit: add b", "Git push"]

    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", "-b", "feature/x", str(tmp_path / "remote.git"), str(other))
    git(other, "config", "user.email", "t@example.com")
    git(other, "config", "user.name", "Test")
    (other / "c.py").write_text("z = 3\n")
    git(other, "add", "-A")
    git(other, "commit", "-qm", "add c")
    git(other, "push", "-q")
    toolkit.call("git_pull", {})
    assert (repo / "c.py").exists()
    assert "add c" in toolkit.call("git_log", {"count": 3})


def test_approval_modes(repo: Path) -> None:
    declined, _ = kit(repo, approve=False)
    with pytest.raises(ToolError, match="declined"):
        declined.call("run_command", {"command": "echo hi"})
    read_only, _ = kit(repo, mode="read_only")
    with pytest.raises(ToolError, match="read_only"):
        read_only.call("write_file", {"path": "x", "content": "y"})
    auto, asked = kit(repo, approve=False, mode="auto")
    assert auto.call("run_command", {"command": "echo hi"}) == "hi"
    assert asked == []
    assert "Command failed" in auto.call("run_command", {"command": "exit 3"})
    with pytest.raises(ToolError, match="Invalid branch"):
        auto.call("git_branch", {"name": "--force"})


class FakeGitHub(BaseHTTPRequestHandler):
    calls: list[tuple[str, str, Any]] = []

    def log_message(self, *args: Any) -> None:
        pass

    def reply(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        FakeGitHub.calls.append(("GET", self.path, self.headers.get("Authorization")))
        if self.path == "/repos/acme/demo":
            return self.reply({"full_name": "acme/demo", "default_branch": "main", "description": "Demo"})
        if self.path.startswith("/repos/acme/demo/issues?"):
            return self.reply([{"number": 3, "title": "Bug", "state": "open"}, {"number": 4, "title": "PR", "state": "open", "pull_request": {}}])
        self.reply({"message": "Not Found"}, 404)

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeGitHub.calls.append(("POST", self.path, body))
        self.reply({"number": 9, "html_url": "https://github.com/acme/demo/pull/9"}, 201)


@pytest.fixture()
def github(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    FakeGitHub.calls = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(tools_module, "GITHUB_API_URL", f"http://127.0.0.1:{httpd.server_address[1]}")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    yield
    httpd.shutdown()


def test_github_tools(repo: Path, github: None) -> None:
    git(repo, "remote", "set-url", "origin", "https://github.com/acme/demo.git")
    toolkit, asked = kit(repo)
    assert toolkit.call("github_repo", {}) == "acme/demo (default branch main): Demo"
    assert toolkit.call("github_list_issues", {}) == "#3 Bug (open)"
    result = toolkit.call("github_create_pr", {"title": "Add b", "body": "Adds b"})
    assert result == "Opened pull request #9: https://github.com/acme/demo/pull/9"
    assert ("POST", "/repos/acme/demo/pulls", {"title": "Add b", "body": "Adds b", "head": "main", "base": "main"}) in FakeGitHub.calls
    assert FakeGitHub.calls[0][2] == "Bearer ghp_test"
    assert asked == ["Open pull request: Add b"]


def test_github_slug() -> None:
    assert github_slug("https://github.com/acme/demo.git") == "acme/demo"
    assert github_slug("git@github.com:acme/demo.git") == "acme/demo"
    assert github_slug("https://gitlab.com/acme/demo") is None


class ScriptedLLM:
    def __init__(self, actions: list[dict[str, Any]]) -> None:
        self.actions = actions
        self.prompts: list[str] = []

    def ask_json(self, system: str, user: str, label: str, temperature: float | None = None) -> dict[str, Any]:
        self.prompts.append(user)
        return self.actions.pop(0)


def test_agent_loop_uses_tools_and_finishes(repo: Path) -> None:
    llm = ScriptedLLM([
        {"thought": "look", "tool": "read_file", "args": {"path": "app.py"}},
        {"thought": "fix", "tool": "edit_file", "args": {"path": "app.py", "old": "a + b", "new": "a - b"}},
        {"thought": "oops", "tool": "edit_file", "args": {"path": "app.py", "old": "missing", "new": "x"}},
        {"thought": "test", "tool": "run_command", "args": {"command": "python3 -c 'import app; print(app.add(3, 1))'"}},
        {"thought": "done", "final": "Changed add to subtract; check prints 2."},
    ])
    events: list[tuple[str, dict]] = []
    toolkit, asked = kit(repo)
    finished, message = ToolAgent(llm, toolkit, lambda k, d: events.append((k, d))).run("make add subtract")
    assert finished and message.startswith("Changed add")
    results = [d for k, d in events if k == "tool_result"]
    assert [r["ok"] for r in results] == [True, True, False, True]
    assert results[-1]["result"] == "2"
    assert "Error: 'old' must match exactly once" in llm.prompts[3]  # the failure is fed back to the model
    assert asked == ["Run: python3 -c 'import app; print(app.add(3, 1))'"]
