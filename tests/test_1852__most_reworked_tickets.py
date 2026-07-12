"""Tests for issue #1852 — replace dollar-cost cards with most-reworked-tickets list.

AC coverage:
  AC1 — card-cost-sprint and card-cost-ticket are absent from pane-metrics HTML
  AC2 — a "Most reworked tickets" card is present in pane-metrics HTML
  AC3 — metrics response includes most_reworked list with number + rejections entries
  AC4 — behavioral: seed sprint state with a twice-rejected issue, assert it tops the list with count 2
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
_STATIC_DIR = _DASHBOARD_ROOT / "static"
_PROJECT_HTML = _STATIC_DIR / "project.html"

for _p in (str(_DASHBOARD_ROOT),):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sprint_state(
    project_root: Path,
    sprint_label: str,
    issues: list[dict],
    wall_clock_secs: float = 86400.0,
    start_timestamp: str = "2026-01-10T10:00:00Z",
) -> None:
    import re
    m = re.search(r"(\d+)", sprint_label)
    n = m.group(1) if m else sprint_label
    sprints_dir = project_root / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "sprint_label": sprint_label,
        "sprint_number": int(n),
        "project": "test/repo",
        "start_timestamp": start_timestamp,
        "wall_clock_secs": wall_clock_secs,
        "issues": issues,
    }
    (sprints_dir / f"sprint-{n}-state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )


def _call_metrics(project_root: Path, **params):
    import server as srv
    from starlette.testclient import TestClient

    with (
        patch("server._resolve_project_slug", return_value="test/repo"),
        patch("server._project_root_path", return_value=project_root),
        patch("routers.metrics._project_root_path", return_value=project_root),
        patch("server.db.get_token_usage_by_agent_model", return_value=[]),
    ):
        client = TestClient(srv.app)
        url = "/api/projects/repo/analytics/metrics"
        return client.get(url, params=params)


# ---------------------------------------------------------------------------
# AC1 — dollar-cost cards removed from HTML
# ---------------------------------------------------------------------------

class TestCostCardsRemoved:
    def test_card_cost_sprint_absent(self):
        """AC1: card-cost-sprint is not present in pane-metrics."""
        html = _PROJECT_HTML.read_text(encoding="utf-8")
        assert 'id="card-cost-sprint"' not in html, \
            "card-cost-sprint must be removed from pane-metrics"

    def test_card_cost_ticket_absent(self):
        """AC1: card-cost-ticket is not present in pane-metrics."""
        html = _PROJECT_HTML.read_text(encoding="utf-8")
        assert 'id="card-cost-ticket"' not in html, \
            "card-cost-ticket must be removed from pane-metrics"


# ---------------------------------------------------------------------------
# AC2 — Most reworked tickets card present
# ---------------------------------------------------------------------------

class TestMostReworkedCardPresent:
    def test_most_reworked_card_exists(self):
        """AC2: a Most reworked tickets card is present in pane-metrics."""
        html = _PROJECT_HTML.read_text(encoding="utf-8")
        assert "most-rework" in html.lower() or "reworked" in html.lower(), \
            "A 'Most reworked tickets' card must be present in the HTML"

    def test_most_reworked_list_element_exists(self):
        """AC2: the most-reworked list element is inside anl-panel-metrics."""
        html = _PROJECT_HTML.read_text(encoding="utf-8")
        panel_start = html.find('id="anl-panel-metrics"')
        panel_end = html.find('id="anl-panel-status"')
        assert panel_start != -1, "anl-panel-metrics not found"
        panel_html = html[panel_start:panel_end] if panel_end != -1 else html[panel_start:]
        assert "mrw" in panel_html or "most-rework" in panel_html.lower(), \
            "Most reworked list element must be inside anl-panel-metrics"


# ---------------------------------------------------------------------------
# AC3 — API response includes most_reworked
# ---------------------------------------------------------------------------

class TestMostReworkedInResponse:
    def test_response_includes_most_reworked_key(self, tmp_path):
        """AC3: metrics response includes most_reworked list."""
        _make_sprint_state(tmp_path, "sprint-1", [{
            "number": 100,
            "title": "Ticket A",
            "status": "done",
            "tester_attempt_count": 2,
        }])
        data = _call_metrics(tmp_path).json()
        assert "most_reworked" in data, \
            "metrics response must include most_reworked key"

    def test_most_reworked_is_list(self, tmp_path):
        """AC3: most_reworked is a list."""
        _make_sprint_state(tmp_path, "sprint-1", [{
            "number": 100,
            "title": "Ticket A",
            "status": "done",
            "tester_attempt_count": 2,
        }])
        data = _call_metrics(tmp_path).json()
        assert isinstance(data["most_reworked"], list), \
            "most_reworked must be a list"

    def test_most_reworked_entries_have_number_and_rejections(self, tmp_path):
        """AC3: each entry in most_reworked has number and rejections fields."""
        _make_sprint_state(tmp_path, "sprint-1", [{
            "number": 100,
            "title": "Ticket A",
            "status": "done",
            "tester_attempt_count": 2,
        }])
        data = _call_metrics(tmp_path).json()
        entries = data["most_reworked"]
        assert len(entries) > 0, "most_reworked should have at least one entry"
        entry = entries[0]
        assert "number" in entry, "most_reworked entry must have 'number'"
        assert "rejections" in entry, "most_reworked entry must have 'rejections'"

    def test_most_reworked_empty_when_no_rejections(self, tmp_path):
        """AC3: most_reworked is empty when all tickets are first-pass."""
        _make_sprint_state(tmp_path, "sprint-1", [{
            "number": 100,
            "title": "Ticket A",
            "status": "done",
            "tester_attempt_count": 1,
        }])
        data = _call_metrics(tmp_path).json()
        assert data["most_reworked"] == [], \
            "most_reworked must be empty when no tickets were rejected"


# ---------------------------------------------------------------------------
# AC4 — Behavioral: twice-rejected issue tops the list with count 2
# ---------------------------------------------------------------------------

class TestMostReworkedBehavioral:
    def test_twice_rejected_issue_tops_list(self, tmp_path):
        """AC4: seed sprint state with a twice-rejected issue, assert it tops the list with count 2."""
        twice_rejected = {
            "number": 500,
            "title": "Hard ticket",
            "status": "done",
            "tester_attempt_count": 3,  # 3 attempts = 2 rejections
        }
        once_rejected = {
            "number": 501,
            "title": "Medium ticket",
            "status": "done",
            "tester_attempt_count": 2,  # 2 attempts = 1 rejection
        }
        first_pass = {
            "number": 502,
            "title": "Easy ticket",
            "status": "done",
            "tester_attempt_count": 1,
        }
        _make_sprint_state(tmp_path, "sprint-1", [twice_rejected, once_rejected, first_pass])

        data = _call_metrics(tmp_path).json()
        most_reworked = data["most_reworked"]

        assert len(most_reworked) >= 1, "most_reworked must have at least one entry"
        top = most_reworked[0]
        assert top["number"] == 500, \
            f"Twice-rejected issue #500 must top the list, got #{top['number']}"
        assert top["rejections"] == 2, \
            f"Top entry must show 2 rejections, got {top['rejections']}"

    def test_list_capped_at_top_5(self, tmp_path):
        """AC4: most_reworked lists at most 5 entries."""
        issues = [
            {"number": 600 + i, "title": f"Ticket {i}", "status": "done",
             "tester_attempt_count": 10 - i}
            for i in range(8)
        ]
        _make_sprint_state(tmp_path, "sprint-1", issues)

        data = _call_metrics(tmp_path).json()
        assert len(data["most_reworked"]) <= 5, \
            "most_reworked must list at most 5 entries"

    def test_list_sorted_by_rejections_descending(self, tmp_path):
        """AC4: most_reworked is sorted by rejection count, highest first."""
        issues = [
            {"number": 700, "status": "done", "tester_attempt_count": 2},
            {"number": 701, "status": "done", "tester_attempt_count": 4},
            {"number": 702, "status": "done", "tester_attempt_count": 3},
        ]
        _make_sprint_state(tmp_path, "sprint-1", issues)

        data = _call_metrics(tmp_path).json()
        counts = [e["rejections"] for e in data["most_reworked"]]
        assert counts == sorted(counts, reverse=True), \
            "most_reworked must be sorted by rejections descending"
