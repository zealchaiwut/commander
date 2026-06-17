"""Tests for issue #1270: extract sprint_manager path helpers to paths.py.

AC1: services/sprint_manager/paths.py exists and contains all extracted helpers
     (_pid_file_path, _plan_json_path, _state_path, _summary_path,
     _sprint_number, _label_base, and any other pure path/constant ops)
AC2: Original sprint_manager imports helpers from paths.py; no inline defs
AC3: All path resolutions return identical values (no behavioral change)
AC4: python -m py_compile services/sprint_manager/paths.py exits 0
AC5: python -m py_compile on the original sprint_manager module exits 0
AC6: Definitions appear only in paths.py; sprint_manager.py no inline redefs
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SM_DIR = Path(__file__).parent.parent / "services" / "sprint_manager"
PATHS_PY = SM_DIR / "paths.py"
SM_PY = SM_DIR / "sprint_manager.py"

EXPECTED_SYMBOLS = [
    "_pid_file_path",
    "_plan_json_path",
    "_state_path",
    "_summary_path",
    "_sprint_number",
    "_label_base",
]


# ── AC1: paths.py exists with the right symbols ──────────────────────────────

def test_paths_py_exists():
    """AC1: services/sprint_manager/paths.py must exist."""
    assert PATHS_PY.exists(), f"Expected {PATHS_PY} to exist"


@pytest.mark.parametrize("symbol", EXPECTED_SYMBOLS)
def test_paths_py_exports_symbol(symbol):
    """AC1: paths.py must define each required symbol."""
    import services.sprint_manager.paths as paths_mod
    assert hasattr(paths_mod, symbol), (
        f"services.sprint_manager.paths missing '{symbol}'"
    )


# ── AC2: sprint_manager.py imports from paths.py, no inline definitions ──────

def _get_sm_ast():
    source = SM_PY.read_text(encoding="utf-8")
    return ast.parse(source)


@pytest.mark.parametrize("symbol", EXPECTED_SYMBOLS)
def test_sprint_manager_has_no_inline_definition(symbol):
    """AC2: sprint_manager.py must not define these symbols inline."""
    tree = _get_sm_ast()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.name != symbol, (
                f"sprint_manager.py still defines '{symbol}' inline"
                f" at line {node.lineno}"
            )


def test_sprint_manager_imports_from_paths():
    """AC2: sprint_manager.py must import from services.sprint_manager.paths.
    """
    source = SM_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports_from_paths = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if "paths" in module and "sprint_manager" in module:
                imports_from_paths = True
                break
    assert imports_from_paths, (
        "sprint_manager.py does not import from services.sprint_manager.paths"
    )


# ── AC3: path resolutions return identical values ──────────────────────────

def test_sprint_number_returns_int():
    """AC3: _sprint_number extracts integer from sprint label."""
    from services.sprint_manager.paths import _sprint_number
    assert _sprint_number("sprint-85") == 85
    assert _sprint_number("sprint-85.1") == 85
    assert _sprint_number("no-number") is None


def test_label_base_strips_sub_sprint_suffix():
    """AC3: _label_base returns base sprint label."""
    from services.sprint_manager.paths import _label_base
    assert _label_base("sprint-68.6") == "sprint-68"
    assert _label_base("sprint-85") == "sprint-85"
    assert _label_base("other") == "other"


def test_plan_json_path_filename():
    """AC3: _plan_json_path returns correct filename for a given label."""
    from services.sprint_manager.paths import _plan_json_path
    path = _plan_json_path("sprint-85")
    assert path.name == "sprint-85-plan.json"


def test_plan_json_path_with_cfg(tmp_path):
    """AC3: _plan_json_path respects cfg.sprints_dir."""
    from services.sprint_manager.paths import _plan_json_path
    from services.sprint_manager.config import SprintConfig
    cfg = SprintConfig()
    cfg.sprints_dir = tmp_path
    path = _plan_json_path("sprint-85", cfg=cfg)
    assert path == tmp_path / "sprint-85-plan.json"


def test_pid_file_path_with_cfg(tmp_path):
    """AC3: _pid_file_path respects cfg.sprints_dir."""
    from services.sprint_manager.paths import _pid_file_path
    from services.sprint_manager.config import SprintConfig
    cfg = SprintConfig()
    cfg.sprints_dir = tmp_path
    path = _pid_file_path("sprint-85", cfg=cfg)
    assert path == tmp_path / "sprint-85-pid"


def test_state_path_with_cfg(tmp_path):
    """AC3: _state_path respects cfg.sprints_dir."""
    from services.sprint_manager.paths import _state_path
    from services.sprint_manager.config import SprintConfig
    cfg = SprintConfig()
    cfg.sprints_dir = tmp_path
    path = _state_path(85, "sprint-85", cfg=cfg)
    assert path == tmp_path / "sprint-85-state.json"


def test_summary_path_with_cfg(tmp_path):
    """AC3: _summary_path respects cfg.sprints_dir, uses date in filename."""
    from services.sprint_manager.paths import _summary_path
    from services.sprint_manager.config import SprintConfig
    cfg = SprintConfig()
    cfg.sprints_dir = tmp_path
    path = _summary_path(85, "sprint-85", cfg=cfg)
    assert path.parent == tmp_path
    assert path.name.startswith("sprint-85-summary-")
    assert path.name.endswith(".md")


# ── AC4: py_compile on paths.py exits 0 ─────────────────────────────────────

def test_py_compile_paths_py():
    """AC4: python -m py_compile services/sprint_manager/paths.py exits 0."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(PATHS_PY)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"py_compile failed on paths.py:\n{result.stderr}"
    )


# ── AC5: py_compile on sprint_manager.py exits 0 ────────────────────────────

def test_py_compile_sprint_manager_py():
    """AC5: py_compile services/sprint_manager/sprint_manager.py exits 0."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(SM_PY)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"py_compile failed on sprint_manager.py:\n{result.stderr}"
    )


# ── AC6: definitions found only in paths.py ──────────────────────────────────

def test_symbol_definitions_only_in_paths_py():
    """AC6: each symbol is defined in paths.py and NOT in sprint_manager.py."""
    sm_defined = {
        node.name
        for node in ast.walk(_get_sm_ast())
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    paths_source = PATHS_PY.read_text(encoding="utf-8")
    paths_defined = {
        node.name
        for node in ast.walk(ast.parse(paths_source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for symbol in EXPECTED_SYMBOLS:
        assert symbol not in sm_defined, (
            f"'{symbol}' still defined in sprint_manager.py"
        )
        assert symbol in paths_defined, (
            f"'{symbol}' not found in paths.py"
        )
