"""Tests for issue #1848 — per-sprint trend series in delivery-health metrics.

AC coverage:
  AC1 — metrics response includes a top-level `by_sprint` key
  AC2 — `by_sprint` is a list with entries for each sprint, ordered oldest → newest
  AC3 — each entry has: sprint_label, first_pass_rate, rework_rate,
         avg_coder_minutes, wall_clock_minutes, tickets_done
  AC4 — behavioral: seed two synthetic sprint state files, call endpoint,
         assert by_sprint has both entries with correct rates
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"

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
    wall_clock_secs: float = 7200.0,
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


def _call(project_root: Path, slug: str = "repo", **params):
    import server as srv
    from starlette.testclient import TestClient

    # The route handler in routers/metrics.py uses both server._resolve_project_slug
    # and its own local _project_root_path — patch both targets.
    with (
        patch("server._resolve_project_slug", return_value="test/repo"),
        patch("routers.metrics._project_root_path", return_value=project_root),
        patch("server.db.get_token_usage_by_agent_model", return_value=[]),
    ):
        client = TestClient(srv.app)
        url = f"/api/projects/{slug}/analytics/metrics"
        return client.get(url, params=params)


# Two sprint issues: one first-pass, one rework
_ISSUE_FIRST_PASS = {
    "number": 101,
    "status": "done",
    "coder_started_at": "2026-01-10T10:00:00Z",
    "coder_finished_at": "2026-01-10T10:20:00Z",
    "tester_attempt_count": 1,
}

_ISSUE_REWORK = {
    "number": 102,
    "status": "done",
    "coder_started_at": "2026-01-10T11:00:00Z",
    "coder_finished_at": "2026-01-10T11:40:00Z",
    "tester_attempt_count": 2,
}

_ISSUE_SECOND_SPRINT = {
    "number": 201,
    "status": "done",
    "coder_started_at": "2026-02-01T10:00:00Z",
    "coder_finished_at": "2026-02-01T10:30:00Z",
    "tester_attempt_count": 1,
}


# ---------------------------------------------------------------------------
# AC1 — response includes `by_sprint` key
# ---------------------------------------------------------------------------

class TestBySprintPresent:
    def test_by_sprint_key_in_response(self, tmp_path):
        """AC1: delivery-health response has a top-level `by_sprint` key."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        data = _call(tmp_path).json()
        assert "by_sprint" in data, "Response must include `by_sprint` key"

    def test_by_sprint_is_list(self, tmp_path):
        """AC1: `by_sprint` value is a list."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        by_sprint = _call(tmp_path).json()["by_sprint"]
        assert isinstance(by_sprint, list)

    def test_by_sprint_empty_when_no_state_files(self, tmp_path):
        """AC1: `by_sprint` is an empty list when there are no sprint state files."""
        by_sprint = _call(tmp_path).json()["by_sprint"]
        assert by_sprint == []


# ---------------------------------------------------------------------------
# AC2 — list is ordered oldest → newest
# ---------------------------------------------------------------------------

class TestBySprintOrdering:
    def test_two_sprints_ordered_oldest_first(self, tmp_path):
        """AC2: oldest sprint appears first when two sprints exist."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS],
                           start_timestamp="2026-01-10T10:00:00Z")
        _make_sprint_state(tmp_path, "sprint-2", [_ISSUE_SECOND_SPRINT],
                           start_timestamp="2026-02-01T10:00:00Z")
        by_sprint = _call(tmp_path).json()["by_sprint"]
        assert len(by_sprint) == 2
        assert by_sprint[0]["sprint_label"] == "sprint-1"
        assert by_sprint[1]["sprint_label"] == "sprint-2"

    def test_single_sprint_has_one_entry(self, tmp_path):
        """AC2: single sprint → by_sprint has exactly one entry."""
        _make_sprint_state(tmp_path, "sprint-5", [_ISSUE_FIRST_PASS])
        by_sprint = _call(tmp_path).json()["by_sprint"]
        assert len(by_sprint) == 1
        assert by_sprint[0]["sprint_label"] == "sprint-5"


# ---------------------------------------------------------------------------
# AC3 — each entry has required fields
# ---------------------------------------------------------------------------

class TestBySprintShape:
    def test_entry_has_all_required_fields(self, tmp_path):
        """AC3: each by_sprint entry has all six required fields."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        entry = _call(tmp_path).json()["by_sprint"][0]
        for field in ("sprint_label", "first_pass_rate", "rework_rate",
                      "avg_coder_minutes", "wall_clock_minutes", "tickets_done"):
            assert field in entry, f"by_sprint entry missing field: {field}"

    def test_sprint_label_is_string(self, tmp_path):
        """AC3: sprint_label is a string."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        entry = _call(tmp_path).json()["by_sprint"][0]
        assert isinstance(entry["sprint_label"], str)

    def test_rates_are_numeric_in_zero_one_range(self, tmp_path):
        """AC3: first_pass_rate and rework_rate are floats in [0, 1]."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS, _ISSUE_REWORK])
        entry = _call(tmp_path).json()["by_sprint"][0]
        for field in ("first_pass_rate", "rework_rate"):
            v = entry[field]
            assert isinstance(v, (int, float)), f"{field} must be numeric"
            assert 0.0 <= v <= 1.0, f"{field}={v} out of [0,1]"

    def test_avg_coder_minutes_is_non_negative(self, tmp_path):
        """AC3: avg_coder_minutes is a non-negative number."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        entry = _call(tmp_path).json()["by_sprint"][0]
        assert isinstance(entry["avg_coder_minutes"], (int, float))
        assert entry["avg_coder_minutes"] >= 0

    def test_wall_clock_minutes_is_non_negative(self, tmp_path):
        """AC3: wall_clock_minutes is a non-negative number."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS],
                           wall_clock_secs=3600.0)
        entry = _call(tmp_path).json()["by_sprint"][0]
        assert isinstance(entry["wall_clock_minutes"], (int, float))
        assert entry["wall_clock_minutes"] >= 0

    def test_tickets_done_is_non_negative_int(self, tmp_path):
        """AC3: tickets_done is a non-negative integer."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS, _ISSUE_REWORK])
        entry = _call(tmp_path).json()["by_sprint"][0]
        assert isinstance(entry["tickets_done"], int)
        assert entry["tickets_done"] >= 0


# ---------------------------------------------------------------------------
# AC4 — behavioral: two sprints, correct rates
# ---------------------------------------------------------------------------

class TestBySprintRates:
    def test_two_sprints_both_present_in_by_sprint(self, tmp_path):
        """AC4: seeding two sprint state files produces two by_sprint entries."""
        _make_sprint_state(tmp_path, "sprint-10", [_ISSUE_FIRST_PASS],
                           start_timestamp="2026-01-10T10:00:00Z")
        _make_sprint_state(tmp_path, "sprint-11", [_ISSUE_REWORK],
                           start_timestamp="2026-02-01T10:00:00Z")
        by_sprint = _call(tmp_path).json()["by_sprint"]
        assert len(by_sprint) == 2
        labels = [e["sprint_label"] for e in by_sprint]
        assert "sprint-10" in labels
        assert "sprint-11" in labels

    def test_first_pass_sprint_has_rate_one(self, tmp_path):
        """AC4: sprint with one first-pass ticket → first_pass_rate == 1.0."""
        _make_sprint_state(tmp_path, "sprint-10", [_ISSUE_FIRST_PASS],
                           start_timestamp="2026-01-10T10:00:00Z")
        _make_sprint_state(tmp_path, "sprint-11", [_ISSUE_REWORK],
                           start_timestamp="2026-02-01T10:00:00Z")
        by_sprint = _call(tmp_path).json()["by_sprint"]
        s10 = next(e for e in by_sprint if e["sprint_label"] == "sprint-10")
        assert abs(s10["first_pass_rate"] - 1.0) < 0.001
        assert abs(s10["rework_rate"] - 0.0) < 0.001

    def test_rework_sprint_has_rework_rate_one(self, tmp_path):
        """AC4: sprint with one rework ticket → rework_rate == 1.0."""
        _make_sprint_state(tmp_path, "sprint-10", [_ISSUE_FIRST_PASS],
                           start_timestamp="2026-01-10T10:00:00Z")
        _make_sprint_state(tmp_path, "sprint-11", [_ISSUE_REWORK],
                           start_timestamp="2026-02-01T10:00:00Z")
        by_sprint = _call(tmp_path).json()["by_sprint"]
        s11 = next(e for e in by_sprint if e["sprint_label"] == "sprint-11")
        assert abs(s11["rework_rate"] - 1.0) < 0.001
        assert abs(s11["first_pass_rate"] - 0.0) < 0.001

    def test_mixed_sprint_rates(self, tmp_path):
        """AC4: sprint with 1 first-pass + 1 rework → both rates = 0.5."""
        _make_sprint_state(tmp_path, "sprint-10",
                           [_ISSUE_FIRST_PASS, _ISSUE_REWORK],
                           start_timestamp="2026-01-10T10:00:00Z")
        by_sprint = _call(tmp_path).json()["by_sprint"]
        assert len(by_sprint) == 1
        entry = by_sprint[0]
        assert abs(entry["first_pass_rate"] - 0.5) < 0.001
        assert abs(entry["rework_rate"] - 0.5) < 0.001

    def test_tickets_done_count(self, tmp_path):
        """AC4: tickets_done reflects number of done issues in the sprint."""
        _make_sprint_state(tmp_path, "sprint-10",
                           [_ISSUE_FIRST_PASS, _ISSUE_REWORK],
                           start_timestamp="2026-01-10T10:00:00Z")
        by_sprint = _call(tmp_path).json()["by_sprint"]
        assert by_sprint[0]["tickets_done"] == 2

    def test_avg_coder_minutes_value(self, tmp_path):
        """AC4: avg_coder_minutes is mean of coder durations in the sprint."""
        # _ISSUE_FIRST_PASS: 10:00→10:20 = 20 min
        # _ISSUE_REWORK: 11:00→11:40 = 40 min → mean = 30 min
        _make_sprint_state(tmp_path, "sprint-10",
                           [_ISSUE_FIRST_PASS, _ISSUE_REWORK],
                           start_timestamp="2026-01-10T10:00:00Z")
        by_sprint = _call(tmp_path).json()["by_sprint"]
        assert abs(by_sprint[0]["avg_coder_minutes"] - 30.0) < 0.1

    def test_wall_clock_minutes_value(self, tmp_path):
        """AC4: wall_clock_minutes matches wall_clock_secs / 60."""
        _make_sprint_state(tmp_path, "sprint-10", [_ISSUE_FIRST_PASS],
                           wall_clock_secs=3600.0,
                           start_timestamp="2026-01-10T10:00:00Z")
        by_sprint = _call(tmp_path).json()["by_sprint"]
        assert abs(by_sprint[0]["wall_clock_minutes"] - 60.0) < 0.1

    def test_by_sprint_independent_from_aggregate(self, tmp_path):
        """AC4: by_sprint per-sprint rates are independent of other sprints."""
        _make_sprint_state(tmp_path, "sprint-10", [_ISSUE_FIRST_PASS],
                           start_timestamp="2026-01-10T10:00:00Z")
        _make_sprint_state(tmp_path, "sprint-11",
                           [_ISSUE_REWORK, _ISSUE_REWORK],
                           start_timestamp="2026-02-01T10:00:00Z")
        by_sprint = _call(tmp_path).json()["by_sprint"]
        s10 = next(e for e in by_sprint if e["sprint_label"] == "sprint-10")
        s11 = next(e for e in by_sprint if e["sprint_label"] == "sprint-11")
        # sprint-10: pure first-pass
        assert abs(s10["first_pass_rate"] - 1.0) < 0.001
        # sprint-11: pure rework
        assert abs(s11["rework_rate"] - 1.0) < 0.001
        # The rates are sprint-local, not cross-sprint
        assert s10["first_pass_rate"] != s11["first_pass_rate"]
