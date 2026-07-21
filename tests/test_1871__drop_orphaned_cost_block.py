"""Tests for issue #1871 — Drop orphaned cost block from /analytics/metrics.

AC coverage:
  AC1 — /analytics/metrics response does NOT include a 'cost' key
  AC2 — MODEL_PRICE_MAP is removed from startup.py (no /analytics/cost consumer uses it)
  AC3 — Remaining top-level keys still present: first_pass_rate, rework_rate,
         avg_duration, throughput, most_reworked, by_sprint
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


_ISSUE_FIRST_PASS = {
    "number": 301,
    "title": "Ticket A",
    "status": "done",
    "coder_started_at": "2026-01-10T10:00:00Z",
    "coder_finished_at": "2026-01-10T10:15:00Z",
    "tester_started_at": "2026-01-10T10:15:00Z",
    "tester_finished_at": "2026-01-10T10:20:00Z",
    "tester_attempt_count": 1,
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
# AC1 — 'cost' key must NOT appear in the response
# ---------------------------------------------------------------------------

class TestCostKeyAbsent:
    def test_cost_key_not_in_response(self, tmp_path):
        """AC1: /analytics/metrics must not include a 'cost' key."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        data = _call(tmp_path).json()
        assert "cost" not in data, (
            "'cost' key must be removed from /analytics/metrics — "
            "its frontend consumer was dropped in #1852"
        )

    def test_cost_key_absent_empty_project(self, tmp_path):
        """AC1: 'cost' key absent even when no sprint data exists."""
        data = _call(tmp_path).json()
        assert "cost" not in data


# ---------------------------------------------------------------------------
# AC2 — MODEL_PRICE_MAP must not be defined in startup.py
# ---------------------------------------------------------------------------

class TestModelPriceMapRemoved:
    def test_model_price_map_not_in_server_namespace(self):
        """AC2: MODEL_PRICE_MAP must be removed — /analytics/cost endpoint doesn't use it."""
        import server
        assert not hasattr(server, "MODEL_PRICE_MAP"), (
            "MODEL_PRICE_MAP is dead code: the only consumer was the removed "
            "'cost' block in _compute_analytics_metrics. Remove it from startup.py."
        )

    def test_model_price_map_not_in_startup_source(self):
        """AC2: MODEL_PRICE_MAP must not appear in startup.py source."""
        startup_src = (_DASHBOARD_ROOT / "startup.py").read_text(encoding="utf-8")
        assert "MODEL_PRICE_MAP" not in startup_src, (
            "MODEL_PRICE_MAP definition found in startup.py — it is dead code and must be removed."
        )


# ---------------------------------------------------------------------------
# AC3 — Remaining top-level keys still present
# ---------------------------------------------------------------------------

class TestRemainingKeysPresent:
    def test_all_remaining_keys_in_response(self, tmp_path):
        """AC3: non-cost top-level keys must still be returned."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        data = _call(tmp_path).json()
        for key in ("first_pass_rate", "rework_rate", "avg_duration", "throughput",
                    "most_reworked", "by_sprint"):
            assert key in data, f"Expected key '{key}' missing from /analytics/metrics response"

    def test_first_pass_rate_shape_intact(self, tmp_path):
        """AC3: first_pass_rate sub-keys unaffected by cost removal."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        fpr = _call(tmp_path).json()["first_pass_rate"]
        assert "rate" in fpr
        assert "passed" in fpr
        assert "total_completed" in fpr

    def test_rework_rate_shape_intact(self, tmp_path):
        """AC3: rework_rate sub-keys unaffected by cost removal."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        rwr = _call(tmp_path).json()["rework_rate"]
        assert "rate" in rwr
        assert "count" in rwr
        assert "rework_2plus" in rwr

    def test_avg_duration_shape_intact(self, tmp_path):
        """AC3: avg_duration sub-keys unaffected by cost removal."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        dur = _call(tmp_path).json()["avg_duration"]
        assert "coder_minutes" in dur
        assert "tester_minutes" in dur

    def test_throughput_shape_intact(self, tmp_path):
        """AC3: throughput sub-keys unaffected by cost removal."""
        _make_sprint_state(tmp_path, "sprint-1", [_ISSUE_FIRST_PASS])
        thr = _call(tmp_path).json()["throughput"]
        assert "avg_tickets_per_sprint" in thr
        assert "avg_sprint_length_days" in thr
