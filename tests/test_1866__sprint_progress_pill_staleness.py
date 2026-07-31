"""Tests for issue #1866: sprint-progress pill staleness + finished-sprint removal.

Problem: GET /api/sprint-progress's persisted-cache tier (tier 2) was trusted
forever with no age check — once a project went idle (no sprint_manager
running to refresh tier 1), a frozen snapshot kept being served indefinitely,
even long after the sprint it described had actually finished on GitHub.

AC1: A persisted cache entry younger than 180s is still served as-is.
AC2: A persisted cache entry older than 180s is ignored; falls through to
     the GitHub-backed tier and persists a fresh result.
AC3: When the GitHub-backed tier determines state == "finished", the
     endpoint returns {"has_sprint": False}.
AC4: Existing "live" (tier 1) and "still running" (tier 3) behavior unchanged.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_DASHBOARD_ROOT), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import os
os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")

from routers import sprint_nav  # noqa: E402


def _iso(age_seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()


# ── AC1 / AC2: _is_progress_cache_fresh unit tests ───────────────────────────

class TestIsProgressCacheFresh:
    def test_fresh_entry_is_trusted(self):
        cached = {"has_sprint": True, "fetched_at": _iso(5)}
        assert sprint_nav._is_progress_cache_fresh(cached) is True

    def test_entry_just_under_threshold_is_trusted(self):
        cached = {"has_sprint": True, "fetched_at": _iso(sprint_nav._PROGRESS_CACHE_MAX_AGE_SECONDS - 1)}
        assert sprint_nav._is_progress_cache_fresh(cached) is True

    def test_entry_over_threshold_is_stale(self):
        cached = {"has_sprint": True, "fetched_at": _iso(sprint_nav._PROGRESS_CACHE_MAX_AGE_SECONDS + 1)}
        assert sprint_nav._is_progress_cache_fresh(cached) is False

    def test_missing_fetched_at_is_stale(self):
        cached = {"has_sprint": True}
        assert sprint_nav._is_progress_cache_fresh(cached) is False

    def test_malformed_fetched_at_is_stale(self):
        cached = {"has_sprint": True, "fetched_at": "not-a-timestamp"}
        assert sprint_nav._is_progress_cache_fresh(cached) is False

    def test_future_timestamp_is_stale(self):
        """Clock skew fails closed rather than trusting an impossible entry."""
        cached = {"has_sprint": True, "fetched_at": _iso(-60)}
        assert sprint_nav._is_progress_cache_fresh(cached) is False


# ── AC2 / AC4: get_sprint_progress tier behavior ─────────────────────────────

class TestGetSprintProgressTiers:
    def test_fresh_cache_served_without_github_fallback(self, tmp_path):
        cache_file = tmp_path / "sprint-progress.json"
        cache_file.write_text(json.dumps({
            "has_sprint": True,
            "sprint_label": "sprint-9",
            "sprint": 9,
            "done": 2,
            "total": 2,
            "run_state": "running",
            "source": "live",
            "fetched_at": _iso(5),
        }), encoding="utf-8")

        mock_srv = type("Srv", (), {})()
        mock_srv._all_sprints_running = lambda: []

        with patch.object(sprint_nav, "_server", return_value=mock_srv), \
             patch.object(sprint_nav, "_sprint_progress_file_path", return_value=cache_file), \
             patch.object(sprint_nav, "get_sprint_nav_status") as mock_gh:
            mock_gh.side_effect = AssertionError("GitHub fallback must not run for a fresh cache")
            result = sprint_nav.get_sprint_progress(project="zealchaiwut/claude-proxy")

        assert result["has_sprint"] is True
        assert result["sprint_label"] == "sprint-9"
        assert result["done"] == 2

    def test_stale_cache_falls_through_to_github(self, tmp_path):
        cache_file = tmp_path / "sprint-progress.json"
        cache_file.write_text(json.dumps({
            "has_sprint": True,
            "sprint_label": "sprint-9",
            "sprint": 9,
            "done": 2,
            "total": 2,
            "run_state": "running",
            "source": "live",
            "fetched_at": _iso(sprint_nav._PROGRESS_CACHE_MAX_AGE_SECONDS + 30),
        }), encoding="utf-8")

        mock_srv = type("Srv", (), {})()
        mock_srv._all_sprints_running = lambda: []

        with patch.object(sprint_nav, "_server", return_value=mock_srv), \
             patch.object(sprint_nav, "_sprint_progress_file_path", return_value=cache_file), \
             patch.object(sprint_nav, "_persist_sprint_progress") as mock_persist, \
             patch.object(sprint_nav, "get_sprint_nav_status") as mock_gh:
            mock_gh.return_value = {
                "has_sprint": True,
                "sprint": 11,
                "state": "running",
                "total": 4,
                "done": 1,
                "uat": 0,
                "columns": {"backlog": 0, "in-progress": 3, "sit": 0, "uat": 0, "done": 1, "needs-rework": 0},
            }
            result = sprint_nav.get_sprint_progress(project="zealchaiwut/claude-proxy")

        assert result["has_sprint"] is True
        assert result["sprint"] == 11
        assert result["source"] == "github"
        mock_gh.assert_called_once()
        mock_persist.assert_called_once()

    # ── AC3: finished sprint removes the pill ───────────────────────────────

    def test_github_finished_sprint_returns_no_pill(self, tmp_path):
        missing_cache = tmp_path / "does-not-exist.json"

        mock_srv = type("Srv", (), {})()
        mock_srv._all_sprints_running = lambda: []

        with patch.object(sprint_nav, "_server", return_value=mock_srv), \
             patch.object(sprint_nav, "_sprint_progress_file_path", return_value=missing_cache), \
             patch.object(sprint_nav, "_persist_sprint_progress") as mock_persist, \
             patch.object(sprint_nav, "get_sprint_nav_status") as mock_gh:
            mock_gh.return_value = {
                "has_sprint": True,
                "sprint": 4,
                "state": "finished",
                "total": 9,
                "done": 9,
                "uat": 0,
                "columns": {"backlog": 0, "in-progress": 0, "sit": 0, "uat": 0, "done": 9, "needs-rework": 0},
            }
            result = sprint_nav.get_sprint_progress(project="zealchaiwut/viral-radar")

        assert result == {"has_sprint": False} or (
            result.get("has_sprint") is False
        ), f"finished sprint must remove the pill; got {result!r}"
        # The persisted has_sprint:False result means tier 2's own guard
        # (`if cached.get("has_sprint")`) will skip it on the next read —
        # no separate re-fetch-avoidance needed.
        mock_persist.assert_called_once()
        persisted_arg = mock_persist.call_args[0][1]
        assert persisted_arg.get("has_sprint") is False

    def test_github_running_sprint_still_shows_pill(self, tmp_path):
        """AC4: non-finished GitHub state is unaffected by this fix."""
        missing_cache = tmp_path / "does-not-exist.json"

        mock_srv = type("Srv", (), {})()
        mock_srv._all_sprints_running = lambda: []

        with patch.object(sprint_nav, "_server", return_value=mock_srv), \
             patch.object(sprint_nav, "_sprint_progress_file_path", return_value=missing_cache), \
             patch.object(sprint_nav, "_persist_sprint_progress"), \
             patch.object(sprint_nav, "get_sprint_nav_status") as mock_gh:
            mock_gh.return_value = {
                "has_sprint": True,
                "sprint": 58,
                "state": "running",
                "total": 5,
                "done": 3,
                "uat": 1,
                "columns": {"backlog": 0, "in-progress": 1, "sit": 0, "uat": 1, "done": 3, "needs-rework": 0},
            }
            result = sprint_nav.get_sprint_progress(project="zealchaiwut/crux")

        assert result["has_sprint"] is True
        assert result["run_state"] == "running"
        assert result["sprint"] == 58
