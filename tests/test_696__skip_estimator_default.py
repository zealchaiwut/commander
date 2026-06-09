"""Tests for issue #696: Skip estimator by default when running sprint.

AC-1: run_sprint() has skip_estimator=True as default parameter value
AC-2: Running without estimator flags does not print [estimator] Running sprint estimator
AC-3: skip_estimator=False (--no-skip-estimator) prints [estimator] Running sprint estimator
AC-4: No other behavior of run_sprint() is changed
AC-5: Existing skip_estimator=True (--skip-estimator) still works, no estimator run output
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))
os.environ["COMMANDER_MAX_FIX_ROUNDS"] = "1"

import services.sprint_manager.sprint_manager as sm


_FAKE_ISSUES = [{"number": 101, "title": "Fake ticket"}]


def _apply_base_mocks(monkeypatch, tmp_path):
    monkeypatch.setattr(sm, "list_backlog_issues", lambda *a, **kw: _FAKE_ISSUES)
    monkeypatch.setattr(sm, "_dispatch_coder", lambda *a, **kw: (True, None))
    monkeypatch.setattr(sm, "_find_feature_branch", lambda n: f"feature/{n}-test")
    monkeypatch.setattr(sm, "_dispatch_tester", lambda *a, **kw: (0, None))
    monkeypatch.setattr(sm, "handle_post_tester", lambda *a, **kw: (True, "Merged", None))
    monkeypatch.setattr(sm, "_post_sprint_status", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_neon_ticket_status", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_neon_sprint_init", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_neon_sprint_status", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_setup_pid_file", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_wait_if_paused", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_create_sprint_branch", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_warn_file_conflicts", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_load_estimate", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_transition_safe", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "SPRINTS_DIR", tmp_path / "sprints")
    monkeypatch.setattr(sm, "structured_log", MagicMock())
    monkeypatch.delenv("COMMANDER_RUN_ID", raising=False)
    sm._sprint_user_cancelled.clear()


def _run(monkeypatch, tmp_path, **kwargs):
    _apply_base_mocks(monkeypatch, tmp_path)
    defaults = dict(
        label="sprint-99",
        skip_gates=True,
        gate_pytest=False,
        gate_lint=False,
        gate_merge_preview=False,
        gate_typecheck=False,
        gate_design=False,
        gate_frontend_lint=False,
    )
    defaults.update(kwargs)
    return sm.run_sprint(**defaults)


# ── AC-1 ──────────────────────────────────────────────────────────────────────

def test_ac1_skip_estimator_default_is_true():
    params = inspect.signature(sm.run_sprint).parameters
    assert "skip_estimator" in params, "run_sprint() must have skip_estimator parameter"
    assert params["skip_estimator"].default is True, (
        f"skip_estimator default must be True, got {params['skip_estimator'].default!r}"
    )


# ── AC-2 ──────────────────────────────────────────────────────────────────────

def test_ac2_no_flags_does_not_run_estimator(monkeypatch, tmp_path, capsys):
    _run(monkeypatch, tmp_path)  # no skip_estimator kwarg — uses new default True
    out = capsys.readouterr().out
    assert "[estimator] Running sprint estimator" not in out, (
        "Estimator should NOT run when skip_estimator uses its default (True)"
    )


# ── AC-3 ──────────────────────────────────────────────────────────────────────

def test_ac3_no_skip_estimator_flag_runs_estimator(monkeypatch, tmp_path, capsys):
    _run(monkeypatch, tmp_path, skip_estimator=False)  # simulates --no-skip-estimator
    out = capsys.readouterr().out
    assert "[estimator] Running sprint estimator" in out, (
        "Estimator MUST print 'Running sprint estimator' when skip_estimator=False"
    )


# ── AC-4 ──────────────────────────────────────────────────────────────────────

def test_ac4_estimator_status_is_skipped_by_default(monkeypatch, tmp_path):
    summary, state = _run(monkeypatch, tmp_path)
    assert state.estimator_status == "skipped", (
        f"estimator_status should be 'skipped' when using default, got {state.estimator_status!r}"
    )


def test_ac4_other_sprint_behavior_unchanged(monkeypatch, tmp_path):
    summary, state = _run(monkeypatch, tmp_path)
    assert summary is not None
    assert state is not None


# ── AC-5 ──────────────────────────────────────────────────────────────────────

def test_ac5_explicit_skip_estimator_true_no_run_output(monkeypatch, tmp_path, capsys):
    _run(monkeypatch, tmp_path, skip_estimator=True)  # simulates --skip-estimator
    out = capsys.readouterr().out
    assert "[estimator] Running sprint estimator" not in out, (
        "Estimator must NOT print 'Running sprint estimator' when skip_estimator=True"
    )


def test_ac5_explicit_skip_estimator_true_sets_skipped_status(monkeypatch, tmp_path):
    summary, state = _run(monkeypatch, tmp_path, skip_estimator=True)
    assert state.estimator_status == "skipped"
