"""Tests for issue #1587 — drop the unreachable TimeoutExpired catch in _is_merged.

Follow-up to #849/#808. ``_is_merged`` obtains its value through ``_run_gh``,
which already swallows every subprocess failure (including
``subprocess.TimeoutExpired``) and returns an empty string. A local
``except subprocess.TimeoutExpired`` in ``_is_merged`` could therefore never
fire — it is dead/defensive only. This module pins the AC contract:

  AC1  ``_is_merged`` contains no ``except subprocess.TimeoutExpired`` block.
  AC2  ``_run_gh`` is the sole handler of ``subprocess.TimeoutExpired`` and
       returns "" on timeout — the safety net stays intact.
  AC3  ``_is_merged`` still handles its remaining exception cases
       (non-integer compare output) without regression.
  AC4  No other function in the module re-introduces a redundant
       ``TimeoutExpired`` catch around a ``_run_gh`` call.
  AC5  is the existing suite (``test_808__stale_branch_scan_cleanup.py``),
       run alongside this file.
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import os
import subprocess
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))


def _load_module(name: str, path: Path):
    """Load a routers/*.py file as a submodule of a stub `routers` package."""
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

SERVICE_PATH = ROUTERS_DIR / "stale_branches_service.py"
SOURCE_TREE = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"))


def _func_node(name: str) -> ast.FunctionDef:
    for node in ast.walk(SOURCE_TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found in {SERVICE_PATH}")


def _handler_catches_timeout(handler: ast.ExceptHandler) -> bool:
    """True when an ``except`` clause names ``subprocess.TimeoutExpired``."""
    exc = handler.type
    if exc is None:  # bare except
        return False
    names = exc.elts if isinstance(exc, ast.Tuple) else [exc]
    for n in names:
        # match `subprocess.TimeoutExpired` and a bare `TimeoutExpired`
        if isinstance(n, ast.Attribute) and n.attr == "TimeoutExpired":
            return True
        if isinstance(n, ast.Name) and n.id == "TimeoutExpired":
            return True
    return False


def _function_catches_timeout(name: str) -> bool:
    node = _func_node(name)
    return any(
        _handler_catches_timeout(h)
        for sub in ast.walk(node)
        if isinstance(sub, ast.Try)
        for h in sub.handlers
    )


# ════════════════════════ AC1 — no dead catch in _is_merged ═══════════════════

def test_ac1_is_merged_has_no_timeout_expired_catch():
    """_is_merged must not carry an unreachable except subprocess.TimeoutExpired."""
    assert not _function_catches_timeout("_is_merged"), (
        "_is_merged still contains an except subprocess.TimeoutExpired block; "
        "_run_gh already swallows timeouts, so this catch is unreachable dead code."
    )


# ════════════════════════ AC2 — _run_gh is the sole timeout net ═══════════════

def test_ac2_run_gh_swallows_timeout_and_returns_empty(monkeypatch):
    """A subprocess.TimeoutExpired from the underlying call yields "" out of _run_gh."""
    def _boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="gh", timeout=1)

    monkeypatch.setattr(svc.subprocess, "run", _boom)
    assert svc._run_gh(["api", "whatever"]) == ""


def test_ac2_run_gh_keeps_explicit_timeout_guard():
    """UAT step 2: _run_gh must keep its own explicit except subprocess.TimeoutExpired."""
    assert _function_catches_timeout("_run_gh"), (
        "_run_gh must keep an explicit except subprocess.TimeoutExpired guard so the "
        "single timeout safety net is intentional and documented (UAT step 2)."
    )


# ════════════════════════ AC3 — _is_merged regression guard ═══════════════════

def test_ac3_is_merged_false_on_empty(monkeypatch):
    """Empty compare output (the timeout/failure signal from _run_gh) => not merged."""
    monkeypatch.setattr(svc, "_run_gh", lambda args: "")
    assert svc._is_merged("o/r", "feature/1-x", "develop") is False


def test_ac3_is_merged_false_on_non_integer(monkeypatch):
    """Non-integer compare output is handled (ValueError) and treated as not merged."""
    monkeypatch.setattr(svc, "_run_gh", lambda args: "not-a-number")
    assert svc._is_merged("o/r", "feature/1-x", "develop") is False


def test_ac3_is_merged_true_when_zero_ahead(monkeypatch):
    """ahead_by == 0 means fully contained in target => merged."""
    monkeypatch.setattr(svc, "_run_gh", lambda args: "0")
    assert svc._is_merged("o/r", "feature/1-x", "develop") is True


def test_ac3_is_merged_false_when_ahead(monkeypatch):
    """ahead_by > 0 means commits not in target => not merged."""
    monkeypatch.setattr(svc, "_run_gh", lambda args: "3")
    assert svc._is_merged("o/r", "feature/1-x", "develop") is False


# ════════════════ AC4 — no other redundant TimeoutExpired catch ═══════════════

def test_ac4_only_run_gh_may_catch_timeout():
    """No function other than _run_gh catches subprocess.TimeoutExpired."""
    offenders = [
        node.name
        for node in ast.walk(SOURCE_TREE)
        if isinstance(node, ast.FunctionDef)
        and node.name != "_run_gh"
        and _function_catches_timeout(node.name)
    ]
    assert offenders == [], (
        f"redundant TimeoutExpired catch found in: {offenders}; "
        "only _run_gh should guard against subprocess timeouts."
    )


def test_ac4_is_merged_calls_run_gh_not_subprocess_directly():
    """_is_merged routes through _run_gh (so the single timeout net applies)."""
    src = inspect.getsource(svc._is_merged)
    assert "_run_gh(" in src
    assert "subprocess.run(" not in src
