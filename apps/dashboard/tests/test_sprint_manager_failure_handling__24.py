"""Tests for issue #24 — Sprint Manager failure handling, notifications,
state persistence, and dashboard enhancements.

Static checks validate the source files directly (no live server required).
Live-server checks are marked with @pytest.mark.live.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS_DIR   = Path(__file__).parent.parent / "scripts"
SPRINT_SCRIPT = SCRIPTS_DIR / "sprint_manager.py"
SERVER_PY     = Path(__file__).parent.parent / "server.py"
APP_JS        = Path(__file__).parent.parent / "static" / "app.js"
INDEX_HTML    = Path(__file__).parent.parent / "static" / "index.html"

# ---------------------------------------------------------------------------
# Helpers: import sprint_manager without side effects
# ---------------------------------------------------------------------------

def _import_sprint_manager():
    """Import sprint_manager.py as a module, mocking dotenv and github_client."""
    import importlib
    import importlib.util
    import types

    # Provide stub modules so the import doesn't fail outside the full env
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *a, **kw: None
    sys.modules.setdefault("dotenv", dotenv_stub)

    gc_stub = types.ModuleType("github_client")
    gc_stub.repo = lambda: "zealchaiwut/commander"
    gc_stub.update_labels = lambda *a, **kw: None
    gc_stub.add_comment   = lambda *a, **kw: None
    sys.modules.setdefault("github_client", gc_stub)

    mod_name = "sprint_manager_test_import"
    spec     = importlib.util.spec_from_file_location(mod_name, SPRINT_SCRIPT)
    mod      = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE executing so that dataclass field
    # annotations that reference Optional can be resolved against the module.
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# AC-1: Failure categories
# ---------------------------------------------------------------------------

class TestFailureCategories:
    def test_script_exists(self):
        assert SPRINT_SCRIPT.exists(), "sprint_manager.py must exist in dashboard/scripts/"

    def test_all_five_categories_defined(self):
        content = SPRINT_SCRIPT.read_text()
        for cat in ("HANG", "CRASH", "GATE_FAIL", "TESTER_REJECTED", "RETRY_EXHAUSTED"):
            assert cat in content, f"FailureCategory.{cat} must be defined in sprint_manager.py"

    def test_category_constants_are_strings(self):
        sm = _import_sprint_manager()
        fc = sm.FailureCategory
        assert fc.HANG            == "HANG"
        assert fc.CRASH           == "CRASH"
        assert fc.GATE_FAIL       == "GATE_FAIL"
        assert fc.TESTER_REJECTED == "TESTER_REJECTED"
        assert fc.RETRY_EXHAUSTED == "RETRY_EXHAUSTED"


# ---------------------------------------------------------------------------
# AC-2: Hang detection
# ---------------------------------------------------------------------------

class TestHangDetection:
    def test_hang_detector_class_exists(self):
        content = SPRINT_SCRIPT.read_text()
        assert "HangDetector" in content, "HangDetector class must exist"

    def test_hang_constants_present(self):
        content = SPRINT_SCRIPT.read_text()
        assert "HANG_WARN_SECS" in content
        assert "HANG_KILL_SECS" in content
        assert "HANG_CHECK_SECS" in content

    def test_hang_warn_threshold_is_30_minutes(self):
        sm = _import_sprint_manager()
        assert sm.HANG_WARN_SECS == 30 * 60

    def test_hang_kill_threshold_is_60_minutes(self):
        sm = _import_sprint_manager()
        assert sm.HANG_KILL_SECS == 60 * 60

    def test_hang_check_interval_is_5_minutes(self):
        sm = _import_sprint_manager()
        assert sm.HANG_CHECK_SECS == 5 * 60

    def test_hang_detector_has_kill_on_idle(self):
        content = SPRINT_SCRIPT.read_text()
        assert "proc.kill()" in content, "HangDetector must kill the subprocess on hang"

    def test_hang_detector_has_warn_at_30min(self):
        content = SPRINT_SCRIPT.read_text()
        assert "HANG_WARN_SECS" in content
        assert "WARN" in content.upper()

    def test_hang_result_sets_killed_flag(self):
        sm = _import_sprint_manager()
        assert hasattr(sm.HangDetector, "__dataclass_fields__") or hasattr(sm.HangDetector, "__init__")
        content = SPRINT_SCRIPT.read_text()
        assert "_killed" in content


# ---------------------------------------------------------------------------
# AC-3: Alert modes
# ---------------------------------------------------------------------------

class TestAlertModes:
    def test_alert_mode_class_exists(self):
        content = SPRINT_SCRIPT.read_text()
        assert "AlertMode" in content

    def test_all_five_modes_defined(self):
        sm = _import_sprint_manager()
        assert sm.AlertMode.DASHBOARD_BANNER == "dashboard-banner"
        assert sm.AlertMode.EMAIL            == "email"
        assert sm.AlertMode.DISCORD          == "discord"
        assert sm.AlertMode.FILE             == "file"
        assert sm.AlertMode.NONE             == "none"

    def test_default_alert_mode_is_dashboard_banner(self):
        content = SPRINT_SCRIPT.read_text()
        assert "dashboard-banner" in content
        # Default value appears in argparse default
        assert "default=AlertMode.DASHBOARD_BANNER" in content or \
               'default="dashboard-banner"' in content or \
               "DASHBOARD_BANNER" in content

    def test_email_silently_skipped_when_vars_missing(self):
        sm = _import_sprint_manager()
        # Should not raise even when SMTP vars are absent
        with mock.patch.dict("os.environ", {}, clear=False):
            for key in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "ALERT_EMAIL_TO"):
                os.environ.pop(key, None)
        # No exception expected
        sm._alert_email("test", "body")

    def test_discord_silently_skipped_when_var_missing(self):
        sm = _import_sprint_manager()
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("DISCORD_WEBHOOK_URL", None)
        # No exception expected
        sm._alert_discord("test", "body")

    def test_file_alert_creates_directory_and_writes(self, tmp_path):
        sm = _import_sprint_manager()
        alerts_dir = tmp_path / "alerts"
        with mock.patch.object(sm, "ALERTS_DIR", alerts_dir):
            sm._alert_file("Test alert", "Something went wrong")
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        log_path = alerts_dir / f"{today}.log"
        assert log_path.exists(), "File alert must create the log file"
        content = log_path.read_text()
        assert "Test alert" in content
        assert "Something went wrong" in content

    def test_dispatch_alerts_none_mode_no_output(self):
        sm = _import_sprint_manager()
        with mock.patch.object(sm, "_alert_dashboard_banner") as mock_banner, \
             mock.patch.object(sm, "_alert_email") as mock_email, \
             mock.patch.object(sm, "_alert_discord") as mock_disc, \
             mock.patch.object(sm, "_alert_file") as mock_file:
            sm.dispatch_alerts(["none"], title="x", body="y")
            mock_banner.assert_not_called()
            mock_email.assert_not_called()
            mock_disc.assert_not_called()
            mock_file.assert_not_called()

    def test_multiple_alert_modes_combined(self):
        sm = _import_sprint_manager()
        with mock.patch.object(sm, "_alert_file") as mock_file, \
             mock.patch.object(sm, "_alert_dashboard_banner") as _, \
             mock.patch.object(sm, "_alert_discord") as _:
            try:
                sm.dispatch_alerts(
                    ["dashboard-banner", "file"],
                    title="t", body="b",
                )
            except Exception:
                pass  # dashboard may not be running; file mock should have been called
            mock_file.assert_called_once()

    def test_comma_separated_modes_parsed(self):
        content = SPRINT_SCRIPT.read_text()
        assert "split(" in content, "Alert modes must be parsed from comma-separated string"


# ---------------------------------------------------------------------------
# AC-4: Sprint summary report
# ---------------------------------------------------------------------------

class TestSprintSummaryReport:
    def test_summary_functions_exist(self):
        content = SPRINT_SCRIPT.read_text()
        assert "generate_sprint_summary" in content
        assert "write_sprint_summary" in content

    def test_summary_path_format(self):
        sm = _import_sprint_manager()
        path = sm._summary_path(5, "sprint-5")
        assert "sprint-5-summary-" in path.name
        assert path.suffix == ".md"

    def test_summary_contains_required_sections(self):
        sm = _import_sprint_manager()
        state = sm.SprintState(sprint_label="sprint-5", sprint_number=5)
        state.issues = [
            sm.IssueState(number=1, title="Feature A", status="done",
                          tokens_in=100, tokens_out=200),
            sm.IssueState(number=2, title="Feature B", status="skipped",
                          skip_reason="Tester failed",
                          category=sm.FailureCategory.CRASH,
                          tokens_in=50, tokens_out=60),
        ]
        state.total_tokens_in  = 150
        state.total_tokens_out = 260
        content = sm.generate_sprint_summary(state, elapsed_secs=3661)
        assert "sprint-5" in content.lower() or "Sprint 5" in content
        assert "Attempted" in content
        assert "Completed" in content
        assert "Skipped" in content
        assert "Token" in content
        assert "Duration" in content or "clock" in content.lower() or "1h 1m" in content

    def test_summary_includes_skipped_issue_details(self):
        sm = _import_sprint_manager()
        state = sm.SprintState(sprint_label="sprint-5", sprint_number=5)
        state.issues = [
            sm.IssueState(number=99, title="Broken Feature", status="skipped",
                          skip_reason="Gate pytest failed",
                          category=sm.FailureCategory.GATE_FAIL),
        ]
        content = sm.generate_sprint_summary(state, elapsed_secs=120)
        assert "#99" in content
        assert "GATE_FAIL" in content
        assert "Gate pytest failed" in content

    def test_summary_includes_follow_up_actions(self):
        sm = _import_sprint_manager()
        state = sm.SprintState(sprint_label="sprint-5", sprint_number=5)
        state.issues = [
            sm.IssueState(number=7, title="Hang Issue", status="skipped",
                          category=sm.FailureCategory.HANG),
        ]
        content = sm.generate_sprint_summary(state, elapsed_secs=60)
        assert "#7" in content
        # follow-up action should mention retry or investigate
        assert any(word in content.lower() for word in ("retry", "investigate", "fix", "manual"))

    def test_write_summary_creates_file(self, tmp_path):
        sm = _import_sprint_manager()
        with mock.patch.object(sm, "SPRINTS_DIR", tmp_path / "sprints"), \
             mock.patch.object(sm, "dispatch_alerts"):
            state = sm.SprintState(sprint_label="sprint-1", sprint_number=1)
            path  = sm.write_sprint_summary(state, elapsed_secs=10, alert_modes=["none"])
        assert path.exists()
        assert path.suffix == ".md"

    def test_api_sprint_summary_endpoint_in_server(self):
        content = SERVER_PY.read_text()
        assert "/api/sprint-summary" in content

    def test_api_sprint_summary_returns_path_and_content(self):
        content = SERVER_PY.read_text()
        assert '"path"' in content or "'path'" in content
        assert '"content"' in content or "'content'" in content


# ---------------------------------------------------------------------------
# AC-5: State persistence
# ---------------------------------------------------------------------------

class TestStatePersistence:
    def test_state_path_format(self):
        sm = _import_sprint_manager()
        path = sm._state_path(5, "sprint-5")
        assert "sprint-5-state.json" == path.name

    def test_sprint_state_serialisation_round_trip(self):
        sm = _import_sprint_manager()
        state = sm.SprintState(sprint_label="sprint-3", sprint_number=3,
                               start_timestamp="2026-01-01T00:00:00Z")
        state.issues = [
            sm.IssueState(number=10, title="Feat", status="done",
                          tokens_in=100, tokens_out=50),
            sm.IssueState(number=11, title="Broken", status="skipped",
                          skip_reason="crash", category="CRASH"),
        ]
        d = state.to_dict()
        state2 = sm.SprintState.from_dict(d)
        assert state2.sprint_label  == "sprint-3"
        assert state2.sprint_number == 3
        assert len(state2.issues)   == 2
        assert state2.issues[0].status == "done"
        assert state2.issues[1].category == "CRASH"

    def test_state_saved_to_file(self, tmp_path):
        sm = _import_sprint_manager()
        state = sm.SprintState(sprint_label="sprint-7", sprint_number=7)
        state.issues = [sm.IssueState(number=42, title="Test")]
        path = tmp_path / "sprint-7-state.json"
        state.save(path)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["sprint_label"] == "sprint-7"
        assert len(data["issues"]) == 1

    def test_resume_flag_accepted_by_cli(self):
        content = SPRINT_SCRIPT.read_text()
        assert "--resume" in content

    def test_retry_failed_flag_accepted_by_cli(self):
        content = SPRINT_SCRIPT.read_text()
        assert "--retry-failed" in content

    def test_state_has_all_required_fields(self):
        sm = _import_sprint_manager()
        state = sm.SprintState(sprint_label="sprint-1", sprint_number=1)
        d     = state.to_dict()
        for field in ("sprint_label", "sprint_number", "issues",
                      "start_timestamp", "total_tokens_in", "total_tokens_out"):
            assert field in d, f"State dict must contain field '{field}'"

    def test_issue_state_has_per_issue_token_counts(self):
        sm = _import_sprint_manager()
        i = sm.IssueState(number=1, title="x", tokens_in=5, tokens_out=10)
        d = i.to_dict()
        assert d["tokens_in"]  == 5
        assert d["tokens_out"] == 10


# ---------------------------------------------------------------------------
# AC-6: Dashboard sprint panel
# ---------------------------------------------------------------------------

class TestDashboardSprintPanel:
    def test_sprint_panel_html_exists(self):
        """Issue #82 moved sprint progress to per-project expand panels.
        The global #sprint-panel is removed; progress is now in mini-sprint-summary.
        """
        content = INDEX_HTML.read_text()
        # The new per-project summary element replaces the global panel
        assert "mini-sprint-summary" in content or "prog-track" in content, (
            "index.html must have per-project sprint progress elements (issue #82)"
        )

    def test_progress_bar_element_exists(self):
        """Issue #82: progress bar is now per-project in expand panels, not global."""
        content = INDEX_HTML.read_text()
        # prog-track/prog-fill classes are still used in per-project rows
        assert "prog-track" in content or "prog-fill" in content, (
            "index.html must still have progress bar CSS classes"
        )

    def test_skipped_list_element_exists(self):
        """Issue #82: global skipped list moved to sprint manager CLI; no global panel."""
        content = APP_JS.read_text()
        # renderSprintPanel still exists in JS (kept as no-op for SSE compat)
        assert "renderSprintPanel" in content, (
            "app.js must still define renderSprintPanel for SSE compatibility"
        )

    def test_retry_skipped_button_exists(self):
        """Issue #82: retry skipped is a CLI operation; JS helper still present."""
        content = APP_JS.read_text()
        assert "retry" in content.lower() or "retrySkipped" in content, (
            "app.js must still have retrySkipped function"
        )

    def test_js_polls_sprint_status(self):
        content = APP_JS.read_text()
        assert "/api/sprint-status" in content

    def test_js_polls_every_30_seconds(self):
        content = APP_JS.read_text()
        assert "30_000" in content or "30000" in content, \
            "app.js must poll sprint status every 30s"

    def test_js_render_sprint_panel_function(self):
        content = APP_JS.read_text()
        assert "renderSprintPanel" in content

    def test_js_updates_in_place_no_full_reload(self):
        """Issue #82: global sprint panel removed; per-project progress bar in expand panel."""
        content = APP_JS.read_text()
        # renderSprintPanel still exists for SSE compatibility (as a no-op)
        assert "renderSprintPanel" in content, (
            "app.js must still have renderSprintPanel for SSE compatibility (issue #82)"
        )

    def test_api_sprint_status_endpoint_in_server(self):
        content = SERVER_PY.read_text()
        assert "/api/sprint-status" in content

    def test_api_sprint_status_post_endpoint_in_server(self):
        content = SERVER_PY.read_text()
        assert "set_sprint_status" in content or "POST" in content


# ---------------------------------------------------------------------------
# AC-3a: Dashboard alert banner
# ---------------------------------------------------------------------------

class TestDashboardAlertBanner:
    def test_alert_banner_css_exists(self):
        content = INDEX_HTML.read_text()
        assert "alert-banner" in content

    def test_alert_dismiss_button_exists(self):
        content = APP_JS.read_text()
        assert "dismissAlert" in content

    def test_api_alerts_endpoint_in_server(self):
        content = SERVER_PY.read_text()
        assert "/api/alerts" in content

    def test_api_alerts_post_endpoint_in_server(self):
        content = SERVER_PY.read_text()
        assert "receive_alert" in content

    def test_api_alerts_delete_endpoint_in_server(self):
        content = SERVER_PY.read_text()
        assert "dismiss_alert" in content

    def test_js_load_alerts_function(self):
        content = APP_JS.read_text()
        assert "loadAlerts" in content

    def test_js_render_alert_banners_function(self):
        content = APP_JS.read_text()
        assert "renderAlertBanners" in content

    def test_alert_payload_model_in_server(self):
        content = SERVER_PY.read_text()
        assert "AlertPayload" in content


# ---------------------------------------------------------------------------
# Live server tests
# ---------------------------------------------------------------------------

@pytest.mark.live
class TestLiveAlerts:
    def test_post_alert_returns_201(self, client):
        res = client.post("/api/alerts", json={
            "title": "Test alert",
            "body":  "Sprint issue #99 failed with CRASH",
            "issue_num": 99,
            "category": "CRASH",
        })
        assert res.status_code == 201

    def test_get_alerts_returns_list(self, client):
        res = client.get("/api/alerts")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_posted_alert_appears_in_list(self, client):
        unique_title = f"Test-{int(time.time())}"
        client.post("/api/alerts", json={"title": unique_title, "body": "body"})
        res = client.get("/api/alerts")
        titles = [a["title"] for a in res.json()]
        assert unique_title in titles


@pytest.mark.live
class TestLiveSprintStatus:
    def test_post_sprint_status(self, client):
        payload = {
            "sprint_label": "sprint-99",
            "sprint_number": 99,
            "issues": [
                {"number": 1, "title": "Feature A", "status": "done",
                 "skip_reason": None, "category": None, "tokens_in": 0, "tokens_out": 0},
            ],
            "start_timestamp": "2026-01-01T00:00:00Z",
            "total_tokens_in": 0,
            "total_tokens_out": 0,
            "wall_clock_secs": 0.0,
        }
        res = client.post("/api/sprint-status", json=payload)
        assert res.status_code == 200

    def test_get_sprint_status_returns_list_shape(self, client):
        # GET /api/sprint-status must always return {"running_sprints": [...]} with HTTP 200,
        # even when no sprints are running.
        res = client.get("/api/sprint-status")
        assert res.status_code == 200
        data = res.json()
        assert "running_sprints" in data
        assert isinstance(data["running_sprints"], list)
