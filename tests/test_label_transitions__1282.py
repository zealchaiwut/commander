"""Tests for issue #1282: Extract label transition logic to dedicated service module.

Acceptance Criteria:
- AC-1: File services/sprint_manager/label_transitions.py exists and contains all
        six functions: _get_issue_labels, _current_status_labels, _sweep_stale_status,
        _transition_safe, _add_blocked_label, _emit_label_transition_event
- AC-2: Original call sites import and invoke the moved functions from the new module
        without modification to call signatures
- AC-3: No function logic is altered — pure move only, zero behavioral change
- AC-4: python -m py_compile services/sprint_manager/label_transitions.py exits 0
- AC-5: python -m py_compile on all files that previously contained or imported
        these functions also exits 0
- AC-6: All existing label transition behavior is preserved end-to-end
"""
from __future__ import annotations

import importlib
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))


# ── AC-1: module exists with all six functions ────────────────────────────────

def test_label_transitions_module_exists():
    """AC-1: label_transitions.py exists on disk."""
    module_path = REPO_ROOT / "services" / "sprint_manager" / "label_transitions.py"
    assert module_path.exists(), (
        f"Expected {module_path} to exist — the six label-transition functions "
        "must be extracted there per issue #1282 AC-1."
    )


def test_all_six_functions_importable():
    """AC-1: All six functions are importable from label_transitions."""
    import services.sprint_manager.label_transitions as lt  # noqa: PLC0415

    missing = []
    for name in (
        "_get_issue_labels",
        "_current_status_labels",
        "_sweep_stale_status",
        "_transition_safe",
        "_add_blocked_label",
        "_emit_label_transition_event",
    ):
        if not hasattr(lt, name):
            missing.append(name)

    assert not missing, (
        f"Functions missing from label_transitions: {missing} — "
        "AC-1 requires all six to be defined there."
    )


def test_all_six_are_callables():
    """AC-1: All six exported symbols are callable functions."""
    import services.sprint_manager.label_transitions as lt  # noqa: PLC0415

    for name in (
        "_get_issue_labels",
        "_current_status_labels",
        "_sweep_stale_status",
        "_transition_safe",
        "_add_blocked_label",
        "_emit_label_transition_event",
    ):
        fn = getattr(lt, name, None)
        assert callable(fn), f"{name} in label_transitions must be callable"


# ── AC-2: call signatures unchanged ──────────────────────────────────────────

def test_get_issue_labels_signature():
    """AC-2: _get_issue_labels(issue_num, repo_name=None) signature preserved."""
    import services.sprint_manager.label_transitions as lt  # noqa: PLC0415

    sig = inspect.signature(lt._get_issue_labels)
    params = list(sig.parameters)
    assert params[0] == "issue_num"
    assert "repo_name" in params
    assert sig.parameters["repo_name"].default is None


def test_current_status_labels_signature():
    """AC-2: _current_status_labels(issue_num, repo_name) signature preserved."""
    import services.sprint_manager.label_transitions as lt  # noqa: PLC0415

    sig = inspect.signature(lt._current_status_labels)
    params = list(sig.parameters)
    assert params[0] == "issue_num"
    assert params[1] == "repo_name"


def test_sweep_stale_status_signature():
    """AC-2: _sweep_stale_status(status_label, sprint_label, repo_name, active_issue=None) preserved."""
    import services.sprint_manager.label_transitions as lt  # noqa: PLC0415

    sig = inspect.signature(lt._sweep_stale_status)
    params = list(sig.parameters)
    assert params[0] == "status_label"
    assert params[1] == "sprint_label"
    assert params[2] == "repo_name"
    assert "active_issue" in params
    assert sig.parameters["active_issue"].default is None


def test_transition_safe_signature():
    """AC-2: _transition_safe(issue_num, target_state, actor, repo_name=None, note=None) preserved."""
    import services.sprint_manager.label_transitions as lt  # noqa: PLC0415

    sig = inspect.signature(lt._transition_safe)
    params = list(sig.parameters)
    assert params[0] == "issue_num"
    assert params[1] == "target_state"
    assert params[2] == "actor"
    assert "repo_name" in params
    assert "note" in params
    assert sig.parameters["repo_name"].default is None
    assert sig.parameters["note"].default is None


def test_add_blocked_label_signature():
    """AC-2: _add_blocked_label(issue_num, reason, repo_name=None, sprint_label=None) preserved."""
    import services.sprint_manager.label_transitions as lt  # noqa: PLC0415

    sig = inspect.signature(lt._add_blocked_label)
    params = list(sig.parameters)
    assert params[0] == "issue_num"
    assert params[1] == "reason"
    assert "repo_name" in params
    assert "sprint_label" in params
    assert sig.parameters["repo_name"].default is None
    assert sig.parameters["sprint_label"].default is None


def test_emit_label_transition_event_signature():
    """AC-2: _emit_label_transition_event(issue_num, target_state, actor, repo_name, before) preserved."""
    import services.sprint_manager.label_transitions as lt  # noqa: PLC0415

    sig = inspect.signature(lt._emit_label_transition_event)
    params = list(sig.parameters)
    assert params[0] == "issue_num"
    assert params[1] == "target_state"
    assert params[2] == "actor"
    assert params[3] == "repo_name"
    assert params[4] == "before"


# ── AC-4 & AC-5: py_compile passes ───────────────────────────────────────────

def test_py_compile_label_transitions():
    """AC-4: python -m py_compile on label_transitions.py exits 0."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile",
         str(REPO_ROOT / "services" / "sprint_manager" / "label_transitions.py")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"py_compile failed on label_transitions.py:\n{result.stderr}"
    )
    assert result.stdout == "", (
        f"py_compile produced unexpected output:\n{result.stdout}"
    )


def test_py_compile_sprint_manager():
    """AC-5: python -m py_compile on sprint_manager.py exits 0 after imports updated."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile",
         str(REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"py_compile failed on sprint_manager.py:\n{result.stderr}"
    )


# ── AC-3: functions not defined in sprint_manager directly ────────────────────

def test_functions_not_defined_in_sprint_manager():
    """AC-3: The six functions must not be *defined* in sprint_manager.py —
    they must be imported from label_transitions."""
    sm_path = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
    source = sm_path.read_text()

    for fn_name in (
        "_get_issue_labels",
        "_current_status_labels",
        "_sweep_stale_status",
        "_transition_safe",
        "_add_blocked_label",
        "_emit_label_transition_event",
    ):
        # A definition looks like "def <fn_name>(" at the start of a line
        definition = f"\ndef {fn_name}("
        assert definition not in source, (
            f"{fn_name} is still defined in sprint_manager.py — it must be "
            "moved to label_transitions.py (AC-3)."
        )


# ── AC-1: functions defined in label_transitions source ──────────────────────

def test_functions_defined_in_label_transitions_source():
    """AC-1: All six functions are defined (not just imported) in label_transitions.py."""
    lt_path = REPO_ROOT / "services" / "sprint_manager" / "label_transitions.py"
    if not lt_path.exists():
        pytest.skip("label_transitions.py not yet created")

    source = lt_path.read_text()
    for fn_name in (
        "_get_issue_labels",
        "_current_status_labels",
        "_sweep_stale_status",
        "_transition_safe",
        "_add_blocked_label",
        "_emit_label_transition_event",
    ):
        definition = f"def {fn_name}("
        assert definition in source, (
            f"{fn_name} not defined in label_transitions.py — AC-1 requires "
            "all six function bodies live in that file."
        )
