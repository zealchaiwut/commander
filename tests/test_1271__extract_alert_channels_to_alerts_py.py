"""Tests for issue #1271: extract sprint_manager alert channels to alerts.py.

AC1: services/sprint_manager/alerts.py exists and contains HangDetector,
     dispatch_alerts, _alert_dashboard_banner, _alert_email, _alert_discord,
     _alert_ntfy, _alert_file
AC2: All moved symbols are importable from alerts.py with no circular imports
AC3: Original module re-exports or updates imports so call sites are unaffected
AC4: python -m py_compile services/sprint_manager/alerts.py exits 0
AC5: python -m py_compile on the original sprint_manager module exits 0
AC6: No alert dispatch logic added, removed, or altered — move only
AC7: Existing tests pass without modification
"""
from __future__ import annotations

import ast
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

SM_DIR = Path(__file__).parent.parent / "services" / "sprint_manager"
ALERTS_PY = SM_DIR / "alerts.py"
SM_PY = SM_DIR / "sprint_manager.py"

EXPECTED_SYMBOLS = [
    "HangDetector",
    "dispatch_alerts",
    "_alert_dashboard_banner",
    "_alert_email",
    "_alert_discord",
    "_alert_ntfy",
    "_alert_file",
]


# ── AC1: alerts.py exists with the right symbols ─────────────────────────────

def test_alerts_py_exists():
    """AC1: services/sprint_manager/alerts.py must exist."""
    assert ALERTS_PY.exists(), f"Expected {ALERTS_PY} to exist"


@pytest.mark.parametrize("symbol", EXPECTED_SYMBOLS)
def test_alerts_py_defines_symbol(symbol):
    """AC1: alerts.py must define each required symbol."""
    import services.sprint_manager.alerts as alerts_mod
    assert hasattr(alerts_mod, symbol), (
        f"services.sprint_manager.alerts missing '{symbol}'"
    )


# ── AC2: importable from alerts.py, no circular imports ──────────────────────

def test_alerts_py_importable_no_circular():
    """AC2: import services.sprint_manager.alerts succeeds without circular import error."""
    # Force a fresh import to surface any circular dependency
    mod_name = "services.sprint_manager.alerts"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    try:
        importlib.import_module(mod_name)
    except ImportError as exc:
        pytest.fail(f"Circular or missing import in alerts.py: {exc}")


@pytest.mark.parametrize("symbol", EXPECTED_SYMBOLS)
def test_symbol_importable_from_alerts(symbol):
    """AC2: each symbol can be imported directly from services.sprint_manager.alerts."""
    import services.sprint_manager.alerts as alerts_mod
    obj = getattr(alerts_mod, symbol, None)
    assert obj is not None, (
        f"Cannot import '{symbol}' from services.sprint_manager.alerts"
    )


# ── AC3: original module still exposes the symbols (re-export / import) ──────

def _get_sm_ast():
    source = SM_PY.read_text(encoding="utf-8")
    return ast.parse(source)


def test_sprint_manager_imports_from_alerts():
    """AC3: sprint_manager.py must import from services.sprint_manager.alerts."""
    source = SM_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports_from_alerts = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if "alerts" in module and "sprint_manager" in module:
                imports_from_alerts = True
                break
    assert imports_from_alerts, (
        "sprint_manager.py does not import from services.sprint_manager.alerts"
    )


@pytest.mark.parametrize("symbol", EXPECTED_SYMBOLS)
def test_sprint_manager_still_has_symbol_in_scope(symbol):
    """AC3: symbols are still accessible from the sprint_manager module namespace."""
    import sprint_manager as sm  # uses conftest sys.path (services/sprint_manager/)
    assert hasattr(sm, symbol), (
        f"sprint_manager module no longer exposes '{symbol}' — call sites would break"
    )


# ── AC4: py_compile on alerts.py exits 0 ─────────────────────────────────────

def test_py_compile_alerts_py():
    """AC4: python -m py_compile services/sprint_manager/alerts.py exits 0."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(ALERTS_PY)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"py_compile failed on alerts.py:\n{result.stderr}"
    )


# ── AC5: py_compile on sprint_manager.py exits 0 ─────────────────────────────

def test_py_compile_sprint_manager_py():
    """AC5: python -m py_compile services/sprint_manager/sprint_manager.py exits 0."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(SM_PY)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"py_compile failed on sprint_manager.py:\n{result.stderr}"
    )


# ── AC6: symbols defined only in alerts.py, not inline in sprint_manager.py ──

def test_symbol_definitions_only_in_alerts_py():
    """AC6: each symbol is defined in alerts.py and NOT inline in sprint_manager.py."""
    sm_tree = _get_sm_ast()
    sm_defined_funcs = {
        node.name
        for node in ast.walk(sm_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    sm_defined_classes = {
        node.name
        for node in ast.walk(sm_tree)
        if isinstance(node, ast.ClassDef)
    }
    sm_defined = sm_defined_funcs | sm_defined_classes

    alerts_source = ALERTS_PY.read_text(encoding="utf-8")
    alerts_tree = ast.parse(alerts_source)
    alerts_defined_funcs = {
        node.name
        for node in ast.walk(alerts_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    alerts_defined_classes = {
        node.name
        for node in ast.walk(alerts_tree)
        if isinstance(node, ast.ClassDef)
    }
    alerts_defined = alerts_defined_funcs | alerts_defined_classes

    for symbol in EXPECTED_SYMBOLS:
        assert symbol not in sm_defined, (
            f"'{symbol}' still defined inline in sprint_manager.py"
        )
        assert symbol in alerts_defined, (
            f"'{symbol}' not found in alerts.py"
        )


# ── AC6: dispatch_alerts behavior unchanged ───────────────────────────────────

def test_dispatch_alerts_skips_none_mode():
    """AC6: dispatch_alerts with mode='none' does not call any channel (behavior unchanged)."""
    from services.sprint_manager.alerts import dispatch_alerts
    # Should complete without error and not attempt any network/file I/O
    dispatch_alerts(["none"], title="test", body="test body")


def test_hang_detector_is_dataclass():
    """AC6: HangDetector is still a dataclass with the expected fields."""
    from services.sprint_manager.alerts import HangDetector
    import dataclasses
    assert dataclasses.is_dataclass(HangDetector)
    field_names = {f.name for f in dataclasses.fields(HangDetector)}
    assert "issue_num" in field_names
    assert "log_path" in field_names
    assert "proc" in field_names
    assert "max_total_secs" in field_names
