"""Tests for issue #688 — Extract date-param validation to helper function.

AC coverage:
  AC1  — _parse_iso_date exists and is importable from server.py
  AC2  — _parse_iso_date returns datetime at midnight UTC for a valid YYYY-MM-DD string
  AC3  — _parse_iso_date raises HTTPException(400) for an invalid date string
  AC4  — HTTPException detail includes the param name (in quotes) and the bad value
  AC5  — _parse_iso_date with end_of_day=True returns datetime at 23:59:59 UTC
  AC6  — _compute_analytics_metrics raises 400 for invalid 'since'
  AC7  — _compute_analytics_metrics raises 400 for invalid 'until'
  AC8  — _compute_calibration raises 400 for invalid 'since'
  AC9  — _compute_calibration raises 400 for invalid 'until'
  AC10 — Inline strptime+ValueError+HTTPException blocks removed from both callers
         (implementation uses _parse_iso_date, not duplicated inline logic)
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"

for _p in (str(_DASHBOARD_ROOT),):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# AC1 — helper is importable
# ---------------------------------------------------------------------------

class TestAC1Importable:
    def test_parse_iso_date_importable(self):
        from server import _parse_iso_date
        assert callable(_parse_iso_date)


# ---------------------------------------------------------------------------
# AC2 — returns midnight UTC for valid YYYY-MM-DD
# ---------------------------------------------------------------------------

class TestAC2ValidDate:
    def test_returns_datetime(self):
        from server import _parse_iso_date
        result = _parse_iso_date("2026-01-15", "since")
        assert isinstance(result, datetime)

    def test_timezone_is_utc(self):
        from server import _parse_iso_date
        result = _parse_iso_date("2026-01-15", "since")
        assert result.tzinfo is not None
        assert result.utcoffset().total_seconds() == 0

    def test_midnight_time(self):
        from server import _parse_iso_date
        result = _parse_iso_date("2026-01-15", "since")
        assert result.hour == 0
        assert result.minute == 0
        assert result.second == 0

    def test_date_components(self):
        from server import _parse_iso_date
        result = _parse_iso_date("2026-03-22", "until")
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 22


# ---------------------------------------------------------------------------
# AC3 — raises HTTPException(400) for invalid date string
# ---------------------------------------------------------------------------

class TestAC3InvalidDate:
    def test_raises_http_exception_for_garbage(self):
        from server import _parse_iso_date
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _parse_iso_date("not-a-date", "since")
        assert exc_info.value.status_code == 400

    def test_raises_for_wrong_format(self):
        from server import _parse_iso_date
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _parse_iso_date("15/01/2026", "until")
        assert exc_info.value.status_code == 400

    def test_raises_for_partial_date(self):
        from server import _parse_iso_date
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _parse_iso_date("2026-01", "since")
        assert exc_info.value.status_code == 400

    def test_raises_for_empty_string(self):
        from server import _parse_iso_date
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _parse_iso_date("", "since")
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# AC4 — error detail contains the param name and the bad value
# ---------------------------------------------------------------------------

class TestAC4ErrorDetail:
    def test_detail_contains_param_name(self):
        from server import _parse_iso_date
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _parse_iso_date("bad-val", "since")
        assert "'since'" in exc_info.value.detail

    def test_detail_contains_bad_value(self):
        from server import _parse_iso_date
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _parse_iso_date("oops", "until")
        assert "'oops'" in exc_info.value.detail

    def test_detail_mentions_expected_format(self):
        from server import _parse_iso_date
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _parse_iso_date("2026/01/15", "since")
        assert "YYYY-MM-DD" in exc_info.value.detail


# ---------------------------------------------------------------------------
# AC5 — end_of_day=True returns 23:59:59
# ---------------------------------------------------------------------------

class TestAC5EndOfDay:
    def test_end_of_day_time(self):
        from server import _parse_iso_date
        result = _parse_iso_date("2026-01-15", "until", end_of_day=True)
        assert result.hour == 23
        assert result.minute == 59
        assert result.second == 59

    def test_end_of_day_preserves_date(self):
        from server import _parse_iso_date
        result = _parse_iso_date("2026-03-22", "until", end_of_day=True)
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 22

    def test_end_of_day_false_is_default(self):
        from server import _parse_iso_date
        result = _parse_iso_date("2026-01-15", "since")
        assert result.hour == 0
        assert result.second == 0


# ---------------------------------------------------------------------------
# AC6 — _compute_analytics_metrics raises 400 for invalid 'since'
# ---------------------------------------------------------------------------

class TestAC6AnalyticsMetricsBadSince:
    def test_bad_since_raises_400(self, tmp_path):
        from fastapi import HTTPException
        with patch("server._settings_repo") as mock_sr, \
             patch("server._sprint_repo") as mock_spr:
            from server import _compute_analytics_metrics
            with pytest.raises(HTTPException) as exc_info:
                _compute_analytics_metrics(tmp_path, since="not-a-date")
        assert exc_info.value.status_code == 400
        assert "YYYY-MM-DD" in exc_info.value.detail

    def test_bad_since_detail_has_field_name(self, tmp_path):
        from fastapi import HTTPException
        with patch("server._settings_repo"), patch("server._sprint_repo"):
            from server import _compute_analytics_metrics
            with pytest.raises(HTTPException) as exc_info:
                _compute_analytics_metrics(tmp_path, since="bad")
        assert "'since'" in exc_info.value.detail


# ---------------------------------------------------------------------------
# AC7 — _compute_analytics_metrics raises 400 for invalid 'until'
# ---------------------------------------------------------------------------

class TestAC7AnalyticsMetricsBadUntil:
    def test_bad_until_raises_400(self, tmp_path):
        from fastapi import HTTPException
        with patch("server._settings_repo"), patch("server._sprint_repo"):
            from server import _compute_analytics_metrics
            with pytest.raises(HTTPException) as exc_info:
                _compute_analytics_metrics(tmp_path, until="not-a-date")
        assert exc_info.value.status_code == 400
        assert "YYYY-MM-DD" in exc_info.value.detail

    def test_bad_until_detail_has_field_name(self, tmp_path):
        from fastapi import HTTPException
        with patch("server._settings_repo"), patch("server._sprint_repo"):
            from server import _compute_analytics_metrics
            with pytest.raises(HTTPException) as exc_info:
                _compute_analytics_metrics(tmp_path, until="oops")
        assert "'until'" in exc_info.value.detail


# ---------------------------------------------------------------------------
# AC8 — _compute_calibration raises 400 for invalid 'since'
# ---------------------------------------------------------------------------

class TestAC8CalibrationBadSince:
    def test_bad_since_raises_400(self):
        from fastapi import HTTPException
        with patch("server._settings_repo") as mock_sr, \
             patch("server._sprint_repo") as mock_spr, \
             patch("server.build_effective_response", return_value={}):
            mock_sr.get_setting.return_value = {}
            from server import _compute_calibration
            with pytest.raises(HTTPException) as exc_info:
                _compute_calibration("test/repo", since="bad-date")
        assert exc_info.value.status_code == 400
        assert "YYYY-MM-DD" in exc_info.value.detail

    def test_bad_since_detail_has_field_name(self):
        from fastapi import HTTPException
        with patch("server._settings_repo") as mock_sr, \
             patch("server._sprint_repo"), \
             patch("server.build_effective_response", return_value={}):
            mock_sr.get_setting.return_value = {}
            from server import _compute_calibration
            with pytest.raises(HTTPException) as exc_info:
                _compute_calibration("test/repo", since="bad")
        assert "'since'" in exc_info.value.detail


# ---------------------------------------------------------------------------
# AC9 — _compute_calibration raises 400 for invalid 'until'
# ---------------------------------------------------------------------------

class TestAC9CalibrationBadUntil:
    def test_bad_until_raises_400(self):
        from fastapi import HTTPException
        with patch("server._settings_repo") as mock_sr, \
             patch("server._sprint_repo"), \
             patch("server.build_effective_response", return_value={}):
            mock_sr.get_setting.return_value = {}
            from server import _compute_calibration
            with pytest.raises(HTTPException) as exc_info:
                _compute_calibration("test/repo", until="bad-date")
        assert exc_info.value.status_code == 400
        assert "YYYY-MM-DD" in exc_info.value.detail

    def test_bad_until_detail_has_field_name(self):
        from fastapi import HTTPException
        with patch("server._settings_repo") as mock_sr, \
             patch("server._sprint_repo"), \
             patch("server.build_effective_response", return_value={}):
            mock_sr.get_setting.return_value = {}
            from server import _compute_calibration
            with pytest.raises(HTTPException) as exc_info:
                _compute_calibration("test/repo", until="oops")
        assert "'until'" in exc_info.value.detail


# ---------------------------------------------------------------------------
# AC10 — inline strptime+ValueError+HTTPException blocks removed from callers
# ---------------------------------------------------------------------------

class TestAC10NoDuplicateInlineCode:
    """Verify that _parse_iso_date is the single point of date validation.

    Checks the source of both callers to confirm the inline try/except blocks
    were replaced, not merely shadowed.
    """

    def _get_function_source(self, func_name: str) -> str:
        import inspect
        import server as srv
        fn = getattr(srv, func_name)
        return inspect.getsource(fn)

    def test_analytics_metrics_no_inline_strptime(self):
        src = self._get_function_source("_compute_analytics_metrics")
        assert "datetime.strptime" not in src, (
            "_compute_analytics_metrics still contains inline datetime.strptime; "
            "should delegate to _parse_iso_date"
        )

    def test_calibration_no_inline_strptime(self):
        src = self._get_function_source("_compute_calibration")
        assert "datetime.strptime" not in src, (
            "_compute_calibration still contains inline datetime.strptime; "
            "should delegate to _parse_iso_date"
        )

    def test_analytics_metrics_calls_parse_iso_date(self):
        src = self._get_function_source("_compute_analytics_metrics")
        assert "_parse_iso_date" in src, (
            "_compute_analytics_metrics does not call _parse_iso_date"
        )

    def test_calibration_calls_parse_iso_date(self):
        src = self._get_function_source("_compute_calibration")
        assert "_parse_iso_date" in src, (
            "_compute_calibration does not call _parse_iso_date"
        )
