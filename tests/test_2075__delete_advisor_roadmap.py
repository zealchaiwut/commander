"""Tests for issue #2075: delete Advisor and Roadmap (dead code removal).

Each test class maps 1:1 to an Acceptance Criterion (AC1–AC7).
"""
import importlib
import sys
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).parent.parent
APP_DIR = REPO_ROOT / "apps" / "dashboard"
HTML = (APP_DIR / "static" / "project.html").read_text()
FEATURES_JS = (APP_DIR / "static" / "src" / "shell" / "features.js").read_text()
TABS_JS = (APP_DIR / "static" / "src" / "shell" / "tabs.js").read_text()


# ---------------------------------------------------------------------------
# AC1: Advisor router module and service are deleted
# ---------------------------------------------------------------------------
class TestAdvisorRouterDeleted:
    def test_advisor_py_does_not_exist(self):
        assert not (APP_DIR / "routers" / "advisor.py").exists()

    def test_advisor_service_py_does_not_exist(self):
        assert not (APP_DIR / "routers" / "advisor_service.py").exists()

    def test_suggestions_py_does_not_exist(self):
        assert not (APP_DIR / "routers" / "suggestions.py").exists()

    def test_suggestions_service_py_does_not_exist(self):
        assert not (APP_DIR / "routers" / "suggestions_service.py").exists()

    def test_advisor_not_importable(self):
        """importlib must raise ModuleNotFoundError, not succeed."""
        if "routers.advisor" in sys.modules:
            del sys.modules["routers.advisor"]
        with pytest.raises((ModuleNotFoundError, ImportError)):
            importlib.import_module("routers.advisor")

    def test_advisor_not_in_routers_init_all(self):
        src = (APP_DIR / "routers" / "__init__.py").read_text()
        assert "advisor_router" not in src

    def test_suggestions_not_in_routers_init_all(self):
        src = (APP_DIR / "routers" / "__init__.py").read_text()
        # The bare suggestions module import must be gone (xl_suggestions_router is unrelated)
        assert "from .suggestions import" not in src

    def test_advisor_not_in_server_imports(self):
        src = (APP_DIR / "server.py").read_text()
        assert "advisor_router" not in src

    def test_suggestions_not_in_server_imports(self):
        src = (APP_DIR / "server.py").read_text()
        # The bare suggestions_router (not xl_suggestions_router) must be gone
        assert "    suggestions_router," not in src

    def test_advisor_endpoint_returns_404(self):
        sys.path.insert(0, str(APP_DIR))
        from server import app
        client = TestClient(app)
        r = client.get("/api/advisor/suggestions?repo=owner/repo")
        assert r.status_code == 404

    def test_suggestions_endpoint_returns_404(self):
        sys.path.insert(0, str(APP_DIR))
        from server import app
        client = TestClient(app)
        r = client.get("/api/suggestions?repo=owner/repo")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# AC2: Advisor markup and CSS removed from project.html
# ---------------------------------------------------------------------------
class TestAdvisorMarkupGone:
    def test_pane_advisor_element_absent(self):
        assert 'id="pane-advisor"' not in HTML

    def test_stab_advisor_button_absent(self):
        assert 'id="stab-advisor"' not in HTML

    def test_adv_run_btn_absent(self):
        assert "adv-run-btn" not in HTML

    def test_adv_css_class_absent(self):
        assert ".adv-header" not in HTML

    def test_advInit_call_absent(self):
        assert "advInit()" not in HTML

    def test_advInit_function_absent(self):
        assert "function advInit" not in HTML

    def test_sug_css_class_absent(self):
        assert ".sug-card" not in HTML

    def test_sugInit_function_absent(self):
        assert "function sugInit" not in HTML

    def test_disable_advisor_setting_absent(self):
        assert "disable_advisor" not in HTML

    def test_advisor_feature_flag_absent_from_features_js(self):
        assert "advisorEnabled" not in FEATURES_JS

    def test_advisor_not_in_features_type_hint(self):
        assert "advisor?" not in FEATURES_JS


# ---------------------------------------------------------------------------
# AC3: Roadmap ADR recorded
# ---------------------------------------------------------------------------
class TestRoadmapADRRecorded:
    def test_adr_file_exists(self):
        decisions_dir = REPO_ROOT / "docs" / "decisions"
        adrs = list(decisions_dir.glob("*delete-roadmap*"))
        assert len(adrs) >= 1, "No ADR file matching *delete-roadmap* found"

    def test_adr_mentions_roadmap_and_advisor(self):
        decisions_dir = REPO_ROOT / "docs" / "decisions"
        adrs = list(decisions_dir.glob("*delete-roadmap*"))
        text = adrs[0].read_text().lower()
        assert "roadmap" in text
        assert "advisor" in text

    def test_adr_status_is_decided(self):
        decisions_dir = REPO_ROOT / "docs" / "decisions"
        adrs = list(decisions_dir.glob("*delete-roadmap*"))
        text = adrs[0].read_text().lower()
        assert "decided" in text or "status: decided" in text


# ---------------------------------------------------------------------------
# AC4: Roadmap router module and service are deleted
# ---------------------------------------------------------------------------
class TestRoadmapRouterDeleted:
    def test_roadmap_py_does_not_exist(self):
        assert not (APP_DIR / "routers" / "roadmap.py").exists()

    def test_roadmap_service_py_does_not_exist(self):
        assert not (APP_DIR / "routers" / "roadmap_service.py").exists()

    def test_roadmap_not_importable(self):
        if "routers.roadmap" in sys.modules:
            del sys.modules["routers.roadmap"]
        with pytest.raises((ModuleNotFoundError, ImportError)):
            importlib.import_module("routers.roadmap")

    def test_roadmap_not_in_routers_init_all(self):
        src = (APP_DIR / "routers" / "__init__.py").read_text()
        assert "roadmap_router" not in src

    def test_roadmap_not_in_server_imports(self):
        src = (APP_DIR / "server.py").read_text()
        assert "roadmap_router" not in src

    def test_roadmap_endpoint_returns_404(self):
        sys.path.insert(0, str(APP_DIR))
        from server import app
        client = TestClient(app)
        r = client.get("/api/roadmap?repo=owner/repo")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# AC5: Roadmap markup and CSS removed from project.html
# ---------------------------------------------------------------------------
class TestRoadmapMarkupGone:
    def test_pane_roadmap_element_absent(self):
        assert 'id="pane-roadmap"' not in HTML

    def test_stab_roadmap_button_absent(self):
        assert 'id="stab-roadmap"' not in HTML

    def test_rm_card_css_absent(self):
        assert ".rm-card {" not in HTML

    def test_roadmapInit_call_absent(self):
        assert "roadmapInit()" not in HTML

    def test_roadmapInit_function_absent(self):
        assert "function roadmapInit" not in HTML

    def test_hnav_milestone_element_absent(self):
        assert 'id="hnav-milestone"' not in HTML

    def test_hnav_milestone_css_absent(self):
        assert ".hnav-milestone" not in HTML

    def test_milestone_refresh_function_absent(self):
        assert "_milestoneRefresh" not in HTML


# ---------------------------------------------------------------------------
# AC6: Planning nav group removed from project.html and tabs.js
# ---------------------------------------------------------------------------
class TestPlanningGroupRemoved:
    def test_stab_group_planning_absent_from_html(self):
        assert 'id="stab-group-planning"' not in HTML

    def test_planning_trigger_absent_from_html(self):
        assert 'id="stab-planning-trigger"' not in HTML

    def test_planning_not_in_group_children(self):
        assert '"planning"' not in TABS_JS or "planning:" not in TABS_JS

    def test_roadmap_not_in_top_level_tabs(self):
        assert '"roadmap"' not in TABS_JS

    def test_advisor_not_in_top_level_tabs(self):
        assert '"advisor"' not in TABS_JS

    def test_planning_not_in_roving_tabindex_array(self):
        assert '"planning"' not in TABS_JS

    def test_planning_trigger_suffix_removed(self):
        assert "planning-trigger" not in TABS_JS


# ---------------------------------------------------------------------------
# AC7: No orphaned integrations remain
# ---------------------------------------------------------------------------
class TestNoOrphanedIntegrations:
    def test_advisor_disabled_function_absent_from_config(self):
        src = (APP_DIR / "config.py").read_text()
        assert "advisor_disabled" not in src

    def test_advisor_not_in_commander_features(self):
        src = (APP_DIR / "config.py").read_text()
        assert '"advisor"' not in src

    def test_advisor_flag_absent_from_environment_response(self):
        """GET /api/environment features dict must not contain 'advisor'."""
        sys.path.insert(0, str(APP_DIR))
        from server import app
        client = TestClient(app)
        r = client.get("/api/environment")
        assert r.status_code == 200
        data = r.json()
        features = data.get("features", {})
        assert "advisor" not in features

    def test_commander_disable_advisor_absent_from_conftest(self):
        src = (REPO_ROOT / "tests" / "conftest.py").read_text()
        assert "COMMANDER_DISABLE_ADVISOR" not in src
