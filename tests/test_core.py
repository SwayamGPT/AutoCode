from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from agent_platform import config as config_module
from agent_platform.llm import extract_json, normalize_files
from agent_platform.patch_engine import PatchEngine, collect_patches
from agent_platform.server import coerce_settings
from agent_platform.validator import Validator
from agent_platform.workspace import (
    changed_files,
    file_diff,
    is_safe_relative_path,
    load_workspace,
    write_project,
)
from tests.fakes import PATCH


def test_extract_json_handles_fences_and_surrounding_text() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": {"b": 2}} done') == {"a": {"b": 2}}


def test_extract_json_rejects_non_objects() -> None:
    with pytest.raises(ValueError):
        extract_json("[1, 2]")
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_normalize_files_accepts_dict_and_list() -> None:
    assert normalize_files({"a.py": 1}) == {"a.py": "1"}
    assert normalize_files([{"path": "b.py", "content": "x"}, {"bad": 1}]) == {"b.py": "x"}
    assert normalize_files("nope") == {}


@pytest.mark.parametrize(
    ("path", "safe"),
    [("src/a.py", True), ("../evil", False), ("/etc/passwd", False), ("a/../../b", False), ("", False)],
)
def test_is_safe_relative_path(path: str, safe: bool) -> None:
    assert is_safe_relative_path(path) is safe


def test_write_project_skips_unsafe_paths(tmp_path: Path) -> None:
    written = write_project(tmp_path / "out", {"ok/a.py": "x", "../escape.py": "y"})
    assert written == ["ok/a.py"]
    assert not (tmp_path / "escape.py").exists()


def test_load_workspace_skips_ignored_and_binary(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("print(1)\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("x")
    (tmp_path / "img.bin").write_bytes(b"\xff\xfe\x00")
    assert load_workspace(str(tmp_path)) == {"src/a.py": "print(1)\n"}
    assert load_workspace(str(tmp_path / "missing")) == {}
    assert load_workspace("") == {}


def test_changed_files_and_diff() -> None:
    original = {"a.py": "1\n", "b.py": "same\n"}
    final = {"a.py": "2\n", "b.py": "same\n", "c.py": "new\n"}
    assert changed_files(original, final) == {"a.py": "2\n", "c.py": "new\n"}
    assert "-1\n+2" in file_diff("a.py", "1\n", "2\n")
    assert file_diff("c.py", None, "new\n").startswith("--- /dev/null")


def test_collect_patches_drops_incomplete_entries() -> None:
    reviews = [{"patches": [{"file": "a.py", "unified_diff": "d"}, {"file": "b.py"}, "junk"]}, {}]
    assert collect_patches(reviews) == [{"file": "a.py", "unified_diff": "d"}]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_patch_engine_applies_valid_and_reports_invalid() -> None:
    files = {"app.py": "def add(a, b):\n    return a + b\n"}
    bad = {"file": "app.py", "unified_diff": "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-nope\n+x\n"}
    unsafe = {"file": "../x.py", "unified_diff": PATCH}
    result = PatchEngine(timeout_seconds=10).apply(files, [{"file": "app.py", "unified_diff": PATCH}, bad, unsafe])
    assert '"""Add two numbers."""' in result.files["app.py"]
    assert len(result.applied) == 1
    assert [patch["file"] for patch in result.failed] == ["app.py", "../x.py"]


def test_validator_runs_plan_command_and_falls_back_to_compile() -> None:
    validator = Validator(timeout_seconds=30)
    assert validator.run({"test_command": "exit 3"}, {"a.txt": "x"})["exit_code"] == 3
    assert validator.run({}, {"a.py": "def broken(:\n"})["exit_code"] != 0
    assert validator.run({}, {"a.py": "x = 1\n"})["exit_code"] == 0


def test_validator_times_out() -> None:
    result = Validator(timeout_seconds=1).run({"test_command": "sleep 5"}, {})
    assert result["exit_code"] == 124


def test_config_uses_defaults_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in config_module.DEFAULTS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAX_ITERATIONS", "4")
    monkeypatch.setenv("SKIP_VERIFY", "yes")
    config = config_module.config_from_env()
    assert config.max_iterations == 4
    assert config.skip_verify is True
    assert config.model == config_module.DEFAULTS["OLLAMA_MODEL"]
    monkeypatch.setenv("PASSING_SCORE", "high")
    with pytest.raises(RuntimeError, match="PASSING_SCORE"):
        config_module.config_from_env()


def test_coerce_settings_validates_types() -> None:
    base = config_module.config_from_env()
    updated = coerce_settings(base, {"max_iterations": "3", "skip_verify": True, "model": ""})
    assert updated.max_iterations == 3 and updated.skip_verify is True and updated.model == base.model
    with pytest.raises(ValueError):
        coerce_settings(base, {"skip_verify": "true"})
    with pytest.raises(ValueError):
        coerce_settings(base, {"max_iterations": 0})
    with pytest.raises(ValueError):
        coerce_settings(base, {"passing_score": "abc"})


def test_extract_json_roundtrip_large() -> None:
    payload = {"files": {f"f{i}.py": "x" * 100 for i in range(50)}}
    assert extract_json(json.dumps(payload)) == payload
