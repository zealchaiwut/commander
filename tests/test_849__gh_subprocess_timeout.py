"""Tests for issue #849 — timeout on gh subprocess calls in stale-branch helpers.

AC coverage (one test per AC item):
  AC1  _run_gh passes timeout=30 to subprocess.run and catches TimeoutExpired,
       returning "" on timeout.
  AC2  _is_merged catches TimeoutExpired independently, returning False (not-merged).
  AC3  _delete_branch passes timeout=30 and catches TimeoutExpired, returning False.
  AC4  A TimeoutExpired in one branch's check does not abort the entire scan/cleanup.
  AC5  Existing except Exception handlers are preserved alongside TimeoutExpired catches.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))


def _load_module(name: str, path: Path):
    if "routers" not in sys.modules:
        stub = types.ModuleType("routers")
        stub.__path__ = [str(ROUTERS_DIR)]  # type: ignore[attr-defined]
        sys.modules["routers"] = stub
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_load_module("routers.sprint_history_service", ROUTERS_DIR / "sprint_history_service.py")
svc = _load_module("routers.stale_branches_service", ROUTERS_DIR / "stale_branches_service.py")


# ═══════════════════════ AC1 — _run_gh timeout ═══════════════════════════════

def test_ac1_run_gh_returns_empty_string_on_timeout(monkeypatch):
    """_run_gh catches TimeoutExpired and returns '' so callers see a safe default."""
    def _raise(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["gh"], timeout=30)

    monkeypatch.setattr(subprocess, "run", _raise)
    assert svc._run_gh(["api", "repos/o/r/branches"]) == ""


def test_ac1_run_gh_passes_timeout_30_to_subprocess(monkeypatch):
    """_run_gh passes timeout=30 as a keyword argument to subprocess.run."""
    captured: dict = {}

    def _fake_run(*args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        result = MagicMock()
        result.returncode = 0
        result.stdout = "ok"
        return result

    monkeypatch.setattr(subprocess, "run", _fake_run)
    svc._run_gh(["api", "test"])
    assert captured.get("timeout") == 30


# ═══════════════════════ AC2 — _is_merged timeout ════════════════════════════

def test_ac2_is_merged_returns_false_on_timeout(monkeypatch):
    """_is_merged catches TimeoutExpired and returns False (not-merged safe default)."""
    def _raise_timeout(args):
        raise subprocess.TimeoutExpired(cmd=["gh"], timeout=30)

    monkeypatch.setattr(svc, "_run_gh", _raise_timeout)
    assert svc._is_merged("o/r", "feature/42-old", "develop") is False


# ═══════════════════════ AC3 — _delete_branch timeout ════════════════════════

def test_ac3_delete_branch_returns_false_on_timeout(monkeypatch):
    """_delete_branch catches TimeoutExpired and returns False (delete-failed default)."""
    def _raise(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["gh"], timeout=30)

    monkeypatch.setattr(subprocess, "run", _raise)
    assert svc._delete_branch("o/r", "feature/42-old") is False


def test_ac3_delete_branch_passes_timeout_30_to_subprocess(monkeypatch):
    """_delete_branch passes timeout=30 as a keyword argument to subprocess.run."""
    captured: dict = {}

    def _fake_run(*args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        result = MagicMock()
        result.returncode = 0
        return result

    monkeypatch.setattr(subprocess, "run", _fake_run)
    svc._delete_branch("o/r", "feature/42-old")
    assert captured.get("timeout") == 30


# ═══════════════════════ AC4 — scan/cleanup not aborted by timeout ════════════

def test_ac4_timeout_in_one_branch_does_not_abort_scan(monkeypatch):
    """If _run_gh times out mid-scan, the other branches are still processed."""
    branches = ["feature/1-alpha", "feature/2-beta", "feature/3-gamma"]
    monkeypatch.setattr(svc, "_list_remote_feature_branches", lambda repo: list(branches))
    monkeypatch.setattr(svc, "_ticket_sprint_map", lambda: {})

    call_count = [0]

    def _fake_run_gh(args):
        call_count[0] += 1
        if any("feature/2-beta" in a for a in args):
            raise subprocess.TimeoutExpired(cmd=["gh"], timeout=30)
        return "0\n"

    monkeypatch.setattr(svc, "_run_gh", _fake_run_gh)

    result = svc.scan_stale_branches("o/r")
    branch_names = [b["branch"] for b in result["branches"]]
    assert "feature/1-alpha" in branch_names, "branch before timeout must appear in results"
    assert "feature/2-beta" in branch_names, "timed-out branch must still appear (merged=False)"
    assert "feature/3-gamma" in branch_names, "branch after timeout must appear in results"


def test_ac4_timeout_in_delete_does_not_abort_cleanup(monkeypatch):
    """If subprocess.run times out inside _delete_branch, cleanup continues for other branches."""
    branches = ["feature/1-alpha", "feature/2-beta", "feature/3-gamma"]
    monkeypatch.setattr(svc, "_is_merged", lambda repo, branch, target: True)

    def _fake_run(*args, **kwargs):
        cmd = args[0] if args else []
        if any("feature/2-beta" in str(a) for a in cmd):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)
        result = MagicMock()
        result.returncode = 0
        return result

    monkeypatch.setattr(subprocess, "run", _fake_run)

    result = svc.cleanup_stale_branches("o/r", branches, confirm=True)
    assert "feature/1-alpha" in result["deleted"]
    assert "feature/3-gamma" in result["deleted"]
    assert "feature/2-beta" in result["failed"]


# ═══════════════════════ AC5 — existing Exception handlers preserved ══════════

def test_ac5_run_gh_generic_exception_still_returns_empty_string(monkeypatch):
    """The existing except Exception handler in _run_gh is preserved (not replaced)."""
    def _raise(*args, **kwargs):
        raise RuntimeError("unexpected network error")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert svc._run_gh(["api", "test"]) == ""


def test_ac5_delete_branch_generic_exception_still_returns_false(monkeypatch):
    """The existing except Exception handler in _delete_branch is preserved."""
    def _raise(*args, **kwargs):
        raise OSError("gh not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert svc._delete_branch("o/r", "feature/42-old") is False
