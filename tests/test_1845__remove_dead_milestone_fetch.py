"""Tests for issue #1845: remove dead milestone fetch/render from Home.

Coverage:
  AC2: milestone enrichment removed from _enrich_home_artifact / brief router
  AC4: zero milestone-related service calls during /api/brief/daily processing
       (Python-side behavioral spy — complements the frontend fetch-spy test)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from routers import brief as brief_router_mod  # noqa: E402
from routers.brief import _enrich_home_artifact  # noqa: E402


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(brief_router_mod.router)
    return TestClient(app)


def _home_artifact(projects):
    return {
        "available": True,
        "brief": {"projects": projects},
        "date": "2026-01-01",
    }


# ---------------------------------------------------------------------------
# AC2: milestone enrichment removed from _enrich_home_artifact
# ---------------------------------------------------------------------------

class TestMilestoneEnrichmentRemoved:
    def test_enrich_does_not_call_active_milestone_progress(self):
        """_enrich_home_artifact must never call active_milestone_progress (AC2)."""
        artifact = _home_artifact([{"project": "test-proj"}])
        spy = MagicMock(side_effect=AssertionError("active_milestone_progress must not be called"))
        with patch("routers.brief._projects_module.load_projects", return_value=[
            {"repo": "org/test-proj", "name": "Test", "icon": "ti-folder", "color": "gray"}
        ]):
            with patch("routers.brief.brief_summary.get_or_create_project_summary",
                       return_value={"summary": ""}):
                try:
                    with patch("routers.brief.home_milestone_service.active_milestone_progress", spy):
                        _enrich_home_artifact(artifact)
                except AttributeError:
                    # home_milestone_service import removed from brief.py — the call is gone
                    pass
        spy.assert_not_called()

    def test_enrich_does_not_set_milestone_key_on_projects(self):
        """_enrich_home_artifact must not embed a 'milestone' key in project entries (AC2)."""
        artifact = _home_artifact([{"project": "proj-a"}])
        with patch("routers.brief._projects_module.load_projects", return_value=[
            {"repo": "org/proj-a", "name": "Proj A"}
        ]):
            with patch("routers.brief.brief_summary.get_or_create_project_summary",
                       return_value={"summary": ""}):
                try:
                    with patch("routers.brief.home_milestone_service.active_milestone_progress",
                               return_value={"title": "v1", "label": "2/5"}):
                        _enrich_home_artifact(artifact)
                except AttributeError:
                    pass
        proj = artifact["brief"]["projects"][0]
        assert "milestone" not in proj, (
            "'milestone' key must not appear in project entries after enrichment"
        )

    def test_enrich_with_multiple_projects_no_milestone_key(self):
        """No project entry should have a 'milestone' key after enrichment (AC2)."""
        artifact = _home_artifact([
            {"project": "alpha"},
            {"project": "beta"},
        ])
        projects_cfg = [
            {"repo": "org/alpha", "name": "Alpha"},
            {"repo": "org/beta", "name": "Beta"},
        ]
        with patch("routers.brief._projects_module.load_projects", return_value=projects_cfg):
            with patch("routers.brief.brief_summary.get_or_create_project_summary",
                       return_value={"summary": ""}):
                try:
                    with patch("routers.brief.home_milestone_service.active_milestone_progress",
                               return_value={"title": "m", "label": "1/2"}):
                        _enrich_home_artifact(artifact)
                except AttributeError:
                    pass
        for proj in artifact["brief"]["projects"]:
            assert "milestone" not in proj, (
                f"Project {proj.get('project')} must not have a 'milestone' key"
            )


# ---------------------------------------------------------------------------
# AC4: /api/brief/daily makes zero milestone service calls
# ---------------------------------------------------------------------------

class TestBriefDailyZeroMilestoneCalls:
    def test_api_brief_daily_does_not_call_milestone_service(self, client):
        """GET /api/brief/daily must not trigger active_milestone_progress (AC4)."""
        with patch("routers.brief.brief_artifact.get_or_create_home_artifact") as mock_artifact, \
             patch("routers.brief._projects_module.load_projects") as mock_projects, \
             patch("routers.brief.brief_summary.get_or_create_project_summary",
                   return_value={"summary": "summary text"}):
            mock_artifact.return_value = _home_artifact([{"project": "p1"}])
            mock_projects.return_value = [{"repo": "org/p1", "name": "P1"}]

            milestone_spy = MagicMock(side_effect=AssertionError(
                "active_milestone_progress must not be called during /api/brief/daily"
            ))
            try:
                with patch("routers.brief.home_milestone_service.active_milestone_progress",
                           milestone_spy):
                    resp = client.get("/api/brief/daily")
            except AttributeError:
                # home_milestone_service removed from brief — call cannot happen
                resp = client.get("/api/brief/daily")

        assert resp.status_code == 200
        milestone_spy.assert_not_called()

    def test_api_brief_daily_response_has_no_milestone_field(self, client):
        """GET /api/brief/daily response must not include 'milestone' in project entries (AC4)."""
        with patch("routers.brief.brief_artifact.get_or_create_home_artifact") as mock_artifact, \
             patch("routers.brief._projects_module.load_projects") as mock_projects, \
             patch("routers.brief.brief_summary.get_or_create_project_summary",
                   return_value={"summary": ""}):
            mock_artifact.return_value = _home_artifact([{"project": "q1"}])
            mock_projects.return_value = [{"repo": "org/q1", "name": "Q1"}]
            try:
                with patch("routers.brief.home_milestone_service.active_milestone_progress",
                           return_value={"title": "v2", "label": "1/3"}):
                    resp = client.get("/api/brief/daily")
            except AttributeError:
                resp = client.get("/api/brief/daily")

        assert resp.status_code == 200
        data = resp.json()
        projects = data.get("brief", {}).get("projects", [])
        assert len(projects) > 0
        for proj in projects:
            assert "milestone" not in proj, (
                f"Response project {proj.get('project')} must not contain 'milestone' field"
            )
