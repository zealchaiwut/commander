"""Tests for issue #2230 — finish_feature.py must transition ticket to UAT.

AC coverage:
  AC1 — finish_feature.py calls state_machine.transition(..., UAT) after a
         successful merge when COMMANDER_SPRINT_RUNNING is not set
  AC2 — When COMMANDER_SPRINT_LABEL (COMMANDER_SPRINT_RUNNING) is set,
         transition is NOT called (dispatch path handles UAT itself)
  AC4 — Behavioral: guard env var unset → label moves to uat;
         guard env var set → label unchanged
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch, call

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
for _p in (str(_REPO_ROOT), str(_SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load_finish_feature() -> ModuleType:
    """Import finish_feature with its path-dependent side effects suppressed."""
    if "finish_feature" in sys.modules:
        return sys.modules["finish_feature"]
    with patch("dotenv.load_dotenv"):
        with patch.dict("sys.modules", {
            "github_client": MagicMock(),
            "services.run_id": MagicMock(mint_run_id=MagicMock(return_value="run-test")),
            "services.logging": MagicMock(log=MagicMock()),
        }):
            spec = importlib.util.spec_from_file_location(
                "finish_feature", _SCRIPTS_DIR / "finish_feature.py"
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            sys.modules["finish_feature"] = mod
    return mod


_ff = _load_finish_feature()


# ---------------------------------------------------------------------------
# AC1 — transition() is called with UAT when COMMANDER_SPRINT_RUNNING is absent
# ---------------------------------------------------------------------------

def test_uat_transition_called_when_no_dispatch_env(monkeypatch):
    """AC1/AC4: manual path — transition to UAT must fire."""
    monkeypatch.delenv("COMMANDER_SPRINT_RUNNING", raising=False)

    mock_transition = MagicMock(return_value=True)
    mock_ticket_state = MagicMock()
    mock_ticket_state.UAT = "UAT_SENTINEL"

    with patch.dict("sys.modules", {
        "services.sprint_manager.state_machine": MagicMock(
            transition=mock_transition,
            TicketState=mock_ticket_state,
        )
    }):
        _ff._apply_uat_if_manual(issue_num=42, repo="owner/repo")

    mock_transition.assert_called_once()
    _args, _kwargs = mock_transition.call_args
    assert _args[0] == 42
    assert _args[1] == "UAT_SENTINEL"
    assert _kwargs.get("actor") == "finish_feature"


# ---------------------------------------------------------------------------
# AC2 — transition() is NOT called when COMMANDER_SPRINT_RUNNING is set
# ---------------------------------------------------------------------------

def test_uat_transition_skipped_when_dispatch_env_set(monkeypatch):
    """AC2/AC4: dispatch path — sprint_manager owns UAT; finish_feature must skip."""
    monkeypatch.setenv("COMMANDER_SPRINT_RUNNING", "sprint-1021")

    mock_transition = MagicMock()

    with patch.dict("sys.modules", {
        "services.sprint_manager.state_machine": MagicMock(
            transition=mock_transition,
        )
    }):
        _ff._apply_uat_if_manual(issue_num=42, repo="owner/repo")

    mock_transition.assert_not_called()


# ---------------------------------------------------------------------------
# AC4 — empty string for COMMANDER_SPRINT_RUNNING also counts as "not set"
# ---------------------------------------------------------------------------

def test_uat_transition_called_when_dispatch_env_empty(monkeypatch):
    """AC4: empty COMMANDER_SPRINT_RUNNING is treated as unset (manual path)."""
    monkeypatch.setenv("COMMANDER_SPRINT_RUNNING", "")

    mock_transition = MagicMock(return_value=True)
    mock_ticket_state = MagicMock()
    mock_ticket_state.UAT = "UAT_SENTINEL"

    with patch.dict("sys.modules", {
        "services.sprint_manager.state_machine": MagicMock(
            transition=mock_transition,
            TicketState=mock_ticket_state,
        )
    }):
        _ff._apply_uat_if_manual(issue_num=99, repo="owner/repo")

    mock_transition.assert_called_once()


# ---------------------------------------------------------------------------
# AC1 — transition failure is swallowed (best-effort; does not crash the script)
# ---------------------------------------------------------------------------

def test_uat_transition_failure_is_non_fatal(monkeypatch, capsys):
    """AC1: a TransitionError from state_machine must not propagate out."""
    monkeypatch.delenv("COMMANDER_SPRINT_RUNNING", raising=False)

    def _boom(*a, **kw):
        raise RuntimeError("gh api failed")

    with patch.dict("sys.modules", {
        "services.sprint_manager.state_machine": MagicMock(
            transition=_boom,
            TicketState=MagicMock(UAT="UAT_SENTINEL"),
        )
    }):
        # Must not raise
        _ff._apply_uat_if_manual(issue_num=7, repo="owner/repo")

    captured = capsys.readouterr()
    assert "Warning" in captured.out or "warning" in captured.out.lower()
