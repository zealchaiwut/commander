"""Tests for issue #859 — Wire Analytics Metrics, Status, and Trends to Real Data.

The Analytics tab lives in apps/dashboard/static/project.html as four sub-tabs
(Calibration | Metrics | Status | Trends). This issue removes any mock/placeholder
data path and makes all three subject views (Metrics, Status, Trends) source from
live data — sprint summary state files, the agent_runs table, and live label counts.

AC coverage:
  AC1 — Metrics view reads delivery health (avg duration, pass rate, rework rate)
        from sprint summary records; numbers reflect the real records, not mocks.
  AC2 — Status view derives ticket counts by status from live label counts
        (the issues array), with no hardcoded numbers.
  AC3 — Trends view renders throughput, avg ticket time, failure rate, and
        tokens-per-sprint sourced from agent_runs.total_tokens.
  AC4 — No hardcoded sample/mock values remain anywhere in the Metrics/Status/
        Trends data pipeline.
  AC5 — When no real data exists, each view renders a clearly labeled empty state
        rather than zeros or placeholder text.
  AC6 — Numbers are consistent with the sprint summary records / DB rows.
  AC7 — Real-data fetch paths for all three views are covered by tests.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
STATIC_DIR = DASHBOARD_DIR / "static"
PROJECT_HTML = STATIC_DIR / "project.html"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

import db as _db  # noqa: E402  same module key as server.py / routers.analytics


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def html() -> str:
    assert PROJECT_HTML.exists(), "project.html must exist"
    return PROJECT_HTML.read_text(encoding="utf-8")


def _trends_panel(html: str) -> str:
    """Return the HTML of the Trends sub-tab panel."""
    start = html.find('id="anl-panel-trends"')
    assert start >= 0, "anl-panel-trends must exist"
    end = html.find('id="anl-panel-', start + 1)
    if end < 0:
        end = start + 6000
    return html[start:end]


def _mk_db(tmp_path: Path) -> Path:
    """Create a minimal SQLite DB with agent_runs and token_usage tables."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE agent_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_number INTEGER NOT NULL,
            sprint_label TEXT NOT NULL,
            agent TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            duration_seconds INTEGER,
            outcome TEXT,
            total_tokens INTEGER,
            model_used TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            project TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            recorded_at TEXT NOT NULL,
            agent_role TEXT,
            model_name TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    return db_path


def _insert_run(db_path: Path, issue: int, sprint: str, agent: str,
                tokens: int | None, started_at: str = "2026-06-01T10:00:00") -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO agent_runs (issue_number, sprint_label, agent, started_at, "
        "total_tokens, model_used) VALUES (?, ?, ?, ?, ?, ?)",
        (issue, sprint, agent, started_at, tokens, "claude-sonnet-4-6"),
    )
    conn.commit()
    conn.close()


def _mk_sprint_state(project_root: Path, label: str, issues: list[dict],
                     start_ts: str = "2026-06-01T10:00:00",
                     wall_clock_secs: float = 3600.0) -> None:
    d = project_root / ".commander" / "sprints"
    d.mkdir(parents=True, exist_ok=True)
    state = {
        "sprint_label": label,
        "project": "owner/myrepo",
        "start_timestamp": start_ts,
        "wall_clock_secs": wall_clock_secs,
        "issues": issues,
    }
    (d / f"{label}-state.json").write_text(json.dumps(state), encoding="utf-8")


def _done_issue(num: int, *, attempts: int = 1,
                coder_min: float = 10.0, tester_min: float = 5.0) -> dict:
    return {
        "number": num,
        "status": "done",
        "tester_attempt_count": attempts,
        "coder_started_at": "2026-06-01T10:00:00",
        "coder_finished_at": f"2026-06-01T10:{int(coder_min):02d}:00",
        "tester_started_at": "2026-06-01T11:00:00",
        "tester_finished_at": f"2026-06-01T11:{int(tester_min):02d}:00",
    }


def _call_metrics(project_root: Path, db_path: Path,
                  slug: str = "myrepo", repo: str = "owner/myrepo",
                  query: str = ""):
    """Call GET /api/projects/{slug}/analytics/metrics via TestClient."""
    import server as srv
    from starlette.testclient import TestClient
    with (
        patch.object(_db, "DB_PATH", str(db_path)),
        patch.object(srv, "_resolve_project_slug", return_value=repo),
        patch.object(srv, "_project_root_path", return_value=project_root),
    ):
        client = TestClient(srv.app)
        return client.get(f"/api/projects/{slug}/analytics/metrics{query}")


def _call_cost(project_root: Path, db_path: Path,
               slug: str = "myrepo", repo: str = "owner/myrepo"):
    """Call GET /api/projects/{slug}/analytics/cost — the agent_runs token source
    consumed by the Trends tokens-per-sprint chart."""
    import server as srv  # ensures routers.analytics is imported
    from starlette.testclient import TestClient
    import routers.analytics as analytics_mod
    with (
        patch.object(_db, "DB_PATH", str(db_path)),
        patch.object(analytics_mod, "_resolve_project_slug", return_value=repo),
        patch.object(analytics_mod, "_project_root_path", return_value=project_root),
        patch.object(analytics_mod, "_settings_repo") as mock_sr,
    ):
        mock_sr.get_setting.return_value = {}
        client = TestClient(srv.app)
        return client.get(f"/api/projects/{slug}/analytics/cost")


# ---------------------------------------------------------------------------
# AC1 — Metrics view sources delivery health from real sprint records
# ---------------------------------------------------------------------------

class TestMetricsRealData:
    def test_pass_and_rework_rate_reflect_real_records(self, tmp_path):
        # 3 done tickets: 2 first-pass, 1 reworked → pass 2/3, rework 1/3.
        _mk_sprint_state(tmp_path, "sprint-1", [
            _done_issue(1, attempts=1),
            _done_issue(2, attempts=1),
            _done_issue(3, attempts=2),
        ])
        data = _call_metrics(tmp_path, _mk_db(tmp_path)).json()
        assert data["first_pass_rate"]["total_completed"] == 3
        assert data["first_pass_rate"]["passed"] == 2
        assert data["rework_rate"]["count"] == 1

    def test_avg_duration_reflects_real_timestamps(self, tmp_path):
        _mk_sprint_state(tmp_path, "sprint-1", [
            _done_issue(1, coder_min=20.0, tester_min=10.0),
        ])
        data = _call_metrics(tmp_path, _mk_db(tmp_path)).json()
        assert data["avg_duration"]["coder_minutes"] == pytest.approx(20.0, abs=0.5)
        assert data["avg_duration"]["tester_minutes"] == pytest.approx(10.0, abs=0.5)

    def test_throughput_reflects_done_count(self, tmp_path):
        _mk_sprint_state(tmp_path, "sprint-1", [
            _done_issue(1), _done_issue(2),
        ])
        data = _call_metrics(tmp_path, _mk_db(tmp_path)).json()
        assert data["throughput"]["avg_tickets_per_sprint"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# AC2 — Status view derives ticket counts by status from live label counts
# ---------------------------------------------------------------------------

class TestStatusLiveLabelCounts:
    def test_label_render_iterates_live_issues(self, html):
        """The label breakdown must be computed from the issues array, not baked in."""
        fn = _func_body(html, "_statusRenderLabels")
        assert "issues" in fn, "label render must read the live issues array"
        assert "counts[s]++" in fn or "counts[s] +=" in fn, \
            "counts must be tallied from each issue's status"

    def test_label_counts_start_at_zero_not_hardcoded(self, html):
        fn = _func_body(html, "_statusRenderLabels")
        m = re.search(r"counts\s*=\s*\{([^}]*)\}", fn)
        assert m, "a counts object must be initialised"
        # Every initial count must be 0 — no seeded/mock numbers.
        nums = re.findall(r":\s*(\d+)", m.group(1))
        assert nums and all(n == "0" for n in nums), \
            f"label counts must start at 0, found {nums}"

    def test_status_fetches_live_issues_endpoint(self, html):
        fn = _func_body(html, "statusRefresh")
        assert "/api/sprint-management/issues" in fn, \
            "Status must fetch live issues for label counts"


# ---------------------------------------------------------------------------
# AC3 — Trends view: tokens-per-sprint sourced from agent_runs.total_tokens
# ---------------------------------------------------------------------------

class TestTrendsTokensFromAgentRuns:
    def test_cost_by_sprint_tokens_equal_agent_runs_sum(self, tmp_path):
        """The token source the Trends chart consumes must equal the agent_runs sum."""
        db = _mk_db(tmp_path)
        _insert_run(db, 1, "sprint-7", "coder", 2000)
        _insert_run(db, 2, "sprint-7", "tester", 500)
        by_sprint = _call_cost(tmp_path, db).json()["by_sprint"]
        s7 = next(x for x in by_sprint if x["sprint_label"] == "sprint-7")
        assert s7["total_tokens"] == 2500

    def test_null_tokens_excluded_from_sprint_total(self, tmp_path):
        db = _mk_db(tmp_path)
        _insert_run(db, 1, "sprint-7", "coder", None)
        _insert_run(db, 2, "sprint-7", "coder", 800)
        by_sprint = _call_cost(tmp_path, db).json()["by_sprint"]
        s7 = next(x for x in by_sprint if x["sprint_label"] == "sprint-7")
        assert s7["total_tokens"] == 800

    def test_trends_panel_has_tokens_chart_canvas(self, html):
        panel = _trends_panel(html)
        assert "metrics-tokens-chart" in panel, \
            "Trends panel must contain a tokens-per-sprint chart canvas"

    def test_tokens_chart_wrapped_in_anl_card(self, html):
        panel = _trends_panel(html)
        # The chart wrapper must sit inside an anl-card like the other trend charts.
        assert "metrics-tokens-wrap" in panel
        assert panel.count("anl-card") >= 4, \
            "Trends must have four chart cards (velocity, failure, duration, tokens)"

    def test_trends_init_fetches_agent_runs_token_source(self, html):
        fn = _func_body(html, "window.anlTrendsInit")
        assert "analytics/cost" in fn, \
            "Trends init must fetch the agent_runs-backed cost endpoint for tokens"

    def test_tokens_render_reads_total_tokens(self, html):
        # A dedicated render path must read total_tokens from the API.
        fn = _func_body(html, "_renderTokens")
        assert "total_tokens" in fn, \
            "tokens-per-sprint render must read total_tokens from the API"


# ---------------------------------------------------------------------------
# AC4 — No hardcoded sample/mock values in the Metrics/Status/Trends pipeline
# ---------------------------------------------------------------------------

class TestNoMockData:
    FORBIDDEN = (
        "mockData", "MOCK_DATA", "sampleData", "SAMPLE_DATA",
        "dummyData", "DUMMY_DATA", "fakeData", "placeholderData",
        "FIXTURE", "fixtureData",
    )

    def _analytics_region(self, html: str) -> str:
        start = html.find('id="anl-panel-metrics"')
        end = html.find("anlTrendsInit")
        assert start >= 0 and end >= 0
        # Extend to include the trends IIFE render functions following the panels.
        return html[start: end + 4000]

    def test_no_mock_constants_in_analytics_region(self, html):
        region = self._analytics_region(html)
        for token in self.FORBIDDEN:
            assert token not in region, \
                f"Mock/sample constant {token!r} must not appear in the analytics views"

    def test_metrics_sprints_endpoint_not_a_baked_array(self, tmp_path):
        """With no projects/sprints the trends endpoint returns [] — not a fixture."""
        import server as srv
        from starlette.testclient import TestClient
        with patch.object(srv.projects_module, "load_projects", return_value=[]):
            client = TestClient(srv.app)
            resp = client.get("/api/metrics/sprints")
            assert resp.status_code == 200
            assert resp.json() == []


# ---------------------------------------------------------------------------
# AC5 — Empty states (not zeros / placeholder text) when no real data
# ---------------------------------------------------------------------------

class TestEmptyStates:
    def test_metrics_view_has_empty_state(self, html):
        assert "metric-empty" in html
        assert "No data for this period" in html

    def test_status_view_has_empty_states(self, html):
        assert "No active sprint." in html
        assert "No open issues." in html

    def test_trends_views_have_empty_state(self, html):
        # velocity/failure/duration use "No data available"; tokens must too.
        assert "No data available" in html

    def test_tokens_chart_shows_empty_state_when_no_data(self, html):
        fn = _func_body(html, "_renderTokens")
        assert "_showMsg('tokens'" in fn or '_showMsg("tokens"' in fn, \
            "tokens render must emit an empty-state message when there is no data"

    def test_metrics_endpoint_empty_state_values(self, tmp_path):
        # No sprint files at all → zeroed, render layer shows empty state.
        data = _call_metrics(tmp_path, _mk_db(tmp_path)).json()
        assert data["first_pass_rate"]["total_completed"] == 0


# ---------------------------------------------------------------------------
# Shared helper: extract a JS function body from the HTML by name
# ---------------------------------------------------------------------------

def _func_body(html: str, name: str) -> str:
    """Return ~the body of a JS function declared as `function name(` or
    `name = function(` — a best-effort brace-matched slice."""
    patterns = [
        f"function {name}(",
        f"{name} = function",
        f"{name} = async function",
    ]
    start = -1
    for p in patterns:
        start = html.find(p)
        if start >= 0:
            break
    assert start >= 0, f"function {name} must exist"
    brace = html.find("{", start)
    depth = 0
    i = brace
    while i < len(html):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                return html[start:i + 1]
        i += 1
    return html[start:start + 4000]
