"""Tests for issue #1844 — Home AI summary enrichment must respect
brief_disabled() gate.

AC1  When brief_disabled() is True, _enrich_home_artifact() skips
     get_or_create_project_summary() and the project card falls back to the
     deterministic status line (briefSummary is empty string, not LLM text).
AC2  When brief_disabled() is False, get_or_create_project_summary() is called
     as before (behaviour unchanged).
AC3  Behavioral TestClient test: with the disable flag set,
     GET /api/brief/daily
     must not invoke the summary generator (call_count == 0).
"""
from __future__ import annotations

import copy
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

# Wire up a throw-away SQLite DB before importing any dashboard modules.
_TMPDIR = tempfile.mkdtemp(prefix="test_1844_")
os.environ.setdefault("DB_PATH", str(Path(_TMPDIR) / "test_1844.db"))

if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))


# ── helpers ──────────────────────────────────────────────────────────────────

_FAKE_ARTIFACT = {
    "available": True,
    "date": "2026-07-12",
    "brief": {
        "projects": [{"project": "commander"}],
        "kpis": {
            "sprints_shipped": 0, "tickets_done": 0,
            "in_progress": 0, "needs_your_call": 0,
        },
        "decisions": [],
        "covering_since": "2026-07-01",
    },
}

_FAKE_PROJECTS = [
    {
        "repo": "zealchaiwut/commander",
        "name": "Commander",
        "icon": "ti-folder",
        "color": "blue",
    }
]


def _get_brief_router_module():
    """Return the brief module object as used by the running server
    (routers.brief)."""
    # DASHBOARD_DIR is on sys.path, so server imports routers.brief
    # (not apps.dashboard.routers.brief)
    if "routers.brief" in sys.modules:
        return sys.modules["routers.brief"]
    import routers.brief  # noqa: PLC0415
    return routers.brief


# ── AC1: brief_disabled=True → summary skipped, briefSummary == "" ───────────

def test_ac1_summary_skipped_when_brief_disabled():
    """_enrich_home_artifact must not call get_or_create_project_summary
    when disabled."""
    brief_router = _get_brief_router_module()

    summary_mock = MagicMock(return_value={"summary": "AI-generated text"})

    # Patch on the router module object so the already-resolved reference in
    # _enrich_home_artifact sees the mock (patching the source module directly
    # would leave the router's bound reference unchanged).
    with patch.object(brief_router, "config") as mock_cfg, \
         patch.object(brief_router, "brief_summary") as mock_bs, \
         patch.object(brief_router, "_projects_module") as mock_proj:

        mock_cfg.brief_disabled.return_value = True
        mock_bs.get_or_create_project_summary = summary_mock
        mock_proj.load_projects.return_value = _FAKE_PROJECTS

        artifact = copy.deepcopy(_FAKE_ARTIFACT)
        brief_router._enrich_home_artifact(artifact)

    # The LLM generator must not have been called.
    assert summary_mock.call_count == 0, (
        "get_or_create_project_summary was called despite"
        " brief_disabled()=True"
    )
    # The project entry must carry an empty briefSummary
    # (deterministic fallback).
    project_entry = artifact["brief"]["projects"][0]
    assert project_entry.get("briefSummary") == "", (
        "project briefSummary should be empty string when brief is disabled"
    )


# ── AC2: brief_disabled=False → summary IS called (behaviour unchanged) ──────

def test_ac2_summary_called_when_brief_enabled():
    """_enrich_home_artifact must still call get_or_create_project_summary
    when enabled."""
    brief_router = _get_brief_router_module()

    summary_mock = MagicMock(return_value={"summary": "AI text"})

    with patch.object(brief_router, "config") as mock_cfg, \
         patch.object(brief_router, "brief_summary") as mock_bs, \
         patch.object(brief_router, "_projects_module") as mock_proj:

        mock_cfg.brief_disabled.return_value = False
        mock_bs.get_or_create_project_summary = summary_mock
        mock_proj.load_projects.return_value = _FAKE_PROJECTS

        artifact = copy.deepcopy(_FAKE_ARTIFACT)
        brief_router._enrich_home_artifact(artifact)

    # The LLM generator must have been called exactly once (one project).
    assert summary_mock.call_count == 1, (
        "get_or_create_project_summary should be called"
        " when brief_disabled()=False"
    )


# ── AC3: TestClient behavioral test ──────────────────────────────────────────

def test_ac3_testclient_brief_disabled_does_not_invoke_summary():
    """GET /api/brief/daily with brief_disabled=True must not call
    the summary generator."""
    import server as srv  # noqa: PLC0415

    brief_router = _get_brief_router_module()

    summary_mock = MagicMock(return_value={"summary": "should not appear"})

    with patch.object(brief_router, "config") as mock_cfg, \
         patch.object(brief_router, "brief_summary") as mock_bs, \
         patch.object(brief_router, "brief_artifact") as mock_ba, \
         patch.object(brief_router, "_projects_module") as mock_proj:

        mock_cfg.brief_disabled.return_value = True
        mock_bs.get_or_create_project_summary = summary_mock
        mock_ba.get_or_create_home_artifact.return_value = copy.deepcopy(
            _FAKE_ARTIFACT)
        mock_proj.load_projects.return_value = _FAKE_PROJECTS

        from fastapi.testclient import TestClient  # noqa: PLC0415
        client = TestClient(srv.app, raise_server_exceptions=False)
        resp = client.get("/api/brief/daily")

    assert resp.status_code == 200, (
        f"Expected 200, got {resp.status_code}: {resp.text}"
    )
    assert summary_mock.call_count == 0, (
        f"get_or_create_project_summary was called "
        f"{summary_mock.call_count} time(s) despite brief_disabled()=True\n"
        "gate is missing in _enrich_home_artifact"
    )
