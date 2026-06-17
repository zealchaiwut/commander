"""Tests for issue #649 — GET /api/projects/{slug}/analytics/calibration.

Migrated to the local-file source under issue #718: calibration is computed
exclusively from `.commander/sprints/sprint-N-state.json` +
`.commander/estimates/issue-N.json` (no Neon). The response contract is
unchanged, so every original AC assertion is preserved — only the data setup
moved from DB rows to status/estimate files, and date scoping is now applied at
the sprint level (start_timestamp).

AC coverage:
  AC1  — returns HTTP 200 with valid JSON
  AC2  — by_size has all four keys (S, M, L, XL) with required sub-fields
  AC3  — configured_minutes sourced from project estimation settings (not hardcoded)
  AC4  — count/min/avg/max computed from local files scoped to the project
  AC5  — elapsed seconds converted to minutes (÷60)
  AC6  — points array: each element has issue_number, estimated_size, estimated_minutes, actual_minutes
  AC7  — since param filters sprints by start_timestamp ≥ date
  AC8  — until param filters sprints by start_timestamp ≤ date
  AC9  — sprint param filters by sprint label
  AC10 — sizes with zero tickets: count=0, min/avg/max=null (not omitted)
  AC11 — unknown slug returns 404
  AC12 — since + until + sprint can be combined
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"

for _p in (str(_DASHBOARD_ROOT),):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _ticket(num: int, secs: int | None, start: str = "2026-01-15T10:00:00Z") -> dict:
    """A completed ticket whose coder elapsed == ``secs`` (tester elapsed 0).

    actual_minutes therefore equals secs/60. When ``secs`` is None the ticket
    has no usable coder/tester timestamps and must be excluded.
    """
    issue = {"number": num, "title": f"T{num}", "status": "done"}
    if secs is not None:
        s = datetime.fromisoformat(start.rstrip("Z")).replace(tzinfo=timezone.utc)
        e = s + timedelta(seconds=secs)
        issue["coder_started_at"] = start
        issue["coder_finished_at"] = e.isoformat().replace("+00:00", "Z")
    return issue


def _state(project_root: Path, label: str, tickets: list[dict],
           start_timestamp: str = "2026-01-15T10:00:00Z") -> None:
    import re
    n = re.search(r"(\d+)", label).group(1)
    d = project_root / ".commander" / "sprints"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"sprint-{n}-state.json").write_text(json.dumps({
        "sprint_label": label,
        "sprint_number": int(n),
        "project": "owner/myrepo",
        "start_timestamp": start_timestamp,
        "wall_clock_secs": 86400.0,
        "issues": tickets,
    }), encoding="utf-8")


def _est(project_root: Path, num: int, size: str | None) -> None:
    if size is None:
        return  # no estimate file → size unknown → excluded
    d = project_root / ".commander" / "estimates"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"issue-{num}.json").write_text(
        json.dumps({"issue_number": num, "size": size}), encoding="utf-8")


@pytest.fixture
def root(tmp_path):
    return tmp_path


def _call(project_root: Path, slug: str = "myrepo", project: str = "owner/myrepo",
          settings: dict | None = None, **params):
    """Call GET /api/projects/{slug}/analytics/calibration via TestClient."""
    import server as srv
    from starlette.testclient import TestClient

    with (
        patch("server._resolve_project_slug", return_value=project),
        patch("server._project_root_path", return_value=project_root),
        patch("server._settings_repo.get_setting", return_value=(settings or {})),
    ):
        client = TestClient(srv.app)
        return client.get(f"/api/projects/{slug}/analytics/calibration", params=params)


# ---------------------------------------------------------------------------
# AC1 — HTTP 200, valid JSON
# ---------------------------------------------------------------------------

class TestHttp200:
    def test_returns_200(self, root):
        _state(root, "sprint-1", [])
        assert _call(root).status_code == 200

    def test_response_is_json(self, root):
        _state(root, "sprint-1", [])
        assert isinstance(_call(root).json(), dict)


# ---------------------------------------------------------------------------
# AC2 — by_size has all four size keys with required fields
# ---------------------------------------------------------------------------

class TestBySizeShape:
    def test_by_size_present(self, root):
        _state(root, "sprint-1", [])
        assert "by_size" in _call(root).json()

    def test_all_four_size_keys_present(self, root):
        _state(root, "sprint-1", [])
        by_size = _call(root).json()["by_size"]
        for sz in ("S", "M", "L", "XL"):
            assert sz in by_size, f"Missing size key: {sz}"

    def test_each_size_has_required_fields(self, root):
        _state(root, "sprint-1", [])
        by_size = _call(root).json()["by_size"]
        for sz in ("S", "M", "L", "XL"):
            entry = by_size[sz]
            assert "configured_minutes" in entry
            assert "count" in entry
            assert "min_minutes" in entry
            assert "avg_minutes" in entry
            assert "max_minutes" in entry

    def test_points_present(self, root):
        _state(root, "sprint-1", [])
        data = _call(root).json()
        assert "points" in data
        assert isinstance(data["points"], list)


# ---------------------------------------------------------------------------
# AC3 — configured_minutes from project estimation settings
# ---------------------------------------------------------------------------

class TestConfiguredMinutes:
    def test_configured_minutes_uses_defaults_when_no_override(self, root):
        _state(root, "sprint-1", [])
        by_size = _call(root).json()["by_size"]
        assert by_size["S"]["configured_minutes"] == 5
        assert by_size["M"]["configured_minutes"] == 15
        assert by_size["L"]["configured_minutes"] == 30
        assert by_size["XL"]["configured_minutes"] == 60

    def test_configured_minutes_reflects_project_override(self, root):
        _state(root, "sprint-1", [])
        by_size = _call(root, settings={"estimation_s_minutes": 10}).json()["by_size"]
        assert by_size["S"]["configured_minutes"] == 10
        assert by_size["M"]["configured_minutes"] == 15  # unaffected


# ---------------------------------------------------------------------------
# AC4 — aggregates computed from local files scoped to project
# ---------------------------------------------------------------------------

class TestAggregates:
    def test_count_matches_qualifying_tickets(self, root):
        _state(root, "sprint-1", [_ticket(101, 900), _ticket(102, 1800)])
        _est(root, 101, "M")
        _est(root, 102, "M")
        assert _call(root).json()["by_size"]["M"]["count"] == 2

    def test_tickets_from_other_project_excluded(self, root, tmp_path_factory):
        # Project A is the one queried; project B lives in a separate root the
        # endpoint never reads — so B's tickets cannot leak in.
        other_root = tmp_path_factory.mktemp("other")
        _state(root, "sprint-1", [_ticket(101, 1800)])
        _est(root, 101, "L")
        _state(other_root, "sprint-2", [_ticket(201, 600)])
        _est(other_root, 201, "L")
        assert _call(root).json()["by_size"]["L"]["count"] == 1

    def test_null_estimated_size_excluded(self, root):
        _state(root, "sprint-1", [_ticket(101, 900), _ticket(102, 300)])
        _est(root, 101, None)   # no estimate → excluded
        _est(root, 102, "S")
        assert _call(root).json()["by_size"]["S"]["count"] == 1

    def test_null_elapsed_seconds_excluded(self, root):
        _state(root, "sprint-1", [_ticket(101, None), _ticket(102, 300)])
        _est(root, 101, "S")
        _est(root, 102, "S")
        assert _call(root).json()["by_size"]["S"]["count"] == 1

    def test_min_avg_max_correct(self, root):
        _state(root, "sprint-1", [_ticket(101, 60), _ticket(102, 120), _ticket(103, 180)])
        for n in (101, 102, 103):
            _est(root, n, "M")
        entry = _call(root).json()["by_size"]["M"]
        assert entry["count"] == 3
        assert abs(entry["min_minutes"] - 1.0) < 0.01
        assert abs(entry["avg_minutes"] - 2.0) < 0.01
        assert abs(entry["max_minutes"] - 3.0) < 0.01


# ---------------------------------------------------------------------------
# AC5 — elapsed seconds converted to minutes (÷60)
# ---------------------------------------------------------------------------

class TestMinutesConversion:
    def test_seconds_divided_by_60(self, root):
        _state(root, "sprint-1", [_ticket(101, 3600)])  # 60 min
        _est(root, 101, "L")
        assert abs(_call(root).json()["by_size"]["L"]["avg_minutes"] - 60.0) < 0.01

    def test_fractional_minutes_preserved(self, root):
        _state(root, "sprint-1", [_ticket(101, 90)])  # 1.5 min
        _est(root, 101, "S")
        assert abs(_call(root).json()["by_size"]["S"]["avg_minutes"] - 1.5) < 0.01

    def test_points_actual_minutes_in_minutes(self, root):
        _state(root, "sprint-1", [_ticket(101, 1200)])  # 20 min
        _est(root, 101, "M")
        points = _call(root).json()["points"]
        assert len(points) == 1
        assert abs(points[0]["actual_minutes"] - 20.0) < 0.01


# ---------------------------------------------------------------------------
# AC6 — points array shape
# ---------------------------------------------------------------------------

class TestPointsShape:
    def test_points_count_equals_qualifying_tickets(self, root):
        _state(root, "sprint-1", [
            _ticket(101, 300), _ticket(102, 900),
            _ticket(103, 600), _ticket(104, None),
        ])
        _est(root, 101, "S")
        _est(root, 102, "M")
        _est(root, 103, None)   # excluded (no size)
        _est(root, 104, "L")    # excluded (no elapsed)
        assert len(_call(root).json()["points"]) == 2

    def test_point_has_required_fields(self, root):
        _state(root, "sprint-1", [_ticket(101, 900)])
        _est(root, 101, "M")
        point = _call(root).json()["points"][0]
        assert "issue_number" in point
        assert "estimated_size" in point
        assert "estimated_minutes" in point
        assert "actual_minutes" in point

    def test_point_issue_number_correct(self, root):
        _state(root, "sprint-1", [_ticket(555, 900)])
        _est(root, 555, "M")
        assert _call(root).json()["points"][0]["issue_number"] == 555

    def test_point_estimated_size_correct(self, root):
        _state(root, "sprint-1", [_ticket(101, 3600)])
        _est(root, 101, "XL")
        assert _call(root).json()["points"][0]["estimated_size"] == "XL"

    def test_point_estimated_minutes_matches_configured(self, root):
        _state(root, "sprint-1", [_ticket(101, 1800)])
        _est(root, 101, "L")
        assert _call(root).json()["points"][0]["estimated_minutes"] == 30  # default L


# ---------------------------------------------------------------------------
# AC7 — since param filters sprints by start_timestamp
# ---------------------------------------------------------------------------

class TestSinceFilter:
    def test_since_excludes_earlier_sprints(self, root):
        _state(root, "sprint-1", [_ticket(101, 300)], start_timestamp="2025-12-31T23:59:59Z")
        _state(root, "sprint-2", [_ticket(102, 600)], start_timestamp="2026-01-15T10:00:00Z")
        _est(root, 101, "S")
        _est(root, 102, "S")
        data = _call(root, since="2026-01-01").json()
        assert data["by_size"]["S"]["count"] == 1
        assert data["points"][0]["issue_number"] == 102

    def test_since_inclusive_boundary(self, root):
        _state(root, "sprint-1", [_ticket(101, 300)], start_timestamp="2026-01-01T00:00:00Z")
        _est(root, 101, "S")
        assert _call(root, since="2026-01-01").json()["by_size"]["S"]["count"] == 1


# ---------------------------------------------------------------------------
# AC8 — until param filters sprints by start_timestamp
# ---------------------------------------------------------------------------

class TestUntilFilter:
    def test_until_excludes_later_sprints(self, root):
        _state(root, "sprint-1", [_ticket(101, 900)], start_timestamp="2026-01-15T10:00:00Z")
        _state(root, "sprint-2", [_ticket(102, 1800)], start_timestamp="2026-03-01T10:00:00Z")
        _est(root, 101, "M")
        _est(root, 102, "M")
        data = _call(root, until="2026-01-31").json()
        assert data["by_size"]["M"]["count"] == 1
        assert data["points"][0]["issue_number"] == 101


# ---------------------------------------------------------------------------
# AC9 — sprint param filters by sprint label
# ---------------------------------------------------------------------------

class TestSprintFilter:
    def test_sprint_filter_returns_only_matching_sprint(self, root):
        _state(root, "sprint-42", [_ticket(101, 300)], start_timestamp="2026-01-10T10:00:00Z")
        _state(root, "sprint-43", [_ticket(201, 600)], start_timestamp="2026-02-10T10:00:00Z")
        _est(root, 101, "S")
        _est(root, 201, "S")
        data = _call(root, sprint="sprint-42").json()
        assert data["by_size"]["S"]["count"] == 1
        assert data["points"][0]["issue_number"] == 101

    def test_sprint_filter_no_match_gives_empty(self, root):
        _state(root, "sprint-1", [_ticket(101, 300)])
        _est(root, 101, "S")
        data = _call(root, sprint="sprint-99").json()
        assert data["by_size"]["S"]["count"] == 0
        assert data["points"] == []


# ---------------------------------------------------------------------------
# AC10 — sizes with zero tickets: count=0, min/avg/max=null
# ---------------------------------------------------------------------------

class TestZeroSizes:
    def test_empty_size_has_count_zero(self, root):
        _state(root, "sprint-1", [_ticket(101, 300)])
        _est(root, 101, "S")
        assert _call(root).json()["by_size"]["XL"]["count"] == 0

    def test_empty_size_has_null_stats(self, root):
        _state(root, "sprint-1", [_ticket(101, 300)])
        _est(root, 101, "S")
        entry = _call(root).json()["by_size"]["XL"]
        assert entry["min_minutes"] is None
        assert entry["avg_minutes"] is None
        assert entry["max_minutes"] is None

    def test_all_sizes_present_even_with_no_tickets(self, root):
        _state(root, "sprint-1", [])
        by_size = _call(root).json()["by_size"]
        for sz in ("S", "M", "L", "XL"):
            assert sz in by_size
            assert by_size[sz]["count"] == 0


# ---------------------------------------------------------------------------
# AC11 — unknown slug returns 404
# ---------------------------------------------------------------------------

class TestNotFound:
    def test_unknown_slug_returns_404(self):
        import server as srv
        from starlette.testclient import TestClient
        client = TestClient(srv.app, raise_server_exceptions=False)
        resp = client.get("/api/projects/does-not-exist/analytics/calibration")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# AC12 — combined filters
# ---------------------------------------------------------------------------

class TestCombinedFilters:
    def test_since_until_sprint_combined(self, root):
        # sprint-42 in date range and matching label → kept.
        _state(root, "sprint-42", [_ticket(101, 900)], start_timestamp="2026-02-10T10:00:00Z")
        # right date, wrong sprint label → dropped by sprint filter.
        _state(root, "sprint-43", [_ticket(201, 900)], start_timestamp="2026-02-10T10:00:00Z")
        # matching nothing: out of date range entirely.
        _state(root, "sprint-44", [_ticket(301, 1200)], start_timestamp="2025-12-01T10:00:00Z")
        for n in (101, 201, 301):
            _est(root, n, "M")
        data = _call(root, since="2026-01-01", until="2026-03-31", sprint="sprint-42").json()
        assert data["by_size"]["M"]["count"] == 1
        assert len(data["points"]) == 1
        assert data["points"][0]["issue_number"] == 101


# ---------------------------------------------------------------------------
# AC (issue #1331) — size-resolution fallback hierarchy
# ---------------------------------------------------------------------------

def _state_with_estimates(
    project_root: Path,
    label: str,
    tickets: list[dict],
    estimates: dict,
    start_timestamp: str = "2026-01-15T10:00:00Z",
) -> None:
    """Write a sprint state file that includes a top-level estimates dict."""
    import re
    n = re.search(r"(\d+)", label).group(1)
    d = project_root / ".commander" / "sprints"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"sprint-{n}-state.json").write_text(json.dumps({
        "sprint_label": label,
        "sprint_number": int(n),
        "project": "owner/myrepo",
        "start_timestamp": start_timestamp,
        "wall_clock_secs": 86400.0,
        "issues": tickets,
        "estimates": estimates,
    }), encoding="utf-8")


class TestSizeFallbacks:
    """issue #1331 — size resolution: JSON → state.estimates → size-* label."""

    def test_size_from_github_label_only(self, root):
        """Ticket with size-* label only (no JSON, no state.estimates) is counted."""
        ticket = _ticket(101, 900)
        ticket["labels"] = [{"name": "size-M"}]
        _state(root, "sprint-1", [ticket])
        # no _est() — no estimate JSON
        assert _call(root).json()["by_size"]["M"]["count"] == 1

    def test_size_from_state_estimates_only(self, root):
        """Ticket with state.estimates entry only (no JSON, no label) is counted."""
        ticket = _ticket(101, 900)
        _state_with_estimates(root, "sprint-1", [ticket], estimates={"101": {"size": "L"}})
        # no _est() — no estimate JSON, no label
        assert _call(root).json()["by_size"]["L"]["count"] == 1

    def test_json_at_canonical_path_counts(self, root):
        """Ticket with JSON at canonical estimates_dir (after path fix) is counted."""
        ticket = _ticket(101, 900)
        _state(root, "sprint-1", [ticket])
        _est(root, 101, "XL")
        assert _call(root).json()["by_size"]["XL"]["count"] == 1

    def test_json_takes_precedence_over_label(self, root):
        """JSON at canonical path wins over size-* label."""
        ticket = _ticket(101, 900)
        ticket["labels"] = [{"name": "size-S"}]
        _state(root, "sprint-1", [ticket])
        _est(root, 101, "M")  # JSON says M
        data = _call(root).json()
        assert data["by_size"]["M"]["count"] == 1
        assert data["by_size"]["S"]["count"] == 0

    def test_json_takes_precedence_over_state_estimates(self, root):
        """JSON at canonical path wins over state.estimates size."""
        ticket = _ticket(101, 900)
        _state_with_estimates(root, "sprint-1", [ticket], estimates={"101": {"size": "S"}})
        _est(root, 101, "M")  # JSON says M
        data = _call(root).json()
        assert data["by_size"]["M"]["count"] == 1
        assert data["by_size"]["S"]["count"] == 0

    def test_state_estimates_takes_precedence_over_label(self, root):
        """state.estimates wins over size-* label when no JSON."""
        ticket = _ticket(101, 900)
        ticket["labels"] = [{"name": "size-S"}]
        _state_with_estimates(root, "sprint-1", [ticket], estimates={"101": {"size": "L"}})
        # no JSON — state.estimates should win over label
        data = _call(root).json()
        assert data["by_size"]["L"]["count"] == 1
        assert data["by_size"]["S"]["count"] == 0

    def test_no_size_source_excludes_ticket(self, root):
        """Ticket with no JSON, no state.estimates, no label is excluded."""
        ticket = _ticket(101, 900)
        _state(root, "sprint-1", [ticket])
        # no _est(), no label, no state.estimates
        data = _call(root).json()
        assert all(data["by_size"][sz]["count"] == 0 for sz in ("S", "M", "L", "XL"))
