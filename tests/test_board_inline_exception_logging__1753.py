"""Tests for issue #1753: broad except Exception swallows errors in inline aggregate builders.

AC coverage:
  AC1 — _build_outcome_inline logs a warning (with exc_info) when db.get_sprint raises
  AC2 — _build_finish_card_inline logs a warning (with exc_info) when db.get_sprint raises
  AC3 — _build_branch_status_inline logs a warning (with exc_info) when db.get_sprint raises
  AC4 — the warning includes the sprint label so the failure is diagnosable
  AC5 — functions still return their safe fallback after logging (no exception propagated)
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
_SERVICES_ROOT = _REPO_ROOT / "services" / "sprint_manager"

for _p in (str(_DASHBOARD_ROOT), str(_DASHBOARD_ROOT / "routers"), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load_board_service():
    if "board_service" in sys.modules:
        return sys.modules["board_service"]
    spec = importlib.util.find_spec("board_service")
    bs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bs)
    sys.modules["board_service"] = bs
    return bs


def _make_mock_db_raising(exc: Exception):
    """Return a mock db whose get_sprint always raises exc."""
    mock_db = MagicMock()
    mock_db.get_sprint.side_effect = exc
    return mock_db


# ── AC1: _build_outcome_inline logs warning on db error ──────────────────────

def test_ac1_outcome_inline_logs_warning_on_db_error(caplog):
    """_build_outcome_inline warns when db.get_sprint raises an unexpected exception (AC1)."""
    bs = _load_board_service()
    error = RuntimeError("simulated db failure")

    with patch.object(bs, "db", _make_mock_db_raising(error)):
        with caplog.at_level(logging.WARNING, logger="board_service"):
            result = bs._build_outcome_inline("sprint-99", "owner/repo", "planned")

    assert any("sprint-99" in r.getMessage() for r in caplog.records), (
        "_build_outcome_inline must log a warning that includes the sprint label"
    )
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, "_build_outcome_inline must emit at least one WARNING log"


def test_ac1_outcome_inline_logs_exc_info(caplog):
    """_build_outcome_inline warning includes exc_info so the traceback is visible (AC1)."""
    bs = _load_board_service()
    error = RuntimeError("simulated db failure")

    with patch.object(bs, "db", _make_mock_db_raising(error)):
        with caplog.at_level(logging.WARNING, logger="board_service"):
            bs._build_outcome_inline("sprint-99", "owner/repo", "planned")

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, "Expected at least one WARNING record"
    assert warning_records[0].exc_info is not None, (
        "_build_outcome_inline warning must include exc_info=True so the traceback is logged"
    )


# ── AC2: _build_finish_card_inline logs warning on db error ──────────────────

def test_ac2_finish_card_inline_logs_warning_on_db_error(caplog):
    """_build_finish_card_inline warns when db.get_sprint raises an unexpected exception (AC2)."""
    bs = _load_board_service()
    error = RuntimeError("simulated db failure")

    with patch.object(bs, "db", _make_mock_db_raising(error)):
        with caplog.at_level(logging.WARNING, logger="board_service"):
            result = bs._build_finish_card_inline("sprint-99", "owner/repo", "planned")

    assert any("sprint-99" in r.getMessage() for r in caplog.records), (
        "_build_finish_card_inline must log a warning that includes the sprint label"
    )
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, "_build_finish_card_inline must emit at least one WARNING log"


def test_ac2_finish_card_inline_logs_exc_info(caplog):
    """_build_finish_card_inline warning includes exc_info (AC2)."""
    bs = _load_board_service()
    error = RuntimeError("simulated db failure")

    with patch.object(bs, "db", _make_mock_db_raising(error)):
        with caplog.at_level(logging.WARNING, logger="board_service"):
            bs._build_finish_card_inline("sprint-99", "owner/repo", "planned")

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, "Expected at least one WARNING record"
    assert warning_records[0].exc_info is not None, (
        "_build_finish_card_inline warning must include exc_info=True"
    )


# ── AC3: _build_branch_status_inline logs warning on db error ────────────────

def test_ac3_branch_status_inline_logs_warning_on_db_error(caplog):
    """_build_branch_status_inline warns when db.get_sprint raises an unexpected exception (AC3)."""
    bs = _load_board_service()
    error = RuntimeError("simulated db failure")

    with patch.object(bs, "db", _make_mock_db_raising(error)):
        with caplog.at_level(logging.WARNING, logger="board_service"):
            result = bs._build_branch_status_inline("sprint-99", "owner/repo")

    assert any("sprint-99" in r.getMessage() for r in caplog.records), (
        "_build_branch_status_inline must log a warning that includes the sprint label"
    )
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, "_build_branch_status_inline must emit at least one WARNING log"


def test_ac3_branch_status_inline_logs_exc_info(caplog):
    """_build_branch_status_inline warning includes exc_info (AC3)."""
    bs = _load_board_service()
    error = RuntimeError("simulated db failure")

    with patch.object(bs, "db", _make_mock_db_raising(error)):
        with caplog.at_level(logging.WARNING, logger="board_service"):
            bs._build_branch_status_inline("sprint-99", "owner/repo")

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, "Expected at least one WARNING record"
    assert warning_records[0].exc_info is not None, (
        "_build_branch_status_inline warning must include exc_info=True"
    )


# ── AC4: label included in warning message ────────────────────────────────────

def test_ac4_outcome_warning_contains_sprint_label(caplog):
    """Warning from _build_outcome_inline includes the sprint label for diagnosability (AC4)."""
    bs = _load_board_service()
    with patch.object(bs, "db", _make_mock_db_raising(RuntimeError("boom"))):
        with caplog.at_level(logging.WARNING, logger="board_service"):
            bs._build_outcome_inline("sprint-42", "owner/repo", "planned")
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "sprint-42" in msgs


def test_ac4_finish_card_warning_contains_sprint_label(caplog):
    """Warning from _build_finish_card_inline includes the sprint label (AC4)."""
    bs = _load_board_service()
    with patch.object(bs, "db", _make_mock_db_raising(RuntimeError("boom"))):
        with caplog.at_level(logging.WARNING, logger="board_service"):
            bs._build_finish_card_inline("sprint-42", "owner/repo", "planned")
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "sprint-42" in msgs


def test_ac4_branch_status_warning_contains_sprint_label(caplog):
    """Warning from _build_branch_status_inline includes the sprint label (AC4)."""
    bs = _load_board_service()
    with patch.object(bs, "db", _make_mock_db_raising(RuntimeError("boom"))):
        with caplog.at_level(logging.WARNING, logger="board_service"):
            bs._build_branch_status_inline("sprint-42", "owner/repo")
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "sprint-42" in msgs


# ── AC5: safe fallbacks still returned ───────────────────────────────────────

def test_ac5_outcome_inline_returns_none_on_db_error(caplog):
    """_build_outcome_inline returns None (not raises) even when db.get_sprint raises (AC5)."""
    bs = _load_board_service()
    with patch.object(bs, "db", _make_mock_db_raising(RuntimeError("boom"))):
        with caplog.at_level(logging.WARNING, logger="board_service"):
            result = bs._build_outcome_inline("sprint-99", "owner/repo", "planned")
    assert result is None, "Must still return None as the safe fallback"


def test_ac5_finish_card_inline_returns_no_data_on_db_error(caplog):
    """_build_finish_card_inline returns no_data dict (not raises) when db.get_sprint raises (AC5)."""
    bs = _load_board_service()
    with patch.object(bs, "db", _make_mock_db_raising(RuntimeError("boom"))):
        with caplog.at_level(logging.WARNING, logger="board_service"):
            result = bs._build_finish_card_inline("sprint-99", "owner/repo", "planned")
    assert result.get("state") == "no_data", (
        "_build_finish_card_inline must return no_data dict as safe fallback"
    )


def test_ac5_branch_status_inline_returns_base_on_db_error(caplog):
    """_build_branch_status_inline returns base dict (not raises) when db.get_sprint raises (AC5)."""
    bs = _load_board_service()
    with patch.object(bs, "db", _make_mock_db_raising(RuntimeError("boom"))):
        with caplog.at_level(logging.WARNING, logger="board_service"):
            result = bs._build_branch_status_inline("sprint-99", "owner/repo")
    assert "exists" in result, "Must still return base dict with 'exists' key"
    assert "branch" in result, "Must still return base dict with 'branch' key"
    assert result["exists"] is False, "Safe fallback must have exists=False"
