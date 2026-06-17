"""Tests for issue #1269: extract sprint_manager config loading to config.py.

AC1: services/sprint_manager/config.py exists and contains SprintConfig,
     load_config, discover_config, _default_config, _resolve_path
AC2: sprint_manager.py imports these symbols from config.py (no inline defs)
AC3: No logic changes — pure move; behavior identical to before
AC4: Config discovery order and fallback behavior identical to before
AC5: All existing tests pass without modification (verified by pytest run)
AC6: python sprint_manager.py --help exits 0 and output unchanged
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SM_DIR = Path(__file__).parent.parent / "services" / "sprint_manager"
CONFIG_PY = SM_DIR / "config.py"
SM_PY = SM_DIR / "sprint_manager.py"

EXPECTED_SYMBOLS = ["SprintConfig", "load_config", "discover_config",
                    "_default_config", "_resolve_path"]


# ── AC1: config.py exists with the right symbols ─────────────────────────────

def test_config_py_exists():
    """AC1: services/sprint_manager/config.py must exist."""
    assert CONFIG_PY.exists(), f"Expected {CONFIG_PY} to exist"


@pytest.mark.parametrize("symbol", EXPECTED_SYMBOLS)
def test_config_py_exports_symbol(symbol):
    """AC1: config.py must define each required symbol."""
    import services.sprint_manager.config as cfg_mod
    assert hasattr(cfg_mod, symbol), (
        f"services.sprint_manager.config missing '{symbol}'"
    )


# ── AC2: sprint_manager.py imports from config.py, no inline definitions ─────

def _get_sm_ast():
    source = SM_PY.read_text(encoding="utf-8")
    return ast.parse(source)


@pytest.mark.parametrize("symbol", EXPECTED_SYMBOLS)
def test_sprint_manager_has_no_inline_definition(symbol):
    """AC2: sprint_manager.py must not define these symbols inline."""
    tree = _get_sm_ast()
    for node in ast.walk(tree):
        # Check for class or function definitions with the symbol name
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.name != symbol, (
                f"sprint_manager.py still defines '{symbol}' inline at line {node.lineno}"
            )


def test_sprint_manager_imports_from_config():
    """AC2: sprint_manager.py must import from services.sprint_manager.config."""
    source = SM_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports_from_config = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if "config" in module and "sprint_manager" in module:
                imports_from_config = True
                break
    assert imports_from_config, (
        "sprint_manager.py does not import from services.sprint_manager.config"
    )


# ── AC3 & AC4: behavior identical to before ─────────────────────────────────

def _make_sprint_yaml(tmp_path: Path, extra: str = "") -> Path:
    """Helper: valid sprint.yaml with real tmp worktree paths."""
    coder_wt = tmp_path / "coder"
    tester_wt = tmp_path / "tester"
    coder_wt.mkdir()
    tester_wt.mkdir()
    yaml_path = tmp_path / "sprint.yaml"
    yaml_path.write_text(
        f"repo_name: owner/testrepo\n"
        f"worktrees:\n"
        f"  coder: {coder_wt}\n"
        f"  tester: {tester_wt}\n"
        + extra,
        encoding="utf-8",
    )
    return yaml_path


def test_load_config_from_config_module(tmp_path):
    """AC3: load_config imported from config.py returns a valid SprintConfig."""
    from services.sprint_manager.config import load_config, SprintConfig
    yaml_path = _make_sprint_yaml(tmp_path)
    cfg = load_config(yaml_path)
    assert isinstance(cfg, SprintConfig)
    assert cfg.repo_name == "owner/testrepo"


def test_load_config_from_sprint_manager_still_works(tmp_path):
    """AC3: load_config re-exported via sprint_manager.py is same object."""
    import services.sprint_manager.sprint_manager as sm
    from services.sprint_manager.config import load_config as cfg_load
    assert sm.load_config is cfg_load, (
        "sprint_manager.load_config must be the same object as config.load_config"
    )


def test_discover_config_walks_up(tmp_path):
    """AC4: discover_config finds .commander/sprint.yaml walking up from subdir."""
    from services.sprint_manager.config import discover_config
    commander_dir = tmp_path / ".commander"
    commander_dir.mkdir()
    yaml_path = commander_dir / "sprint.yaml"
    yaml_path.write_text("repo_name: test\n", encoding="utf-8")
    subdir = tmp_path / "a" / "b"
    subdir.mkdir(parents=True)
    found = discover_config(start_dir=subdir)
    assert found == yaml_path


def test_discover_config_returns_none_when_missing(tmp_path):
    """AC4: discover_config returns None when no config file found."""
    from services.sprint_manager.config import discover_config
    found = discover_config(start_dir=tmp_path)
    assert found is None


def test_default_config_returns_sprint_config():
    """AC4: _default_config() returns a SprintConfig instance with defaults."""
    from services.sprint_manager.config import _default_config, SprintConfig
    cfg = _default_config()
    assert isinstance(cfg, SprintConfig)
    assert cfg.api_url is not None


def test_resolve_path_absolute_passthrough(tmp_path):
    """AC3: _resolve_path with absolute path returns it unchanged."""
    from services.sprint_manager.config import _resolve_path
    abs_path = tmp_path / "some" / "dir"
    result = _resolve_path(str(abs_path), Path("/base"))
    assert result == abs_path


def test_resolve_path_relative_resolves_against_base(tmp_path):
    """AC3: _resolve_path with relative path resolves against base_dir."""
    from services.sprint_manager.config import _resolve_path
    base = tmp_path / "base"
    base.mkdir()
    result = _resolve_path("subdir", base)
    assert result == (base / "subdir").resolve()


# ── AC6: python sprint_manager.py --help exits 0 ────────────────────────────

def test_sprint_manager_help_exits_zero():
    """AC6: python sprint_manager.py --help exits 0 with no import errors."""
    result = subprocess.run(
        [sys.executable, str(SM_PY), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"sprint_manager.py --help failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "usage" in result.stdout.lower() or "usage" in result.stderr.lower(), (
        "Expected 'usage' in help output"
    )
