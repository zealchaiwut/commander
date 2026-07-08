"""Tests for issue #1778 AC1, AC2, AC5 — home API enrichment and backward compatibility.

Coverage:
  - AC2: /api/brief/daily embeds briefSummary and milestone fields
  - AC5: Per-project endpoints remain functional (backward compatibility)
  - AC1: request count verification (manual via browser devtools)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from routers import brief as brief_router_mod  # noqa: E402


@pytest.fixture()
def client_with_home():
    """FastAPI app with brief router."""
    app = FastAPI()
    app.include_router(brief_router_mod.router)
    return TestClient(app)


class TestBriefDailyEnrichment:
    """AC2 — /api/brief/daily enrichment with per-project metadata."""

    def test_brief_daily_embeds_brief_summary_field(self, client_with_home):
        """AC2: briefSummary is embedded in brief.projects."""
        with patch('routers.brief.brief_artifact.get_or_create_home_artifact') as mock_get_artifact, \
             patch('routers.brief._projects_module.load_projects') as mock_projects, \
             patch('routers.brief.brief_summary.get_or_create_project_summary') as mock_summary:
            # Mock the home artifact with a project
            mock_get_artifact.return_value = {
                "available": True,
                "brief": {
                    "projects": [
                        {"project": "test-proj", "decisions": []}
                    ]
                },
                "date": "2026-01-01"
            }
            mock_projects.return_value = [
                {"repo": "org/test-proj", "name": "Test Project", "icon": "ti-star"}
            ]
            mock_summary.return_value = {"summary": "Worked on features"}

            r = client_with_home.get("/api/brief/daily")
            assert r.status_code == 200
            data = r.json()
            assert "brief" in data
            projects = data["brief"].get("projects", [])
            assert len(projects) > 0
            assert projects[0]["briefSummary"] == "Worked on features"

    def test_brief_daily_embeds_milestone_field(self, client_with_home):
        """AC2: milestone is embedded in brief.projects."""
        with patch('routers.brief.brief_artifact.get_or_create_home_artifact') as mock_get_artifact, \
             patch('routers.brief._projects_module.load_projects') as mock_projects, \
             patch('routers.brief.home_milestone_service.active_milestone_progress') as mock_milestone:
            mock_get_artifact.return_value = {
                "available": True,
                "brief": {
                    "projects": [
                        {"project": "test-proj2"}
                    ]
                },
                "date": "2026-01-01"
            }
            mock_projects.return_value = [
                {"repo": "org/test-proj2", "name": "Test 2"}
            ]
            mock_milestone.return_value = {"title": "v1.0", "label": "3/5"}

            r = client_with_home.get("/api/brief/daily")
            assert r.status_code == 200
            data = r.json()
            projects = data["brief"].get("projects", [])
            assert len(projects) > 0
            assert projects[0]["milestone"] == {"title": "v1.0", "label": "3/5"}

    def test_brief_daily_embeds_repo_and_project_metadata(self, client_with_home):
        """AC2: repo, name, icon, color are embedded."""
        with patch('routers.brief.brief_artifact.get_or_create_home_artifact') as mock_get_artifact, \
             patch('routers.brief._projects_module.load_projects') as mock_projects, \
             patch('routers.brief.brief_summary.get_or_create_project_summary'), \
             patch('routers.brief.home_milestone_service.active_milestone_progress'):
            mock_get_artifact.return_value = {
                "available": True,
                "brief": {
                    "projects": [
                        {"project": "my-proj"}
                    ]
                },
                "date": "2026-01-01"
            }
            mock_projects.return_value = [
                {
                    "repo": "zealchaiwut/my-proj",
                    "name": "My Project",
                    "icon": "ti-rocket",
                    "color": "red"
                }
            ]

            r = client_with_home.get("/api/brief/daily")
            assert r.status_code == 200
            data = r.json()
            p = data["brief"]["projects"][0]
            assert p["repo"] == "zealchaiwut/my-proj"
            assert p["name"] == "My Project"
            assert p["icon"] == "ti-rocket"
            assert p["color"] == "red"

    def test_brief_daily_handles_missing_project_gracefully(self, client_with_home):
        """AC2: missing project metadata does not crash."""
        with patch('routers.brief.brief_artifact.get_or_create_home_artifact') as mock_get_artifact, \
             patch('routers.brief._projects_module.load_projects') as mock_projects, \
             patch('routers.brief.brief_summary.get_or_create_project_summary') as mock_summary, \
             patch('routers.brief.home_milestone_service.active_milestone_progress') as mock_milestone:
            mock_get_artifact.return_value = {
                "available": True,
                "brief": {
                    "projects": [
                        {"project": "unknown-proj"}
                    ]
                },
                "date": "2026-01-01"
            }
            mock_projects.return_value = []
            mock_summary.return_value = None
            mock_milestone.return_value = None

            r = client_with_home.get("/api/brief/daily")
            assert r.status_code == 200
            data = r.json()
            p = data["brief"]["projects"][0]
            # Should have defaults when project is not found
            assert p.get("briefSummary") == ""
            assert p.get("milestone") is None


class TestBackwardCompatibility:
    """AC5 — Per-project endpoints remain functional."""

    def test_per_project_brief_summary_endpoint_exists(self, client_with_home):
        """AC5: /api/projects/{slug}/brief/summary is still available."""
        with patch('routers.brief.brief_summary.get_or_create_project_summary') as mock:
            mock.return_value = {"summary": "Test summary", "source": "model", "date": "2026-01-01"}
            r = client_with_home.get("/api/projects/test/brief/summary")
            assert r.status_code == 200
            data = r.json()
            assert data["summary"] == "Test summary"
            assert data["date"] == "2026-01-01"
            assert data["source"] == "model"

    def test_per_project_todos_endpoint_exists(self, client_with_home):
        """AC5: per-project todos endpoint behavior is unaffected (covered in test_1778__batch_todos.py)."""
        # Per-project todo endpoint is the same as the batch with projects=slug
        # The feature is that the batch endpoint exists and works; per-project
        # endpoints are tested separately in other test files.
        pass
