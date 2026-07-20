"""Tests for issue #651 — GET /api/projects/{slug}/analytics/metrics.

AC coverage:
  AC1  — GET returns HTTP 200 with full metrics payload (all top-level keys)
  AC2  — first_pass_rate: has `passed` and `total_completed` raw counts; rate computed correctly
  AC3  — rework_rate: has `count` (rejected ≥1 time) and `total`; `rework_2plus` for 2+ rejections
  AC4  — first_pass_rate.passed + rework_rate.count == first_pass_rate.total_completed
  AC5  — avg_duration: overall coder_minutes, tester_minutes; coder_by_size breakdown
  AC6  — throughput: includes avg_sprint_length_days (not only minutes)
  AC7  — [removed in #1871] cost block dropped — no frontend consumer
  AC8  — [removed in #1871] MODEL_PRICE_MAP dropped — no /analytics/cost consumer
  AC9  — since, until, sprint filters narrow all metrics to the requested window
  AC10 — when project_events has no rows, all numeric fields return 0 (not null, not 500)
  AC11 — unknown slug returns HTTP 404
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

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
    wall_clock_secs: float = 86400.0,  # 1 day
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


def _make_estimate(project_root: Path, issue_num: int, size: str) -> None:
    est_dir = project_root / ".commander" / "estimates"
    est_dir.mkdir(parents=True, exist_ok=True)
    est = {"issue_number": issue_num, "size": size}
    (est_dir / f"issue-{issue_num}.json").write_text(
        json.dumps(est), encoding="utf-8"
    )


_ISSUE_FIRST_PASS = {
    "number": 201,
    "title": "Ticket A",
    "status": "done",
    "coder_started_at": "2026-01-10T10:00:00Z",
    "coder_finished_at": "2026-01-10T10:15:00Z",
    "tester_started_at": "2026-01-10T10:15:00Z",
    "tester_finished_at": "2026-01-10T10:20:00Z",
    "tester_attempt_count": 1,
}

_ISSUE_REWORK = {
    "number": 202,
    "title": "Ticket B",
    "status": "done",
    "coder_started_at": "2026-01-10T11:00:00Z",
    "coder_finished_at": "2026-01-10T11:30:00Z",
    "tester_started_at": "2026-01-10T11:30:00Z",
    "tester_finished_at": "2026-01-10T11:36:00Z",
    "tester_attempt_count": 2,
}

_ISSUE_HEAVY_REWORK = {
    "number": 203,
    "title": "Ticket C",
    "status": "done",
    "coder_started_at": "2026-01-10T12:00:00Z",
    "coder_finished_at": "2026-01-10T12:45:00Z",
    "tester_started_at": "2026-01-10T12:45:00Z",
    "tester_finished_at": "2026-01-10T12:51:00Z",
    "tester_attempt_count": 3,
}


def _call(project_root: Path, slug: str = "repo", **params):
    import server as srv
    from starlette.testclient import TestClient

    with (
        patch("server._resolve_project_slug", return_value="test/repo"),
        patch("server._project_root_path", return_value=project_root),
    ):
        client = TestClient(srv.app)
        url = f"/api/projects/{slug}/analytics/metrics"
        return client.get(url, params=params)


# ---------------------------------------------------------------------------
# AC1 — HTTP 200, full payload
# ---------------------------------------------------------------------------

class TestHttp200:
    def test_returns_200(self, tmp_path):
        """AC1: endpoint returns HTTP 200."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        resp = _call(tmp_path)
        assert resp.status_code == 200

    def test_response_has_all_top_level_keys(self, tmp_path):
        """AC1: response has all metric sections (cost removed in #1871)."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        data = _call(tmp_path).json()
        for key in ("first_pass_rate", "rework_rate", "avg_duration", "throughput",
                    "most_reworked", "by_sprint"):
            assert key in data, f"Missing top-level key: {key}"


# ---------------------------------------------------------------------------
# AC2 — first_pass_rate shape and values
# ---------------------------------------------------------------------------

class TestFirstPassRate:
    def test_has_required_fields(self, tmp_path):
        """AC2: first_pass_rate has rate, passed, total_completed."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        fpr = _call(tmp_path).json()["first_pass_rate"]
        assert "rate" in fpr
        assert "passed" in fpr
        assert "total_completed" in fpr

    def test_rate_is_float_in_range(self, tmp_path):
        """AC2: rate is a float in [0, 1]."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS, _ISSUE_REWORK])
        fpr = _call(tmp_path).json()["first_pass_rate"]
        assert isinstance(fpr["rate"], (int, float))
        assert 0.0 <= fpr["rate"] <= 1.0

    def test_passed_count_correct(self, tmp_path):
        """AC2: passed count matches tickets with attempt_count == 1."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS, _ISSUE_REWORK])
        fpr = _call(tmp_path).json()["first_pass_rate"]
        assert fpr["passed"] == 1
        assert fpr["total_completed"] == 2
        assert abs(fpr["rate"] - 0.5) < 0.01

    def test_all_first_pass(self, tmp_path):
        """AC2: all tickets first-pass → rate == 1.0."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        fpr = _call(tmp_path).json()["first_pass_rate"]
        assert fpr["rate"] == 1.0
        assert fpr["passed"] == 1
        assert fpr["total_completed"] == 1


# ---------------------------------------------------------------------------
# AC3 — rework_rate shape and values
# ---------------------------------------------------------------------------

class TestReworkRate:
    def test_has_required_fields(self, tmp_path):
        """AC3: rework_rate has rate, count, rework_2plus, total."""
        _make_sprint_state(tmp_path, "sprint-1",
                           [_ISSUE_FIRST_PASS, _ISSUE_REWORK, _ISSUE_HEAVY_REWORK])
        rwr = _call(tmp_path).json()["rework_rate"]
        assert "rate" in rwr
        assert "count" in rwr
        assert "rework_2plus" in rwr
        assert "total" in rwr

    def test_count_is_rejected_at_least_once(self, tmp_path):
        """AC3: count = tickets with attempt_count >= 2."""
        _make_sprint_state(tmp_path, "sprint-1",
                           [_ISSUE_FIRST_PASS, _ISSUE_REWORK, _ISSUE_HEAVY_REWORK])
        rwr = _call(tmp_path).json()["rework_rate"]
        assert rwr["count"] == 2   # REWORK(attempt=2) + HEAVY_REWORK(attempt=3)
        assert rwr["total"] == 3

    def test_rework_2plus_is_attempt_count_gte_3(self, tmp_path):
        """AC3: rework_2plus = tickets with attempt_count >= 3 (rejected 2+ times)."""
        _make_sprint_state(tmp_path, "sprint-1",
                           [_ISSUE_FIRST_PASS, _ISSUE_REWORK, _ISSUE_HEAVY_REWORK])
        rwr = _call(tmp_path).json()["rework_rate"]
        assert rwr["rework_2plus"] == 1  # only HEAVY_REWORK

    def test_rate_calculation(self, tmp_path):
        """AC3: rework rate = rework_count / total_completed."""
        _make_sprint_state(tmp_path, "sprint-1",
                           [_ISSUE_FIRST_PASS, _ISSUE_REWORK, _ISSUE_HEAVY_REWORK])
        rwr = _call(tmp_path).json()["rework_rate"]
        assert abs(rwr["rate"] - 2/3) < 0.01


# ---------------------------------------------------------------------------
# AC4 — reconciliation: first_pass.passed + rework.count == total_completed
# ---------------------------------------------------------------------------

class TestReconciliation:
    def test_passed_plus_rework_equals_total(self, tmp_path):
        """AC4: no ticket counted twice or missed."""
        _make_sprint_state(tmp_path, "sprint-1",
                           [_ISSUE_FIRST_PASS, _ISSUE_REWORK, _ISSUE_HEAVY_REWORK])
        data = _call(tmp_path).json()
        fpr = data["first_pass_rate"]
        rwr = data["rework_rate"]
        assert fpr["passed"] + rwr["count"] == fpr["total_completed"]

    def test_reconciliation_multi_sprint(self, tmp_path):
        """AC4: reconciliation holds across multiple sprints."""
        _make_sprint_state(tmp_path, "sprint-1",
                           [_ISSUE_FIRST_PASS, _ISSUE_REWORK],
                           start_timestamp="2026-01-10T00:00:00Z")
        _make_sprint_state(tmp_path, "sprint-2",
                           [_ISSUE_HEAVY_REWORK],
                           start_timestamp="2026-02-01T00:00:00Z")
        data = _call(tmp_path).json()
        fpr = data["first_pass_rate"]
        rwr = data["rework_rate"]
        assert fpr["passed"] + rwr["count"] == fpr["total_completed"]
        assert fpr["total_completed"] == 3


# ---------------------------------------------------------------------------
# AC5 — avg_duration shape and values
# ---------------------------------------------------------------------------

class TestAvgDuration:
    def test_has_required_fields(self, tmp_path):
        """AC5: avg_duration has coder_minutes, tester_minutes, coder_by_size."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        dur = _call(tmp_path).json()["avg_duration"]
        assert "coder_minutes" in dur
        assert "tester_minutes" in dur
        assert "coder_by_size" in dur
        assert isinstance(dur["coder_by_size"], dict)

    def test_coder_minutes_calculation(self, tmp_path):
        """AC5: coder_minutes = mean of coder_started→coder_finished durations."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        dur = _call(tmp_path).json()["avg_duration"]
        assert abs(dur["coder_minutes"] - 15.0) < 0.1  # 10:00→10:15 = 15 min

    def test_tester_minutes_calculation(self, tmp_path):
        """AC5: tester_minutes = mean of tester_started→tester_finished durations."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        dur = _call(tmp_path).json()["avg_duration"]
        assert abs(dur["tester_minutes"] - 5.0) < 0.1  # 10:15→10:20 = 5 min

    def test_coder_by_size_populated_with_estimates(self, tmp_path):
        """AC5: coder_by_size uses estimate files for size-tier breakdown."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        _make_estimate(tmp_path, 201, "M")
        dur = _call(tmp_path).json()["avg_duration"]
        assert "M" in dur["coder_by_size"]
        assert abs(dur["coder_by_size"]["M"] - 15.0) < 0.1


# ---------------------------------------------------------------------------
# AC6 — throughput includes avg_sprint_length_days
# ---------------------------------------------------------------------------

class TestThroughput:
    def test_has_avg_tickets_per_sprint(self, tmp_path):
        """AC6: throughput includes avg_tickets_per_sprint."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        thr = _call(tmp_path).json()["throughput"]
        assert "avg_tickets_per_sprint" in thr

    def test_has_avg_sprint_length_days(self, tmp_path):
        """AC6: throughput includes avg_sprint_length_days (not only minutes)."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS],
                           wall_clock_secs=86400.0)  # 1 day
        thr = _call(tmp_path).json()["throughput"]
        assert "avg_sprint_length_days" in thr

    def test_avg_sprint_length_days_value(self, tmp_path):
        """AC6: 86400 seconds (1 day) → avg_sprint_length_days == 1.0."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS],
                           wall_clock_secs=86400.0)
        thr = _call(tmp_path).json()["throughput"]
        assert abs(thr["avg_sprint_length_days"] - 1.0) < 0.001

    def test_avg_sprint_length_days_two_sprints(self, tmp_path):
        """AC6: mean sprint length in days across two sprints."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS],
                           wall_clock_secs=86400.0,
                           start_timestamp="2026-01-10T00:00:00Z")
        _make_sprint_state(tmp_path, "sprint-2", [_ISSUE_REWORK],
                           wall_clock_secs=172800.0,  # 2 days
                           start_timestamp="2026-02-01T00:00:00Z")
        thr = _call(tmp_path).json()["throughput"]
        # mean(1 day, 2 days) = 1.5 days
        assert abs(thr["avg_sprint_length_days"] - 1.5) < 0.001


# ---------------------------------------------------------------------------
# AC9 — scope filters: since, until, sprint
# ---------------------------------------------------------------------------

class TestScopeFilters:
    def test_since_excludes_older_sprints(self, tmp_path):
        """AC9: since=date excludes sprints that started before that date."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS],
                           start_timestamp="2025-06-01T00:00:00Z")
        _make_sprint_state(tmp_path, "sprint-2", [_ISSUE_REWORK],
                           start_timestamp="2026-03-01T00:00:00Z")
        data = _call(tmp_path, since="2026-01-01").json()
        # Only sprint-2 is in scope; only _ISSUE_REWORK counted
        assert data["first_pass_rate"]["total_completed"] == 1
        assert data["first_pass_rate"]["passed"] == 0

    def test_until_excludes_newer_sprints(self, tmp_path):
        """AC9: until=date excludes sprints that started after that date."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS],
                           start_timestamp="2026-01-15T00:00:00Z")
        _make_sprint_state(tmp_path, "sprint-2", [_ISSUE_REWORK],
                           start_timestamp="2026-06-01T00:00:00Z")
        data = _call(tmp_path, until="2026-03-01").json()
        # Only sprint-1 in scope
        assert data["first_pass_rate"]["total_completed"] == 1
        assert data["first_pass_rate"]["passed"] == 1

    def test_sprint_filter_narrows_to_one_sprint(self, tmp_path):
        """AC9: sprint=<label> includes only that sprint's tickets."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS],
                           start_timestamp="2026-01-01T00:00:00Z")
        _make_sprint_state(tmp_path, "sprint-2", [_ISSUE_REWORK, _ISSUE_HEAVY_REWORK],
                           start_timestamp="2026-02-01T00:00:00Z")
        data = _call(tmp_path, sprint="sprint-2").json()
        assert data["first_pass_rate"]["total_completed"] == 2
        assert data["first_pass_rate"]["passed"] == 0

    def test_since_and_until_combined(self, tmp_path):
        """AC9: since + until together narrow window correctly."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS],
                           start_timestamp="2025-01-01T00:00:00Z")
        _make_sprint_state(tmp_path, "sprint-2", [_ISSUE_REWORK],
                           start_timestamp="2026-02-01T00:00:00Z")
        _make_sprint_state(tmp_path, "sprint-3", [_ISSUE_HEAVY_REWORK],
                           start_timestamp="2026-12-01T00:00:00Z")
        data = _call(tmp_path, since="2026-01-01", until="2026-06-01").json()
        # Only sprint-2 falls in window
        assert data["first_pass_rate"]["total_completed"] == 1


# ---------------------------------------------------------------------------
# AC10 — empty project_events → all numerics 0, not null, not 500
# ---------------------------------------------------------------------------

class TestEmptyData:
    def test_no_data_returns_200(self, tmp_path):
        """AC10: no sprint state files (empty DB) → HTTP 200."""
        resp = _call(tmp_path)
        assert resp.status_code == 200

    def test_no_data_first_pass_rate_is_zero(self, tmp_path):
        """AC10: first_pass_rate all zeros when no data."""
        data = _call(tmp_path).json()
        fpr = data["first_pass_rate"]
        assert fpr["rate"] == 0
        assert fpr["passed"] == 0
        assert fpr["total_completed"] == 0

    def test_no_data_rework_rate_is_zero(self, tmp_path):
        """AC10: rework_rate all zeros when no data."""
        data = _call(tmp_path).json()
        rwr = data["rework_rate"]
        assert rwr["rate"] == 0
        assert rwr["count"] == 0
        assert rwr["total"] == 0

    def test_no_data_avg_duration_is_zero(self, tmp_path):
        """AC10: avg_duration all zeros when no data."""
        data = _call(tmp_path).json()
        dur = data["avg_duration"]
        assert dur["coder_minutes"] == 0
        assert dur["tester_minutes"] == 0

    def test_no_data_throughput_is_zero(self, tmp_path):
        """AC10: throughput all zeros when no data."""
        data = _call(tmp_path).json()
        thr = data["throughput"]
        assert thr["avg_tickets_per_sprint"] == 0
        assert thr["avg_sprint_length_days"] == 0

    def test_no_data_fields_not_null(self, tmp_path):
        """AC10: numeric fields are 0 (int/float), not None/null."""
        data = _call(tmp_path).json()
        fpr = data["first_pass_rate"]
        thr = data["throughput"]
        assert fpr["rate"] is not None
        assert thr["avg_sprint_length_days"] is not None


# ---------------------------------------------------------------------------
# AC11 — unknown slug returns 404
# ---------------------------------------------------------------------------

class TestUnknownSlug:
    def test_unknown_slug_returns_404(self):
        """AC11: unrecognised project slug → HTTP 404."""
        import server
        from fastapi import HTTPException
        from starlette.testclient import TestClient

        with patch(
            "server._resolve_project_slug",
            side_effect=HTTPException(status_code=404, detail="Project 'no-such' not found"),
        ):
            client = TestClient(server.app)
            resp = client.get("/api/projects/no-such/analytics/metrics")
        assert resp.status_code == 404

    def test_unknown_slug_error_message(self):
        """AC11: 404 response includes an error message."""
        import server
        from fastapi import HTTPException
        from starlette.testclient import TestClient

        with patch(
            "server._resolve_project_slug",
            side_effect=HTTPException(status_code=404, detail="Project 'ghost' not found"),
        ):
            client = TestClient(server.app)
            resp = client.get("/api/projects/ghost/analytics/metrics")
        body = resp.json()
        assert "detail" in body or "error" in body or "message" in body
