"""Tests for issue #1542: Remove dead try/except from settings_service.py.

AC1: The dead try/except block at settings_service.py (which only sets
     SYNC_SETTINGS_AVAILABLE = True with an unreachable except) is removed,
     along with the SYNC_SETTINGS_AVAILABLE flag declaration.
AC2: No other references to SYNC_SETTINGS_AVAILABLE or _ss_* names exist
     in settings_service.py after the removal.
AC3: The file settings_service.py is syntactically valid Python and imports
     correctly with no NameError or ImportError at startup.
"""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

SETTINGS_SERVICE_PATH = (
    Path(__file__).resolve().parent.parent
    / "apps" / "dashboard" / "routers" / "settings_service.py"
)


def _source() -> str:
    return SETTINGS_SERVICE_PATH.read_text(encoding="utf-8")


# ── AC1: SYNC_SETTINGS_AVAILABLE flag is gone ────────────────────────────────

def test_sync_settings_available_flag_removed():
    """AC1: SYNC_SETTINGS_AVAILABLE must not appear anywhere in settings_service.py."""
    assert "SYNC_SETTINGS_AVAILABLE" not in _source(), (
        "Dead flag SYNC_SETTINGS_AVAILABLE still present in settings_service.py"
    )


def test_dead_try_except_block_removed():
    """AC1: The bare 'try: SYNC_SETTINGS_AVAILABLE = True' block must be gone."""
    src = _source()
    assert "SYNC_SETTINGS_AVAILABLE = True" not in src, (
        "Dead assignment SYNC_SETTINGS_AVAILABLE = True still present"
    )
    assert "SYNC_SETTINGS_AVAILABLE = False" not in src, (
        "Dead assignment SYNC_SETTINGS_AVAILABLE = False still present"
    )


# ── AC2: No _ss_* aliases in settings_service.py ────────────────────────────

def test_no_ss_aliases_in_settings_service():
    """AC2: No _ss_* names should appear in settings_service.py.
    (They are legitimately used in settings_sync.py, but not here.)
    """
    src = _source()
    lines_with_ss = [
        line for line in src.splitlines() if "_ss_" in line
    ]
    assert not lines_with_ss, (
        f"Unexpected _ss_* references in settings_service.py:\n"
        + "\n".join(lines_with_ss)
    )


# ── AC3: Syntactically valid Python ──────────────────────────────────────────

def test_settings_service_parses_without_syntax_error():
    """AC3: settings_service.py must parse as valid Python (no SyntaxError)."""
    src = _source()
    try:
        ast.parse(src, filename=str(SETTINGS_SERVICE_PATH))
    except SyntaxError as exc:
        raise AssertionError(f"settings_service.py has a syntax error: {exc}") from exc


def test_settings_service_module_attributes_present():
    """AC3: Module must expose core service functions without NameError/ImportError.

    We check attributes on the already-imported module (or import fresh).
    The module has heavy dependency chains (FastAPI, projects, etc.) so we
    verify the essential public symbols are present after import.
    """
    mod_name = "apps.dashboard.routers.settings_service"
    if mod_name in sys.modules:
        mod = sys.modules[mod_name]
        importlib.reload(mod)
    else:
        mod = importlib.import_module(mod_name)

    assert hasattr(mod, "get_global_settings"), "get_global_settings missing from module"
    assert hasattr(mod, "put_global_settings"), "put_global_settings missing from module"
    assert hasattr(mod, "get_project_settings"), "get_project_settings missing from module"
    assert not hasattr(mod, "SYNC_SETTINGS_AVAILABLE"), (
        "Dead flag SYNC_SETTINGS_AVAILABLE still exported by the module"
    )
