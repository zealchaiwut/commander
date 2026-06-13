"""The gate tool resolver must find pytest/ruff/mypy in the real venv locations
(worktester_dashboard/venv = tester/apps/dashboard/venv, or the clone-root
tester/venv), not only the legacy tester/apps/venv. Missing the binary made
every pytest gate fail with "binary not found" before running a test
(sprint-66.6 — fix-rounds exhausted on already-passing code)."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "sprint_manager"))
import sprint_manager as sm


def _mk_venv(root: Path, rel: str, tool: str) -> Path:
    p = root / rel / "venv" / "bin" / tool
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("#!/bin/sh\n")
    return p


def test_finds_tool_in_dashboard_venv(tmp_path):
    # worktester_dashboard = <clone>/apps/dashboard ; venv at apps/dashboard/venv
    wd = tmp_path / "tester" / "apps" / "dashboard"
    wd.mkdir(parents=True)
    _mk_venv(tmp_path / "tester" / "apps", "dashboard", "pytest")
    with patch.object(sm, "_try", return_value=(False, "", "")):  # not on PATH
        got = sm._find_tool_bin("pytest", wd)
    assert got and got.endswith("tester/apps/dashboard/venv/bin/pytest")


def test_finds_tool_in_clone_root_venv(tmp_path):
    wd = tmp_path / "tester" / "apps" / "dashboard"
    wd.mkdir(parents=True)
    _mk_venv(tmp_path, "tester", "ruff")  # tester/venv/bin/ruff
    with patch.object(sm, "_try", return_value=(False, "", "")):
        got = sm._find_tool_bin("ruff", wd)
    assert got and got.endswith("tester/venv/bin/ruff")


def test_prefers_path_when_present(tmp_path):
    wd = tmp_path / "tester" / "apps" / "dashboard"
    wd.mkdir(parents=True)
    with patch.object(sm, "_try", return_value=(True, "/usr/local/bin/pytest", "")):
        assert sm._find_tool_bin("pytest", wd) == "/usr/local/bin/pytest"


def test_returns_none_when_missing(tmp_path):
    wd = tmp_path / "tester" / "apps" / "dashboard"
    wd.mkdir(parents=True)
    with patch.object(sm, "_try", return_value=(False, "", "")):
        assert sm._find_tool_bin("mypy", wd) is None
