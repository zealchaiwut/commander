"""Tests for issue #1332 — Rebuild calibration cache to surface full sprint history.

AC coverage:
  (a) version-bump invalidation — cache with old version discarded on first read
  (b) rebuild endpoint — POST /api/maintenance/calibration/rebuild returns correct count summary
  (c) CLI dry-run output matches live-run counts (verified via service layer)
  (d) idempotency — two consecutive rebuilds yield same result
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
_ROUTERS_ROOT = _DASHBOARD_ROOT / "routers"

for _p in (str(_DASHBOARD_ROOT), str(_ROUTERS_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _done_ticket(
    num: int,
    coder_min: float,
    tester_min: float = 0.0,
    start: str = "2026-01-10T10:00:00Z",
) -> dict:
    """Build a 'done' sprint issue dict with timing."""
    s = datetime.fromisoformat(start.rstrip("Z")).replace(tzinfo=timezone.utc)
    coder_end = s + timedelta(minutes=coder_min)
    tester_end = coder_end + timedelta(minutes=tester_min)
    return {
        "number": num,
        "title": f"Issue #{num}",
        "status": "done",
        "coder_started_at": start,
        "coder_finished_at": coder_end.isoformat().replace("+00:00", "Z"),
        "tester_started_at": coder_end.isoformat().replace("+00:00", "Z"),
        "tester_finished_at": tester_end.isoformat().replace("+00:00", "Z"),
    }


def _write_sprint_state(
    project_root: Path,
    sprint_label: str,
    issues: list[dict],
    *,
    archive: bool = False,
    estimates: dict | None = None,
) -> None:
    import re
    n = re.search(r"(\d+)", sprint_label).group(1)
    sprints_dir = project_root / ".commander" / "sprints"
    if archive:
        sprints_dir = sprints_dir / "archive"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    state: dict = {
        "sprint_label": sprint_label,
        "sprint_number": int(n),
        "project": "owner/myrepo",
        "start_timestamp": "2026-01-10T10:00:00Z",
        "wall_clock_secs": 86400.0,
        "issues": issues,
    }
    if estimates is not None:
        state["estimates"] = estimates
    (sprints_dir / f"sprint-{n}-state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )


def _write_estimate(project_root: Path, num: int, size: str) -> None:
    est_dir = project_root / ".commander" / "estimates"
    est_dir.mkdir(parents=True, exist_ok=True)
    (est_dir / f"issue-{num}.json").write_text(
        json.dumps({"issue_number": num, "size": size}), encoding="utf-8"
    )


_DEFAULT_MINUTES = {"S": 5, "M": 15, "L": 30, "XL": 60}


# ---------------------------------------------------------------------------
# AC (a) — version-bump invalidation: old cache discarded on first read
# ---------------------------------------------------------------------------

class TestVersionBumpInvalidation:
    def test_cache_with_old_version_returns_empty(self, tmp_path):
        """Cache file with version != current version yields empty cache on load."""
        import server as srv

        commander = tmp_path / ".commander"
        commander.mkdir()
        cache_path = commander / "calibration_cache.json"
        # Write a cache claiming version=1 with non-empty data.
        old_cache = {
            "version": 1,
            "archive_bootstrap_done": True,
            "by_size": {
                "S": {"count": 99, "min_minutes": 1.0, "avg_minutes": 5.0, "max_minutes": 10.0},
                "M": {"count": 50, "min_minutes": 5.0, "avg_minutes": 15.0, "max_minutes": 30.0},
                "L": {"count": 20, "min_minutes": 10.0, "avg_minutes": 30.0, "max_minutes": 60.0},
                "XL": {"count": 5, "min_minutes": 30.0, "avg_minutes": 60.0, "max_minutes": 120.0},
            },
            "processed": ["sprint-1-state.json/101", "sprint-1-state.json/102"],
            "points": [{"issue_number": 101, "estimated_size": "S", "estimated_minutes": 5, "actual_minutes": 3.0}],
        }
        cache_path.write_text(json.dumps(old_cache), encoding="utf-8")

        result = srv._load_calibration_cache(commander)

        # Should return empty cache — version mismatch discards old data.
        assert result["by_size"]["S"]["count"] == 0
        assert result["processed"] == []
        assert result["points"] == []

    def test_cache_with_current_version_is_loaded(self, tmp_path):
        """Cache file with current version is loaded normally."""
        import server as srv

        commander = tmp_path / ".commander"
        commander.mkdir()
        cache_path = commander / "calibration_cache.json"
        current_version = srv._CALIBRATION_CACHE_VERSION
        cache = {
            "version": current_version,
            "archive_bootstrap_done": True,
            "by_size": {
                "S": {"count": 7, "min_minutes": 2.0, "avg_minutes": 4.0, "max_minutes": 8.0},
                "M": {"count": 0, "min_minutes": None, "avg_minutes": None, "max_minutes": None},
                "L": {"count": 0, "min_minutes": None, "avg_minutes": None, "max_minutes": None},
                "XL": {"count": 0, "min_minutes": None, "avg_minutes": None, "max_minutes": None},
            },
            "processed": ["sprint-1-state.json/101"],
            "points": [],
        }
        cache_path.write_text(json.dumps(cache), encoding="utf-8")

        result = srv._load_calibration_cache(commander)
        assert result["by_size"]["S"]["count"] == 7

    def test_current_version_is_2(self):
        """_CALIBRATION_CACHE_VERSION must be 2 (bumped from 1 by this ticket)."""
        import server as srv
        assert srv._CALIBRATION_CACHE_VERSION == 2


# ---------------------------------------------------------------------------
# AC (b) — rebuild endpoint returns correct count summary
# ---------------------------------------------------------------------------

class TestRebuildEndpoint:
    def _call(self, project_root: Path, project_slug: str = "myrepo") -> "Response":
        import server as srv
        from starlette.testclient import TestClient

        # The route handler calls routers.maintenance_service (package-qualified path).
        with (
            patch("routers.maintenance_service._resolve_project_slug", return_value="owner/myrepo"),
            patch("routers.maintenance_service._project_root_path", return_value=project_root),
            patch("routers.maintenance_service._get_configured_minutes", return_value=_DEFAULT_MINUTES),
        ):
            client = TestClient(srv.app)
            return client.post(f"/api/maintenance/calibration/rebuild?project={project_slug}")

    def test_endpoint_returns_200(self, tmp_path):
        """POST /api/maintenance/calibration/rebuild returns HTTP 200."""
        _write_sprint_state(tmp_path, "sprint-1", [])
        resp = self._call(tmp_path)
        assert resp.status_code == 200

    def test_endpoint_returns_total_and_by_size(self, tmp_path):
        """Response body has 'total' and 'by_size' fields."""
        _write_sprint_state(tmp_path, "sprint-1", [])
        data = self._call(tmp_path).json()
        assert "total" in data
        assert "by_size" in data
        for sz in ("S", "M", "L", "XL"):
            assert sz in data["by_size"]

    def test_endpoint_count_reflects_completed_tickets(self, tmp_path):
        """Returned total equals number of completed tickets with size + timing."""
        _write_sprint_state(tmp_path, "sprint-1", [
            _done_ticket(101, coder_min=10),
            _done_ticket(102, coder_min=15),
            _done_ticket(103, coder_min=20),
        ])
        _write_estimate(tmp_path, 101, "S")
        _write_estimate(tmp_path, 102, "M")
        _write_estimate(tmp_path, 103, "M")
        data = self._call(tmp_path).json()
        assert data["total"] == 3
        assert data["by_size"]["S"] == 1
        assert data["by_size"]["M"] == 2

    def test_endpoint_GET_returns_405(self, tmp_path):
        """GET to the rebuild endpoint is method-not-allowed (route exists, POST-only)."""
        import server as srv
        from starlette.testclient import TestClient
        client = TestClient(srv.app, raise_server_exceptions=False)
        resp = client.get("/api/maintenance/calibration/rebuild?project=myrepo")
        assert resp.status_code == 405

    def test_endpoint_archives_included(self, tmp_path):
        """Rebuild endpoint counts tickets from archive/ subdirectory."""
        _write_sprint_state(tmp_path, "sprint-1", [_done_ticket(201, coder_min=12)], archive=True)
        _write_estimate(tmp_path, 201, "L")
        data = self._call(tmp_path).json()
        assert data["total"] == 1
        assert data["by_size"]["L"] == 1


# ---------------------------------------------------------------------------
# AC (c) — CLI dry-run output matches live-run counts
# ---------------------------------------------------------------------------

class TestDryRunMatchesLive:
    def test_dry_run_returns_same_counts_as_live(self, tmp_path):
        """rebuild_calibration_cache(dry_run=True) returns identical counts to live run."""
        from maintenance_service import rebuild_calibration_cache  # type: ignore[import]

        _write_sprint_state(tmp_path, "sprint-1", [
            _done_ticket(101, coder_min=10),
            _done_ticket(102, coder_min=20),
        ])
        _write_estimate(tmp_path, 101, "S")
        _write_estimate(tmp_path, 102, "M")

        dry = rebuild_calibration_cache(tmp_path, _DEFAULT_MINUTES, dry_run=True)
        live = rebuild_calibration_cache(tmp_path, _DEFAULT_MINUTES, dry_run=False)

        assert dry == live

    def test_dry_run_writes_no_cache_file(self, tmp_path):
        """dry_run=True leaves calibration_cache.json unchanged (no write)."""
        from maintenance_service import rebuild_calibration_cache  # type: ignore[import]

        _write_sprint_state(tmp_path, "sprint-1", [_done_ticket(101, coder_min=10)])
        _write_estimate(tmp_path, 101, "S")

        cache_path = tmp_path / ".commander" / "calibration_cache.json"
        assert not cache_path.exists()

        rebuild_calibration_cache(tmp_path, _DEFAULT_MINUTES, dry_run=True)

        assert not cache_path.exists(), "dry_run must not write calibration_cache.json"

    def test_live_run_writes_cache_file(self, tmp_path):
        """dry_run=False saves calibration_cache.json to disk."""
        from maintenance_service import rebuild_calibration_cache  # type: ignore[import]

        _write_sprint_state(tmp_path, "sprint-1", [_done_ticket(101, coder_min=10)])
        _write_estimate(tmp_path, 101, "S")

        rebuild_calibration_cache(tmp_path, _DEFAULT_MINUTES, dry_run=False)

        cache_path = tmp_path / ".commander" / "calibration_cache.json"
        assert cache_path.is_file(), "live run must write calibration_cache.json"

    def test_dry_run_summary_has_correct_shape(self, tmp_path):
        """Dry-run result has 'total' int and 'by_size' dict with all four keys."""
        from maintenance_service import rebuild_calibration_cache  # type: ignore[import]

        _write_sprint_state(tmp_path, "sprint-1", [])
        result = rebuild_calibration_cache(tmp_path, _DEFAULT_MINUTES, dry_run=True)
        assert isinstance(result["total"], int)
        assert all(isinstance(result["by_size"][sz], int) for sz in ("S", "M", "L", "XL"))


# ---------------------------------------------------------------------------
# AC (d) — idempotency: two consecutive rebuilds yield same result
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_two_rebuilds_yield_same_total(self, tmp_path):
        """Running rebuild twice on the same data produces the same total count."""
        from maintenance_service import rebuild_calibration_cache  # type: ignore[import]

        _write_sprint_state(tmp_path, "sprint-1", [
            _done_ticket(101, coder_min=10),
            _done_ticket(102, coder_min=20),
        ])
        _write_estimate(tmp_path, 101, "S")
        _write_estimate(tmp_path, 102, "M")
        _write_sprint_state(tmp_path, "sprint-2", [
            _done_ticket(201, coder_min=15),
        ], archive=True)
        _write_estimate(tmp_path, 201, "L")

        first = rebuild_calibration_cache(tmp_path, _DEFAULT_MINUTES, dry_run=False)
        second = rebuild_calibration_cache(tmp_path, _DEFAULT_MINUTES, dry_run=False)

        assert first["total"] == second["total"]
        assert first["by_size"] == second["by_size"]

    def test_second_rebuild_same_by_size_counts(self, tmp_path):
        """Per-size counts are identical across two consecutive rebuilds."""
        from maintenance_service import rebuild_calibration_cache  # type: ignore[import]

        for i, sz in enumerate(["S", "M", "L"], start=101):
            _write_sprint_state(tmp_path, f"sprint-{i-100}", [_done_ticket(i, coder_min=5)])
            _write_estimate(tmp_path, i, sz)

        r1 = rebuild_calibration_cache(tmp_path, _DEFAULT_MINUTES, dry_run=False)
        r2 = rebuild_calibration_cache(tmp_path, _DEFAULT_MINUTES, dry_run=False)

        assert r1["by_size"]["S"] == r2["by_size"]["S"]
        assert r1["by_size"]["M"] == r2["by_size"]["M"]
        assert r1["by_size"]["L"] == r2["by_size"]["L"]

    def test_rebuild_clears_stale_points_before_rescan(self, tmp_path):
        """Second rebuild does not accumulate points from previous run (no double-count)."""
        import server as srv
        from maintenance_service import rebuild_calibration_cache  # type: ignore[import]

        _write_sprint_state(tmp_path, "sprint-1", [_done_ticket(101, coder_min=10)])
        _write_estimate(tmp_path, 101, "S")

        rebuild_calibration_cache(tmp_path, _DEFAULT_MINUTES, dry_run=False)
        rebuild_calibration_cache(tmp_path, _DEFAULT_MINUTES, dry_run=False)

        cache_path = tmp_path / ".commander" / "calibration_cache.json"
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        # One ticket → exactly one point after two rebuilds.
        assert len(cache["points"]) == 1
        assert cache["by_size"]["S"]["count"] == 1

    def test_rebuild_uses_new_size_resolver(self, tmp_path):
        """Rebuild uses _resolve_calibration_size hierarchy: JSON > state.estimates > label."""
        from maintenance_service import rebuild_calibration_cache  # type: ignore[import]

        # Ticket with size-* label and state.estimates but no JSON file.
        # state.estimates should win over label per priority order.
        ticket = _done_ticket(301, coder_min=10)
        ticket["labels"] = [{"name": "size-S"}]
        _write_sprint_state(
            tmp_path, "sprint-1", [ticket],
            estimates={"301": {"size": "L"}},
        )
        # No estimate JSON file → falls back to state.estimates (L).

        result = rebuild_calibration_cache(tmp_path, _DEFAULT_MINUTES, dry_run=False)
        assert result["by_size"]["L"] == 1
        assert result["by_size"]["S"] == 0


# ---------------------------------------------------------------------------
# Incremental calls after rebuild must not double-count (AC9)
# ---------------------------------------------------------------------------

class TestNoDoubleCountAfterRebuild:
    def test_incremental_refresh_after_rebuild_adds_only_new(self, tmp_path):
        """After a rebuild, GET /analytics/calibration adds only new tickets."""
        import server as srv
        from starlette.testclient import TestClient
        from maintenance_service import rebuild_calibration_cache  # type: ignore[import]

        # Setup: one ticket in sprint-1.
        _write_sprint_state(tmp_path, "sprint-1", [_done_ticket(101, coder_min=10)])
        _write_estimate(tmp_path, 101, "M")

        # Rebuild fills the cache with 1 ticket.
        rebuild_calibration_cache(tmp_path, _DEFAULT_MINUTES, dry_run=False)

        # Now add a second sprint with a new ticket.
        _write_sprint_state(tmp_path, "sprint-2", [_done_ticket(102, coder_min=20)])
        _write_estimate(tmp_path, 102, "M")

        # Incremental GET should see 2 tickets (1 cached + 1 new), not 3.
        with (
            patch("server._resolve_project_slug", return_value="owner/myrepo"),
            patch("server._project_root_path", return_value=tmp_path),
            patch("server._settings_repo.get_setting", return_value={}),
        ):
            client = TestClient(srv.app)
            resp = client.get("/api/projects/myrepo/analytics/calibration")

        data = resp.json()
        assert data["by_size"]["M"]["count"] == 2
        assert len(data["points"]) == 2
