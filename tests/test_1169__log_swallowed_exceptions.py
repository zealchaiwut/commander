"""Tests for issue #1169 — Log swallowed exceptions in timeline_service data helpers.

AC coverage:
  AC1 — _get_sprint_issues logs a warning (with exc_info) when the GitHub call raises
  AC2 — _get_agent_runs logs a warning (with exc_info) when the DB call raises
  AC3 — _get_settings logs a warning (with exc_info) when settings_repo raises
  AC4 — _get_sprint_row logs a warning (with exc_info) when the DB call raises
  AC5 — _get_calibration_records logs a warning (with exc_info) when calibration raises
  AC6 — All helpers still return their graceful-degradation values after logging
"""
from __future__ import annotations

import logging
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVICES_DIR = REPO_ROOT / "services" / "sprint_manager"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR), str(SERVICES_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from apps.dashboard.routers import timeline_service  # noqa: E402

_LOG_NAME = "apps.dashboard.routers.timeline_service"


# ── AC1: _get_sprint_issues logs on GitHub failure ───────────────────────────

def test_ac1_get_sprint_issues_logs_warning_on_exception(monkeypatch, caplog):
    """_get_sprint_issues must emit a WARNING with exc_info when the GitHub call raises."""
    mock_server = MagicMock()
    mock_server.github_client.list_open_issues_with_body.side_effect = RuntimeError("GitHub down")
    monkeypatch.setattr(timeline_service, "_server", lambda: mock_server)

    with caplog.at_level(logging.WARNING, logger=_LOG_NAME):
        result = timeline_service._get_sprint_issues("sprint-99", "owner/repo")

    assert result == [], "must still return [] on failure"
    assert caplog.records, "expected at least one log record"
    assert any(r.levelno >= logging.WARNING for r in caplog.records), \
        "must log at WARNING level or above"


def test_ac1_get_sprint_issues_log_record_has_exc_info(monkeypatch, caplog):
    """The warning emitted by _get_sprint_issues must include exc_info."""
    mock_server = MagicMock()
    err = RuntimeError("GitHub down")
    mock_server.github_client.list_open_issues_with_body.side_effect = err
    monkeypatch.setattr(timeline_service, "_server", lambda: mock_server)

    with caplog.at_level(logging.WARNING, logger=_LOG_NAME):
        timeline_service._get_sprint_issues("sprint-99", "owner/repo")

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, "no warning record emitted"
    assert warning_records[0].exc_info is not None, \
        "warning must carry exc_info so the traceback is visible"


# ── AC2: _get_agent_runs logs on DB failure ───────────────────────────────────

def test_ac2_get_agent_runs_logs_warning_on_exception(monkeypatch, caplog):
    """_get_agent_runs must emit a WARNING with exc_info when the DB call raises."""
    monkeypatch.setattr(
        timeline_service._db,
        "agent_runs_for_sprint",
        MagicMock(side_effect=RuntimeError("DB locked")),
    )

    with caplog.at_level(logging.WARNING, logger=_LOG_NAME):
        result = timeline_service._get_agent_runs("sprint-99", "owner/repo")

    assert result == [], "must still return [] on failure"
    assert any(r.levelno >= logging.WARNING for r in caplog.records), \
        "must log at WARNING level or above"


def test_ac2_get_agent_runs_log_record_has_exc_info(monkeypatch, caplog):
    """The warning emitted by _get_agent_runs must include exc_info."""
    monkeypatch.setattr(
        timeline_service._db,
        "agent_runs_for_sprint",
        MagicMock(side_effect=RuntimeError("DB locked")),
    )

    with caplog.at_level(logging.WARNING, logger=_LOG_NAME):
        timeline_service._get_agent_runs("sprint-99")

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, "no warning record emitted"
    assert warning_records[0].exc_info is not None, \
        "warning must carry exc_info"


# ── AC3: _get_settings logs on settings_repo failure ─────────────────────────

def test_ac3_get_settings_logs_warning_on_exception(monkeypatch, caplog):
    """_get_settings must emit a WARNING with exc_info when settings_repo raises."""
    monkeypatch.setattr(
        timeline_service._settings_repo,
        "get_setting",
        MagicMock(side_effect=RuntimeError("settings repo unavailable")),
    )

    with caplog.at_level(logging.WARNING, logger=_LOG_NAME):
        result = timeline_service._get_settings("owner/repo")

    assert isinstance(result, dict), "must still return a dict (defaults) on failure"
    assert len(result) > 0, "returned dict must contain default values"
    assert any(r.levelno >= logging.WARNING for r in caplog.records), \
        "must log at WARNING level or above"


def test_ac3_get_settings_log_record_has_exc_info(monkeypatch, caplog):
    """The warning emitted by _get_settings must include exc_info."""
    monkeypatch.setattr(
        timeline_service._settings_repo,
        "get_setting",
        MagicMock(side_effect=RuntimeError("settings repo unavailable")),
    )

    with caplog.at_level(logging.WARNING, logger=_LOG_NAME):
        timeline_service._get_settings("owner/repo")

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, "no warning record emitted"
    assert warning_records[0].exc_info is not None, \
        "warning must carry exc_info"


# ── AC4: _get_sprint_row logs on DB failure ───────────────────────────────────

def test_ac4_get_sprint_row_logs_warning_on_exception(monkeypatch, caplog):
    """_get_sprint_row must emit a WARNING with exc_info when the DB call raises."""
    monkeypatch.setattr(
        timeline_service._db,
        "get_sprint",
        MagicMock(side_effect=RuntimeError("sprints table missing")),
    )

    with caplog.at_level(logging.WARNING, logger=_LOG_NAME):
        result = timeline_service._get_sprint_row("sprint-99", "owner/repo")

    assert result is None, "must still return None on failure"
    assert any(r.levelno >= logging.WARNING for r in caplog.records), \
        "must log at WARNING level or above"


def test_ac4_get_sprint_row_log_record_has_exc_info(monkeypatch, caplog):
    """The warning emitted by _get_sprint_row must include exc_info."""
    monkeypatch.setattr(
        timeline_service._db,
        "get_sprint",
        MagicMock(side_effect=RuntimeError("sprints table missing")),
    )

    with caplog.at_level(logging.WARNING, logger=_LOG_NAME):
        timeline_service._get_sprint_row("sprint-99")

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, "no warning record emitted"
    assert warning_records[0].exc_info is not None, \
        "warning must carry exc_info"


# ── AC5: _get_calibration_records logs on import/call failure ────────────────

def test_ac5_get_calibration_records_logs_warning_on_exception(caplog):
    """_get_calibration_records must emit a WARNING with exc_info when calibration raises."""
    fake_calibration = types.ModuleType("calibration")

    def _raise():
        raise RuntimeError("calibration table missing")

    fake_calibration.sqlite_calibration_records = _raise  # type: ignore[attr-defined]

    with patch.dict(sys.modules, {"calibration": fake_calibration}):
        with caplog.at_level(logging.WARNING, logger=_LOG_NAME):
            result = timeline_service._get_calibration_records()

    assert result == [], "must still return [] on failure"
    assert any(r.levelno >= logging.WARNING for r in caplog.records), \
        "must log at WARNING level or above"


def test_ac5_get_calibration_records_log_record_has_exc_info(caplog):
    """The warning emitted by _get_calibration_records must include exc_info."""
    fake_calibration = types.ModuleType("calibration")

    def _raise():
        raise RuntimeError("calibration table missing")

    fake_calibration.sqlite_calibration_records = _raise  # type: ignore[attr-defined]

    with patch.dict(sys.modules, {"calibration": fake_calibration}):
        with caplog.at_level(logging.WARNING, logger=_LOG_NAME):
            timeline_service._get_calibration_records()

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, "no warning record emitted"
    assert warning_records[0].exc_info is not None, \
        "warning must carry exc_info"


# ── AC6: graceful-degradation values are unchanged ───────────────────────────

def test_ac6_helpers_return_correct_degradation_values(monkeypatch, caplog):
    """All helpers must return their original graceful-degradation values even after logging."""
    mock_server = MagicMock()
    mock_server.github_client.list_open_issues_with_body.side_effect = RuntimeError("err")
    monkeypatch.setattr(timeline_service, "_server", lambda: mock_server)
    monkeypatch.setattr(
        timeline_service._db, "agent_runs_for_sprint",
        MagicMock(side_effect=RuntimeError("err")),
    )
    monkeypatch.setattr(
        timeline_service._settings_repo, "get_setting",
        MagicMock(side_effect=RuntimeError("err")),
    )
    monkeypatch.setattr(
        timeline_service._db, "get_sprint",
        MagicMock(side_effect=RuntimeError("err")),
    )

    with caplog.at_level(logging.WARNING, logger=_LOG_NAME):
        assert timeline_service._get_sprint_issues("sprint-99", "owner/repo") == []
        assert timeline_service._get_agent_runs("sprint-99") == []
        assert isinstance(timeline_service._get_settings("owner/repo"), dict)
        assert timeline_service._get_sprint_row("sprint-99") is None
