"""
Tests for issue #648: Build Metrics tab with ANL-3 data cards.

Acceptance criteria tested:
- AC1: Metrics tab renders inside ANL-2 page shell, reachable via tab nav
- AC2: First-pass rate card — rate value + proportional bar fill
- AC3: Rework rate card — rate value, bar fill, "N tickets needed 2+ rounds" note
- AC4: Avg duration by agent — coder/tester rows; coder includes size breakdown
- AC5: Throughput card — tickets-per-sprint + avg sprint length
- AC6: Cost per sprint card — total + role breakdown
- AC7: Cost per ticket card — avg cost + rework-cost annotation
- AC8: metric-card CSS pattern used throughout
- AC9: Data-source footnote below card grid
- AC10: Scope selector change re-fetches ANL-3 and updates all cards
- AC11: Empty state — friendly message when ANL-3 returns no data
- AC12: No stale data after scope change
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_DASHBOARD_ROOT = Path(__file__).parent.parent / "apps" / "dashboard"
sys.path.insert(0, str(_DASHBOARD_ROOT))

_STATIC_DIR = _DASHBOARD_ROOT / "static"
_ANALYTICS_HTML = _STATIC_DIR / "analytics.html"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sprint_state(tmp: Path, sprint_label: str, issues: list[dict],
                       wall_clock_secs: float = 1200.0,
                       start_timestamp: str = "2026-01-01T10:00:00Z") -> Path:
    commander = tmp / ".commander"
    sprints_dir = commander / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)

    import re
    m = re.search(r"(\d+)", sprint_label)
    n = m.group(1) if m else sprint_label

    state = {
        "sprint_label": sprint_label,
        "sprint_number": int(n),
        "project": "test/repo",
        "start_timestamp": start_timestamp,
        "wall_clock_secs": wall_clock_secs,
        "issues": issues,
        "total_tokens_in": 0,
        "total_tokens_out": 0,
    }
    (sprints_dir / f"sprint-{n}-state.json").write_text(json.dumps(state), encoding="utf-8")
    return tmp


def _make_estimate(project_root: Path, issue_num: int, size: str) -> None:
    est_dir = project_root / ".commander" / "estimates"
    est_dir.mkdir(parents=True, exist_ok=True)
    est = {"issue_number": issue_num, "size": size}
    (est_dir / f"issue-{issue_num}.json").write_text(json.dumps(est), encoding="utf-8")


_ISSUE_DONE_FIRST_PASS = {
    "number": 101,
    "title": "Feature A",
    "status": "done",
    "coder_started_at": "2026-01-01T10:00:00Z",
    "coder_finished_at": "2026-01-01T10:10:00Z",
    "tester_started_at": "2026-01-01T10:10:00Z",
    "tester_finished_at": "2026-01-01T10:16:00Z",
    "tester_attempt_count": 1,
}

_ISSUE_DONE_REWORK = {
    "number": 102,
    "title": "Feature B",
    "status": "done",
    "coder_started_at": "2026-01-01T11:00:00Z",
    "coder_finished_at": "2026-01-01T11:20:00Z",
    "tester_started_at": "2026-01-01T11:20:00Z",
    "tester_finished_at": "2026-01-01T11:26:00Z",
    "tester_attempt_count": 2,
}

_ISSUE_DONE_HEAVY_REWORK = {
    "number": 103,
    "title": "Feature C",
    "status": "done",
    "coder_started_at": "2026-01-01T12:00:00Z",
    "coder_finished_at": "2026-01-01T12:30:00Z",
    "tester_started_at": "2026-01-01T12:30:00Z",
    "tester_finished_at": "2026-01-01T12:36:00Z",
    "tester_attempt_count": 3,
}


# ---------------------------------------------------------------------------
# AC1+AC8+AC9: HTML structure of Metrics tab
# ---------------------------------------------------------------------------

class TestAnalyticsHtmlStructure:
    """AC1: Metrics tab renders inside page shell, reachable via tab nav.
       AC8: metric-card CSS pattern used.
       AC9: Data-source footnote below card grid.
    """

    def test_analytics_html_exists(self):
        assert _ANALYTICS_HTML.exists(), "analytics.html must exist in static/"

    def test_metrics_tab_panel_present(self):
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert 'id="panel-metrics"' in html or 'id="pane-metrics"' in html, \
            "analytics.html must have a metrics tab panel"

    def test_tab_navigation_present(self):
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "Calibration" in html, "Tab bar must include Calibration tab"
        assert "Metrics" in html, "Tab bar must include Metrics tab"

    def test_metric_card_css_pattern(self):
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "metric-card" in html or "metric " in html, \
            "analytics.html must use metric-card CSS pattern from mock"

    def test_six_metric_cards_present(self):
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        # Check for all six card labels
        assert "First-pass rate" in html or "first-pass" in html.lower()
        assert "Rework rate" in html or "rework" in html.lower()
        assert "duration" in html.lower() or "Duration" in html
        assert "Throughput" in html or "throughput" in html
        assert "Cost" in html or "cost" in html.lower()

    def test_data_source_footnote_present(self):
        """AC9: footnote line appears below card grid."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "src-note" in html or "source" in html.lower() or "footnote" in html.lower() or \
               "data-source" in html.lower() or "sources:" in html.lower(), \
            "analytics.html must have a data-source footnote"

    def test_bar_fill_elements_present(self):
        """AC2+AC3: bar fill width elements for rate cards."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert '<div class="bar"' in html or 'class="bar' in html, \
            "Rate cards must include bar fill elements"

    def test_scope_selector_present(self):
        """AC10: scope selector (date range or sprint) is in the page."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "scope" in html.lower() or "date" in html.lower() or "sprint" in html.lower(), \
            "Page must include a scope selector"

    def test_empty_state_message_present(self):
        """AC11: empty state message for when no data."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert (
            "no data" in html.lower()
            or "No data" in html
            or "empty" in html.lower()
            or "empty-state" in html
        ), "analytics.html must include empty-state messaging"

    def test_js_fetch_metrics_function(self):
        """AC10: JS function that fetches ANL-3 and re-renders on scope change."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert (
            "fetchMetrics" in html
            or "fetch_metrics" in html
            or "loadMetrics" in html
            or "metricsLoad" in html
        ), "JS must define a function to fetch metrics from ANL-3"

    def test_js_scope_change_calls_fetch(self):
        """AC10: Scope selector change triggers a metrics re-fetch."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert (
            "analytics/metrics" in html
            or "/analytics/metrics" in html
        ), "JS must call the /api/projects/.../analytics/metrics endpoint"

    def test_rework_2plus_note_in_template(self):
        """AC3: 'N tickets needed 2+ rounds' note present in markup."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "2+" in html or "2-plus" in html or "rounds" in html or "needed" in html, \
            "Rework card must reference 2+ rounds note"

    def test_size_breakdown_in_markup(self):
        """AC4: coder size breakdown (S/M/L/XL) reference in markup."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "breakdown" in html or ("S:" in html or "size" in html.lower()), \
            "Duration card must include size breakdown markup"

    def test_responsive_layout_meta(self):
        """AC: viewport meta for mobile readability."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert 'name="viewport"' in html, "Page must include viewport meta for responsive layout"


# ---------------------------------------------------------------------------
# AC2–AC7 + AC11: Backend API endpoint
# ---------------------------------------------------------------------------

class TestAnalyticsMetricsApiShape:
    """Tests for GET /api/projects/{slug}/analytics/metrics shape and values."""

    def _call_metrics(self, project_root: Path, slug: str = "repo",
                      since: str | None = None, until: str | None = None,
                      sprint: str | None = None):
        from server import get_project_analytics_metrics, _project_root_path
        from starlette.testclient import TestClient
        from server import app

        qs: dict = {}
        if since:
            qs["since"] = since
        if until:
            qs["until"] = until
        if sprint:
            qs["sprint"] = sprint

        with (
            patch("server._resolve_project_slug", return_value="test/repo"),
            patch("server._project_root_path", return_value=project_root),
        ):
            client = TestClient(app)
            url = f"/api/projects/{slug}/analytics/metrics"
            resp = client.get(url, params=qs)
        return resp

    def test_returns_200(self, tmp_path):
        """AC: endpoint returns HTTP 200 with full payload."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_DONE_FIRST_PASS])
        resp = self._call_metrics(tmp_path)
        assert resp.status_code == 200

    def test_response_has_all_top_level_keys(self, tmp_path):
        """AC2–AC7: all six metric sections present."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_DONE_FIRST_PASS])
        resp = self._call_metrics(tmp_path)
        data = resp.json()
        for key in ("first_pass_rate", "rework_rate", "avg_duration", "throughput", "cost"):
            assert key in data, f"Response missing key: {key}"

    def test_first_pass_rate_fields(self, tmp_path):
        """AC2: first_pass_rate has rate, passed count, total_completed."""
        issues = [_ISSUE_DONE_FIRST_PASS, _ISSUE_DONE_REWORK]
        _make_sprint_state(tmp_path, "sprint-1", issues)
        resp = self._call_metrics(tmp_path)
        fpr = resp.json()["first_pass_rate"]
        assert "rate" in fpr
        assert "passed" in fpr
        assert "total_completed" in fpr
        assert 0.0 <= fpr["rate"] <= 1.0
        assert isinstance(fpr["passed"], int)
        assert isinstance(fpr["total_completed"], int)

    def test_first_pass_rate_calculation(self, tmp_path):
        """AC2: 2 done tickets, 1 first-pass → rate = 0.5."""
        issues = [_ISSUE_DONE_FIRST_PASS, _ISSUE_DONE_REWORK]
        _make_sprint_state(tmp_path, "sprint-1", issues)
        resp = self._call_metrics(tmp_path)
        fpr = resp.json()["first_pass_rate"]
        assert fpr["total_completed"] == 2
        assert fpr["passed"] == 1
        assert abs(fpr["rate"] - 0.5) < 0.01

    def test_rework_rate_fields(self, tmp_path):
        """AC3: rework_rate has rate, count, rework_2plus, total."""
        issues = [_ISSUE_DONE_FIRST_PASS, _ISSUE_DONE_REWORK, _ISSUE_DONE_HEAVY_REWORK]
        _make_sprint_state(tmp_path, "sprint-1", issues)
        resp = self._call_metrics(tmp_path)
        rwr = resp.json()["rework_rate"]
        assert "rate" in rwr
        assert "count" in rwr
        assert "rework_2plus" in rwr
        assert "total" in rwr

    def test_rework_rate_calculation(self, tmp_path):
        """AC3: 3 done, 2 reworked (>=1 rejection), 1 needed 2+ rounds."""
        issues = [_ISSUE_DONE_FIRST_PASS, _ISSUE_DONE_REWORK, _ISSUE_DONE_HEAVY_REWORK]
        _make_sprint_state(tmp_path, "sprint-1", issues)
        resp = self._call_metrics(tmp_path)
        rwr = resp.json()["rework_rate"]
        assert rwr["total"] == 3
        assert rwr["count"] == 2   # attempt_count > 1
        assert rwr["rework_2plus"] == 1   # attempt_count >= 3
        assert abs(rwr["rate"] - 2/3) < 0.01

    def test_first_pass_plus_rework_reconcile(self, tmp_path):
        """AC: first_pass.passed + rework.count == total_completed."""
        issues = [_ISSUE_DONE_FIRST_PASS, _ISSUE_DONE_REWORK, _ISSUE_DONE_HEAVY_REWORK]
        _make_sprint_state(tmp_path, "sprint-1", issues)
        resp = self._call_metrics(tmp_path)
        data = resp.json()
        fpr = data["first_pass_rate"]
        rwr = data["rework_rate"]
        assert fpr["passed"] + rwr["count"] == fpr["total_completed"]

    def test_avg_duration_fields(self, tmp_path):
        """AC4: avg_duration has coder_minutes, tester_minutes, coder_by_size."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_DONE_FIRST_PASS])
        resp = self._call_metrics(tmp_path)
        dur = resp.json()["avg_duration"]
        assert "coder_minutes" in dur
        assert "tester_minutes" in dur
        assert "coder_by_size" in dur
        assert isinstance(dur["coder_by_size"], dict)

    def test_avg_duration_coder_calculation(self, tmp_path):
        """AC4: coder duration = 10 min (10:00 → 10:10)."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_DONE_FIRST_PASS])
        _make_estimate(tmp_path, 101, "M")
        resp = self._call_metrics(tmp_path)
        dur = resp.json()["avg_duration"]
        assert abs(dur["coder_minutes"] - 10.0) < 0.1

    def test_avg_duration_tester_calculation(self, tmp_path):
        """AC4: tester duration = 6 min (10:10 → 10:16)."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_DONE_FIRST_PASS])
        resp = self._call_metrics(tmp_path)
        dur = resp.json()["avg_duration"]
        assert abs(dur["tester_minutes"] - 6.0) < 0.1

    def test_avg_duration_by_size_populated(self, tmp_path):
        """AC4: coder_by_size populated when estimates present."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_DONE_FIRST_PASS])
        _make_estimate(tmp_path, 101, "M")
        resp = self._call_metrics(tmp_path)
        dur = resp.json()["avg_duration"]
        assert "M" in dur["coder_by_size"]
        assert abs(dur["coder_by_size"]["M"] - 10.0) < 0.1

    def test_throughput_fields(self, tmp_path):
        """AC5: throughput has avg_tickets_per_sprint and avg_sprint_length_minutes."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_DONE_FIRST_PASS])
        resp = self._call_metrics(tmp_path)
        thr = resp.json()["throughput"]
        assert "avg_tickets_per_sprint" in thr
        assert "avg_sprint_length_minutes" in thr

    def test_throughput_calculation(self, tmp_path):
        """AC5: 1 sprint with 1 done ticket → avg_tickets_per_sprint=1."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_DONE_FIRST_PASS],
                           wall_clock_secs=1200.0)
        resp = self._call_metrics(tmp_path)
        thr = resp.json()["throughput"]
        assert abs(thr["avg_tickets_per_sprint"] - 1.0) < 0.01
        assert abs(thr["avg_sprint_length_minutes"] - 20.0) < 0.1

    def test_cost_fields(self, tmp_path):
        """AC6+AC7: cost has per_sprint (with role breakdown) and per_ticket (with rework_annotation)."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_DONE_FIRST_PASS])
        resp = self._call_metrics(tmp_path)
        cost = resp.json()["cost"]
        assert "per_sprint" in cost
        assert "per_ticket" in cost
        ps = cost["per_sprint"]
        assert "total" in ps
        assert "by_role" in ps
        pt = cost["per_ticket"]
        assert "avg" in pt
        assert "rework_cost_annotation" in pt

    def test_cost_by_role_has_agent_keys(self, tmp_path):
        """AC6: role breakdown includes coder, tester, estimator keys."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_DONE_FIRST_PASS])
        resp = self._call_metrics(tmp_path)
        by_role = resp.json()["cost"]["per_sprint"]["by_role"]
        assert "coder" in by_role
        assert "tester" in by_role
        assert "estimator" in by_role

    def test_empty_data_returns_zeros_not_null(self, tmp_path):
        """AC11: no sprint state files → all numeric fields are 0, not null."""
        # tmp_path has no state files
        resp = self._call_metrics(tmp_path)
        assert resp.status_code == 200
        data = resp.json()
        fpr = data["first_pass_rate"]
        assert fpr["rate"] == 0
        assert fpr["passed"] == 0
        assert fpr["total_completed"] == 0
        rwr = data["rework_rate"]
        assert rwr["rate"] == 0
        thr = data["throughput"]
        assert thr["avg_tickets_per_sprint"] == 0
        cost = data["cost"]
        assert cost["per_sprint"]["total"] == 0
        assert cost["per_ticket"]["avg"] == 0

    def test_unknown_slug_returns_404(self, tmp_path):
        """AC: unknown project slug → HTTP 404."""
        from server import app
        from starlette.testclient import TestClient

        with patch("server._resolve_project_slug",
                   side_effect=__import__("fastapi").HTTPException(
                       status_code=404, detail="Project 'bad-slug' not found"
                   )):
            client = TestClient(app)
            resp = client.get("/api/projects/bad-slug/analytics/metrics")
        assert resp.status_code == 404

    def test_scope_since_filter(self, tmp_path):
        """AC12: since filter excludes sprints before the date."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_DONE_FIRST_PASS],
                           start_timestamp="2025-01-01T10:00:00Z")
        _make_sprint_state(tmp_path, "sprint-2", [_ISSUE_DONE_REWORK],
                           start_timestamp="2026-06-01T10:00:00Z")
        resp = self._call_metrics(tmp_path, since="2026-01-01")
        data = resp.json()
        assert data["first_pass_rate"]["total_completed"] == 1

    def test_model_price_map_constant_exists(self):
        """AC: MODEL_PRICE_MAP is defined in server.py."""
        import server
        assert hasattr(server, "MODEL_PRICE_MAP"), \
            "server.py must define MODEL_PRICE_MAP constant"
        assert isinstance(server.MODEL_PRICE_MAP, dict)
        assert len(server.MODEL_PRICE_MAP) > 0
