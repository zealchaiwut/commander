"""Tests for issue #1849 — delivery-health stat strip on Board/History header.

AC coverage:
  AC1 — analytics/metrics endpoint returns first_pass_rate.rate,
         rework_rate.rate, and throughput.avg_sprint_length_minutes —
         the three values the stat strip renders
  AC3 — when no completed sprints exist, first_pass_rate.total_completed
         is 0 (signals strip should be hidden)
  AC4 — endpoint returns a single, stable response; no N-call fan-out
         (structural: the strip shares one fetch, verified by response shape)
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
# Helpers (reuse pattern from test_1848)
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


def _call(project_root: Path, slug: str = "repo"):
    import server as srv
    from starlette.testclient import TestClient

    with (
        patch("server._resolve_project_slug", return_value="test/repo"),
        patch("routers.metrics._project_root_path", return_value=project_root),
        patch("server.db.get_token_usage_by_agent_model", return_value=[]),
    ):
        client = TestClient(srv.app)
        return client.get(f"/api/projects/{slug}/analytics/metrics")


_ISSUE_FIRST_PASS = {
    "number": 1,
    "status": "done",
    "tester_attempt_count": 1,
}

_ISSUE_REWORK = {
    "number": 2,
    "status": "done",
    "tester_attempt_count": 2,
}


# ---------------------------------------------------------------------------
# AC1 — endpoint returns the three strip fields
# ---------------------------------------------------------------------------

class TestStripFieldsPresent:
    def test_first_pass_rate_key_present(self, tmp_path):
        """AC1: response has top-level first_pass_rate key."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        data = _call(tmp_path).json()
        assert "first_pass_rate" in data

    def test_first_pass_rate_has_rate_subfield(self, tmp_path):
        """AC1: first_pass_rate.rate is present and numeric."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        fpr = _call(tmp_path).json()["first_pass_rate"]
        assert "rate" in fpr
        assert isinstance(fpr["rate"], (int, float))

    def test_first_pass_rate_has_total_completed(self, tmp_path):
        """AC1/AC3: first_pass_rate.total_completed is present — used by strip to hide itself."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        fpr = _call(tmp_path).json()["first_pass_rate"]
        assert "total_completed" in fpr

    def test_rework_rate_key_present(self, tmp_path):
        """AC1: response has top-level rework_rate key."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_REWORK])
        data = _call(tmp_path).json()
        assert "rework_rate" in data

    def test_rework_rate_has_rate_subfield(self, tmp_path):
        """AC1: rework_rate.rate is present and numeric."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_REWORK])
        rwr = _call(tmp_path).json()["rework_rate"]
        assert "rate" in rwr
        assert isinstance(rwr["rate"], (int, float))

    def test_throughput_key_present(self, tmp_path):
        """AC1: response has top-level throughput key (provides avg sprint duration)."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        data = _call(tmp_path).json()
        assert "throughput" in data

    def test_throughput_has_avg_sprint_length_minutes(self, tmp_path):
        """AC1: throughput.avg_sprint_length_minutes is present — the strip's duration value."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS],
                           wall_clock_secs=5400.0)
        thr = _call(tmp_path).json()["throughput"]
        assert "avg_sprint_length_minutes" in thr
        assert isinstance(thr["avg_sprint_length_minutes"], (int, float))
        # 5400s = 90 minutes
        assert abs(thr["avg_sprint_length_minutes"] - 90.0) < 1.0


# ---------------------------------------------------------------------------
# AC1 — correct values are computed
# ---------------------------------------------------------------------------

class TestStripFieldValues:
    def test_first_pass_rate_is_one_when_all_pass(self, tmp_path):
        """AC1: 100% first-pass rate when all tickets pass tester first try."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS, _ISSUE_FIRST_PASS])
        fpr = _call(tmp_path).json()["first_pass_rate"]
        assert fpr["rate"] == 1.0

    def test_rework_rate_is_zero_when_all_pass(self, tmp_path):
        """AC1: 0% rework when all tickets pass first try."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        rwr = _call(tmp_path).json()["rework_rate"]
        assert rwr["rate"] == 0.0

    def test_mixed_sprint_rates(self, tmp_path):
        """AC1: 1 first-pass + 1 rework → 50% first-pass, 50% rework."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS, _ISSUE_REWORK])
        data = _call(tmp_path).json()
        assert data["first_pass_rate"]["rate"] == 0.5
        assert data["rework_rate"]["rate"] == 0.5


# ---------------------------------------------------------------------------
# AC3 — no completed sprints → total_completed = 0 (strip hides itself)
# ---------------------------------------------------------------------------

class TestNoSprintsHideSignal:
    def test_total_completed_zero_when_no_state_files(self, tmp_path):
        """AC3: total_completed is 0 when no sprint state files exist."""
        data = _call(tmp_path).json()
        assert data["first_pass_rate"]["total_completed"] == 0

    def test_first_pass_rate_zero_when_no_state_files(self, tmp_path):
        """AC3: first_pass_rate.rate is 0 when no sprint state files exist."""
        data = _call(tmp_path).json()
        assert data["first_pass_rate"]["rate"] == 0

    def test_rework_rate_zero_when_no_state_files(self, tmp_path):
        """AC3: rework_rate.rate is 0 when no sprint state files exist."""
        data = _call(tmp_path).json()
        assert data["rework_rate"]["rate"] == 0

    def test_response_200_when_no_sprints(self, tmp_path):
        """AC3: endpoint returns 200 (not an error) when no sprints exist."""
        resp = _call(tmp_path)
        assert resp.status_code == 200
