"""Tests for issue #1585 — Remove dead _apply_in_progress_label /
_apply_needs_rework_label helpers from sprint_manager.py.

Background (from the #1585 review of #814): the #814 branch left two helper
functions in the production module — ``_apply_in_progress_label`` and
``_apply_needs_rework_label`` — that were never reachable from any production
code path (their only callers were tests). The chosen resolution path is
**removal**: the in-progress / needs-rework label writes now flow through the
single ``state_machine.transition()`` source of truth via
``_transition_safe(..., _TicketState.IN_PROGRESS / _TicketState.NEEDS_REWORK)``.

Acceptance Criteria covered here:
  AC-1/AC-2 (removal path): neither helper name appears as a definition in the
            production module ``sprint_manager.py``.
  UAT step 2: ``grep -rn`` for the helpers under ``services/`` yields no function
            definitions (zero non-test callers).
  Wiring of replacement: the in-progress and needs-rework label writes are
            reachable through ``_transition_safe`` + the ``_TicketState`` enum,
            so the behaviour the dead helpers were meant to provide is still
            present via the live transition path.
"""
from __future__ import annotations

import re
import sys
import types
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SPRINT_MANAGER_PATH = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
SERVICES_DIR = REPO_ROOT / "services"

DEAD_HELPERS = ("_apply_in_progress_label", "_apply_needs_rework_label")


def _import_sprint_manager():
    """Import sprint_manager.py with stubbed heavy dependencies."""
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *a, **kw: None
    sys.modules.setdefault("dotenv", dotenv_stub)

    gc_stub = types.ModuleType("github_client")
    gc_stub.repo = lambda: "zealchaiwut/commander"
    gc_stub.update_labels = lambda *a, **kw: None
    gc_stub.add_comment = lambda *a, **kw: None
    gc_stub.get_issue = lambda *a, **kw: {"title": "t"}
    sys.modules.setdefault("github_client", gc_stub)

    mod_name = "sprint_manager_1585_import"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, SPRINT_MANAGER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── AC-1 / AC-2: the dead helpers are gone from the production module ──────────

def test_helper_definitions_absent_from_sprint_manager_source():
    content = SPRINT_MANAGER_PATH.read_text()
    for name in DEAD_HELPERS:
        assert f"def {name}" not in content, (
            f"{name} must not be defined in sprint_manager.py (dead code, #1585)"
        )


def test_helpers_not_module_attributes():
    sm = _import_sprint_manager()
    for name in DEAD_HELPERS:
        assert not hasattr(sm, name), (
            f"sprint_manager must not expose {name} after #1585 removal"
        )


# ── UAT step 2: no function definitions for the helpers anywhere under services/

def test_no_helper_definitions_under_services():
    """A static scan of services/ shows zero `def <helper>` definitions."""
    pattern = re.compile(r"def\s+(?:" + "|".join(DEAD_HELPERS) + r")\b")
    offenders: list[str] = []
    for path in SERVICES_DIR.rglob("*.py"):
        if pattern.search(path.read_text()):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], (
        f"Dead helpers still defined under services/: {offenders}"
    )


# ── Replacement is wired through the single transition() source of truth ───────

def test_in_progress_and_needs_rework_use_transition_safe():
    """The label writes the dead helpers used to perform are now reachable via
    _transition_safe(..., _TicketState.IN_PROGRESS / NEEDS_REWORK)."""
    content = SPRINT_MANAGER_PATH.read_text()
    assert "_transition_safe(" in content, (
        "_transition_safe must be the live label-write path"
    )
    assert "_TicketState.IN_PROGRESS" in content, (
        "in-progress label must be applied via _transition_safe(_TicketState.IN_PROGRESS)"
    )
    assert "_TicketState.NEEDS_REWORK" in content, (
        "needs-rework label must be applied via _transition_safe(_TicketState.NEEDS_REWORK)"
    )


def test_transition_safe_is_callable_on_module():
    sm = _import_sprint_manager()
    assert callable(getattr(sm, "_transition_safe", None)), (
        "_transition_safe must be importable from sprint_manager as the "
        "replacement for the removed helpers"
    )
    assert sm._TicketState is not None
    assert hasattr(sm._TicketState, "IN_PROGRESS")
    assert hasattr(sm._TicketState, "NEEDS_REWORK")
