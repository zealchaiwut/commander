"""Tests for issue #1869 — by_sprint list must be sorted numerically (oldest→newest).

Lexical sort breaks past sprint-99: sprint-100 sorts before sprint-45 as strings.

AC coverage:
  AC1 — by_sprint ordered numerically past sprint-99: sprint-9 < sprint-99 < sprint-100 < sprint-114
  AC2 — numeric sort is stable when sprint numbers are all single/double digit
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
# Helpers (mirrors pattern from test_1848__analytics_per_sprint_trend_series.py)
# ---------------------------------------------------------------------------

def _make_sprint_state(
    project_root: Path,
    sprint_label: str,
    wall_clock_secs: float = 3600.0,
    start_timestamp: str = "2026-01-10T10:00:00Z",
) -> None:
    import re as _re
    m = _re.search(r"(\d+)", sprint_label)
    n = m.group(1) if m else sprint_label
    sprints_dir = project_root / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "sprint_label": sprint_label,
        "sprint_number": int(n),
        "project": "test/repo",
        "start_timestamp": start_timestamp,
        "wall_clock_secs": wall_clock_secs,
        "issues": [
            {
                "number": int(n) * 100,
                "status": "done",
                "coder_started_at": start_timestamp,
                "coder_finished_at": start_timestamp,
                "tester_attempt_count": 1,
            }
        ],
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


# ---------------------------------------------------------------------------
# AC1 — numeric ordering past sprint-99
# ---------------------------------------------------------------------------

class TestBySprintNumericOrdering:
    def test_sprint_100_after_sprint_99_and_45(self, tmp_path):
        """AC1: sprint-100 must appear after sprint-45 and sprint-99 (breaks under lexical sort)."""
        _make_sprint_state(tmp_path, "sprint-45",  start_timestamp="2025-06-01T10:00:00Z")
        _make_sprint_state(tmp_path, "sprint-99",  start_timestamp="2025-12-01T10:00:00Z")
        _make_sprint_state(tmp_path, "sprint-100", start_timestamp="2026-01-01T10:00:00Z")

        by_sprint = _call(tmp_path).json()["by_sprint"]
        labels = [e["sprint_label"] for e in by_sprint]

        assert labels == ["sprint-45", "sprint-99", "sprint-100"], (
            f"Expected numeric order ['sprint-45','sprint-99','sprint-100'], got {labels}"
        )

    def test_four_sprints_past_100_in_numeric_order(self, tmp_path):
        """AC1: sprint-9, sprint-99, sprint-100, sprint-114 appear in numeric order."""
        for label, ts in [
            ("sprint-9",   "2025-01-01T10:00:00Z"),
            ("sprint-99",  "2025-12-01T10:00:00Z"),
            ("sprint-100", "2026-01-01T10:00:00Z"),
            ("sprint-114", "2026-04-01T10:00:00Z"),
        ]:
            _make_sprint_state(tmp_path, label, start_timestamp=ts)

        by_sprint = _call(tmp_path).json()["by_sprint"]
        labels = [e["sprint_label"] for e in by_sprint]

        assert labels == ["sprint-9", "sprint-99", "sprint-100", "sprint-114"], (
            f"Expected numeric order, got {labels}"
        )

    def test_lexical_vs_numeric_difference_is_detected(self, tmp_path):
        """AC1: sprint-100 must NOT appear before sprint-9 (lexical bug symptom)."""
        _make_sprint_state(tmp_path, "sprint-9",   start_timestamp="2025-01-01T10:00:00Z")
        _make_sprint_state(tmp_path, "sprint-100", start_timestamp="2026-01-01T10:00:00Z")

        by_sprint = _call(tmp_path).json()["by_sprint"]
        labels = [e["sprint_label"] for e in by_sprint]

        assert labels.index("sprint-9") < labels.index("sprint-100"), (
            "sprint-9 must come before sprint-100 (numeric order)"
        )


# ---------------------------------------------------------------------------
# AC2 — numeric sort is stable for single/double-digit sprints
# ---------------------------------------------------------------------------

class TestBySprintStableForSmallNumbers:
    def test_single_digit_sprints_still_ordered(self, tmp_path):
        """AC2: sprints sprint-1 through sprint-5 still appear in numeric order."""
        for i, label in enumerate(["sprint-3", "sprint-1", "sprint-5", "sprint-2"]):
            _make_sprint_state(
                tmp_path, label,
                start_timestamp=f"2026-0{i+1}-01T10:00:00Z"
            )

        by_sprint = _call(tmp_path).json()["by_sprint"]
        labels = [e["sprint_label"] for e in by_sprint]
        nums = [int(lbl.split("-")[1]) for lbl in labels]

        assert nums == sorted(nums), f"Expected ascending numeric order, got {labels}"

    def test_double_digit_sprints_ordered(self, tmp_path):
        """AC2: sprints with numbers 10–19 appear in numeric order."""
        for i, label in enumerate(["sprint-19", "sprint-10", "sprint-15"]):
            _make_sprint_state(tmp_path, label, start_timestamp=f"2026-0{i+1}-01T10:00:00Z")

        by_sprint = _call(tmp_path).json()["by_sprint"]
        labels = [e["sprint_label"] for e in by_sprint]
        nums = [int(lbl.split("-")[1]) for lbl in labels]

        assert nums == sorted(nums), f"Expected ascending numeric order, got {labels}"
