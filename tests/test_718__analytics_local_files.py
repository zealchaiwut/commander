"""Tests for issue #718 — wire analytics calculations to local sprint/estimate files.

Each test is anchored to a specific acceptance criterion. The implementation
must satisfy the assertion; tests are never softened to pass.

AC coverage:
  Data sourcing
    AC-DS1 — metrics + calibration read from local files only; no Neon query
    AC-DS2 — scope filter (since/until/sprint) applied before any metric
  Metrics Panel
    AC-M1  — first-pass rate from rejections inferred via status_history,
             category, failure_reason (not only tester_attempt_count)
    AC-M2  — tester_attempt_count persisted to status json (sprint_manager)
    AC-M3  — rework rate + 2+ rounds secondary count
    AC-M4  — avg duration by agent (coder/tester), broken down by est size
    AC-M5  — throughput: avg tickets/sprint + avg sprint length
    AC-M6  — cost: status-file token counts × MODEL_PRICE_MAP price (model joined)
  Calibration Panel
    AC-C1  — scatter points (estimated_minutes, actual_minutes, estimated_size);
             actual_minutes = coder_elapsed + tester_elapsed
    AC-C2  — by_size includes count/min/avg/max/configured_minutes
    AC-C3  — calibration source is files only; Neon not queried
  Trends
    AC-T1  — velocity: tickets completed per sprint, chronological
    AC-T2  — failure-rate: failed ÷ total per sprint, chronological
  Empty / populated states
    AC-E1  — populated state when a finished sprint matches scope
    AC-E2  — empty state only when no sprint matches scope
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
_SM_ROOT = _REPO_ROOT / "services" / "sprint_manager"

for _p in (str(_DASHBOARD_ROOT), str(_SM_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _write_state(
    project_root: Path,
    sprint_label: str,
    issues: list[dict],
    *,
    wall_clock_secs: float = 86400.0,
    start_timestamp: str = "2026-01-10T10:00:00Z",
    total_tokens_in: int = 0,
    total_tokens_out: int = 0,
    project: str = "owner/repo",
) -> None:
    import re
    m = re.search(r"(\d+)", sprint_label)
    n = m.group(1) if m else sprint_label
    sprints_dir = project_root / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "sprint_label": sprint_label,
        "sprint_number": int(n),
        "project": project,
        "start_timestamp": start_timestamp,
        "wall_clock_secs": wall_clock_secs,
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
        "issues": issues,
    }
    (sprints_dir / f"sprint-{n}-state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )


def _write_estimate(project_root: Path, issue_num: int, size: str) -> None:
    est_dir = project_root / ".commander" / "estimates"
    est_dir.mkdir(parents=True, exist_ok=True)
    (est_dir / f"issue-{issue_num}.json").write_text(
        json.dumps({"issue_number": issue_num, "size": size}), encoding="utf-8"
    )


def _done_issue(num, *, coder_min=15, tester_min=5, start="2026-01-10T10:00:00Z",
                **extra) -> dict:
    """A completed ticket with coder/tester durations expressed in minutes."""
    from datetime import datetime, timedelta, timezone
    s = datetime.fromisoformat(start.rstrip("Z")).replace(tzinfo=timezone.utc)
    coder_end = s + timedelta(minutes=coder_min)
    tester_end = coder_end + timedelta(minutes=tester_min)
    issue = {
        "number": num,
        "title": f"Ticket {num}",
        "status": "done",
        "coder_started_at": start,
        "coder_finished_at": coder_end.isoformat().replace("+00:00", "Z"),
        "tester_started_at": coder_end.isoformat().replace("+00:00", "Z"),
        "tester_finished_at": tester_end.isoformat().replace("+00:00", "Z"),
    }
    issue.update(extra)
    return issue


def _metrics(project_root: Path, token_rows=None, slug="repo", **params):
    import server as srv
    from starlette.testclient import TestClient
    with (
        patch("server._resolve_project_slug", return_value="owner/repo"),
        patch("server._project_root_path", return_value=project_root),
        patch("server.db.get_token_usage_by_agent_model", return_value=token_rows or []),
    ):
        client = TestClient(srv.app)
        return client.get(f"/api/projects/{slug}/analytics/metrics", params=params)


def _calibration(project_root: Path, slug="repo", **params):
    import server as srv
    from starlette.testclient import TestClient
    # Issue #758 removed the Neon path from server entirely — calibration reads
    # local files unconditionally, so there is no availability flag to toggle.
    with (
        patch("server._resolve_project_slug", return_value="owner/repo"),
        patch("server._project_root_path", return_value=project_root),
    ):
        client = TestClient(srv.app)
        return client.get(f"/api/projects/{slug}/analytics/calibration", params=params)


def _trends(project_root: Path, repo="owner/repo", **params):
    import server as srv
    from starlette.testclient import TestClient
    with (
        patch("server.projects_module.load_projects", return_value=[{"repo": repo}]),
        patch("server._project_root_path", return_value=project_root),
    ):
        client = TestClient(srv.app)
        return client.get("/api/metrics/sprints", params=params)


# ---------------------------------------------------------------------------
# AC-DS1 / AC-C3 — no Neon; files are the only source
# ---------------------------------------------------------------------------

def test_calibration_reads_files_with_neon_unavailable(tmp_path):
    _write_state(tmp_path, "sprint-1", [_done_issue(101, coder_min=10, tester_min=10)])
    _write_estimate(tmp_path, 101, "M")
    # Calibration reads local files only (no Neon dependency at all post-#758).
    data = _calibration(tmp_path).json()
    assert data["by_size"]["M"]["count"] == 1
    assert len(data["points"]) == 1


def test_calibration_does_not_open_neon_session(tmp_path):
    import server as srv
    _write_state(tmp_path, "sprint-1", [_done_issue(101)])
    _write_estimate(tmp_path, 101, "S")
    # Issue #758: the server has no Neon repo handle, so the calibration path can
    # never open a Neon session — the file source is structurally the only one.
    assert not hasattr(srv, "_sprint_repo"), (
        "server must not retain a live Neon sprint_repo handle (#758)"
    )
    # And the endpoint still serves data from local files.
    data = _calibration(tmp_path).json()
    assert data["by_size"]["S"]["count"] == 1


def test_metrics_reads_files_no_neon(tmp_path):
    _write_state(tmp_path, "sprint-1", [_done_issue(101)])
    data = _metrics(tmp_path).json()
    assert data["first_pass_rate"]["total_completed"] == 1


# ---------------------------------------------------------------------------
# AC-DS2 — scope filter applied before metrics
# ---------------------------------------------------------------------------

def test_sprint_filter_narrows_metrics(tmp_path):
    _write_state(tmp_path, "sprint-1", [_done_issue(101)], start_timestamp="2026-01-10T10:00:00Z")
    _write_state(tmp_path, "sprint-2", [_done_issue(201), _done_issue(202)],
                 start_timestamp="2026-02-10T10:00:00Z")
    data = _metrics(tmp_path, sprint="sprint-2").json()
    assert data["first_pass_rate"]["total_completed"] == 2


def test_since_until_narrows_metrics(tmp_path):
    _write_state(tmp_path, "sprint-1", [_done_issue(101)], start_timestamp="2026-01-10T10:00:00Z")
    _write_state(tmp_path, "sprint-2", [_done_issue(201)], start_timestamp="2026-03-10T10:00:00Z")
    data = _metrics(tmp_path, since="2026-02-01", until="2026-04-01").json()
    assert data["first_pass_rate"]["total_completed"] == 1


# ---------------------------------------------------------------------------
# AC-M1 — first-pass rate inferred from status_history/category/failure_reason
# ---------------------------------------------------------------------------

def test_first_pass_from_status_history_rejection(tmp_path):
    # No tester_attempt_count, but status_history shows a tester rejection.
    rejected = _done_issue(101, status_history=[
        {"status": "tester_rejected", "category": "TESTER_REJECTED"}])
    clean = _done_issue(102)
    _write_state(tmp_path, "sprint-1", [rejected, clean])
    data = _metrics(tmp_path).json()
    assert data["first_pass_rate"]["total_completed"] == 2
    assert data["first_pass_rate"]["passed"] == 1  # only the clean one


def test_first_pass_from_category_field(tmp_path):
    rejected = _done_issue(101, category="TESTER_REJECTED")
    _write_state(tmp_path, "sprint-1", [rejected])
    data = _metrics(tmp_path).json()
    assert data["first_pass_rate"]["passed"] == 0
    assert data["rework_rate"]["count"] == 1


def test_first_pass_from_failure_reason_field(tmp_path):
    rejected = _done_issue(101, failure_reason="tester rejected the merge")
    _write_state(tmp_path, "sprint-1", [rejected])
    data = _metrics(tmp_path).json()
    assert data["first_pass_rate"]["passed"] == 0


def test_passed_plus_rejected_equals_total(tmp_path):
    _write_state(tmp_path, "sprint-1", [
        _done_issue(101),
        _done_issue(102, tester_attempt_count=2),
        _done_issue(103, category="TESTER_REJECTED"),
    ])
    d = _metrics(tmp_path).json()
    assert d["first_pass_rate"]["passed"] + d["rework_rate"]["count"] == \
        d["first_pass_rate"]["total_completed"] == 3


# ---------------------------------------------------------------------------
# AC-M3 — rework rate + 2+ rounds secondary count
# ---------------------------------------------------------------------------

def test_rework_2plus_count(tmp_path):
    _write_state(tmp_path, "sprint-1", [
        _done_issue(101),                              # first pass
        _done_issue(102, tester_attempt_count=2),      # 1 rejection
        _done_issue(103, tester_attempt_count=3),      # 2 rejections → 2plus
    ])
    d = _metrics(tmp_path).json()
    assert d["rework_rate"]["count"] == 2
    assert d["rework_rate"]["rework_2plus"] == 1
    assert abs(d["rework_rate"]["rate"] - (2 / 3)) < 0.001


# ---------------------------------------------------------------------------
# AC-M4 — avg duration by agent, broken down by estimated size
# ---------------------------------------------------------------------------

def test_avg_duration_coder_and_tester(tmp_path):
    _write_state(tmp_path, "sprint-1", [
        _done_issue(101, coder_min=10, tester_min=4),
        _done_issue(102, coder_min=20, tester_min=6),
    ])
    d = _metrics(tmp_path).json()["avg_duration"]
    assert abs(d["coder_minutes"] - 15.0) < 0.01
    assert abs(d["tester_minutes"] - 5.0) < 0.01


def test_avg_duration_by_size(tmp_path):
    _write_state(tmp_path, "sprint-1", [
        _done_issue(101, coder_min=10),
        _done_issue(102, coder_min=30),
        _done_issue(103, coder_min=8),
    ])
    _write_estimate(tmp_path, 101, "M")
    _write_estimate(tmp_path, 102, "M")
    _write_estimate(tmp_path, 103, "S")
    by_size = _metrics(tmp_path).json()["avg_duration"]["coder_by_size"]
    assert abs(by_size["M"] - 20.0) < 0.01   # (10+30)/2
    assert abs(by_size["S"] - 8.0) < 0.01


# ---------------------------------------------------------------------------
# AC-M5 — throughput: avg tickets/sprint + avg sprint length
# ---------------------------------------------------------------------------

def test_throughput(tmp_path):
    _write_state(tmp_path, "sprint-1", [_done_issue(101), _done_issue(102)],
                 wall_clock_secs=86400.0, start_timestamp="2026-01-10T10:00:00Z")
    _write_state(tmp_path, "sprint-2", [_done_issue(201), _done_issue(202),
                 _done_issue(203), _done_issue(204)],
                 wall_clock_secs=86400.0 * 3, start_timestamp="2026-02-10T10:00:00Z")
    thr = _metrics(tmp_path).json()["throughput"]
    assert abs(thr["avg_tickets_per_sprint"] - 3.0) < 0.01  # (2+4)/2
    assert abs(thr["avg_sprint_length_days"] - 2.0) < 0.01  # (1+3)/2


# ---------------------------------------------------------------------------
# AC-M6 — cost from status-file tokens × MODEL_PRICE_MAP (model joined from db)
# ---------------------------------------------------------------------------

def test_cost_uses_status_tokens_and_model_price(tmp_path):
    import server as srv
    in_tok, out_tok = 1_000_000, 500_000
    _write_state(tmp_path, "sprint-1", [_done_issue(101)],
                 total_tokens_in=in_tok, total_tokens_out=out_tok)
    # Single model in token_usage — model_name is joined for pricing only.
    rows = [{"agent_role": "coder", "model_name": "claude-sonnet-4-6",
             "total_input": 700_000, "total_output": 300_000}]
    price_in, price_out = srv.MODEL_PRICE_MAP["claude-sonnet-4-6"]
    expected = in_tok * price_in + out_tok * price_out
    cost = _metrics(tmp_path, token_rows=rows).json()["cost"]
    assert abs(cost["per_sprint"]["total"] - round(expected, 4)) < 0.01


def test_cost_zero_when_status_tokens_zero(tmp_path):
    rows = [{"agent_role": "coder", "model_name": "claude-sonnet-4-6",
             "total_input": 700_000, "total_output": 300_000}]
    _write_state(tmp_path, "sprint-1", [_done_issue(101)],
                 total_tokens_in=0, total_tokens_out=0)
    cost = _metrics(tmp_path, token_rows=rows).json()["cost"]
    assert cost["per_sprint"]["total"] == 0.0


# ---------------------------------------------------------------------------
# AC-C1 — calibration scatter points
# ---------------------------------------------------------------------------

def test_calibration_point_actual_is_coder_plus_tester(tmp_path):
    _write_state(tmp_path, "sprint-1", [_done_issue(101, coder_min=12, tester_min=8)])
    _write_estimate(tmp_path, 101, "M")
    point = _calibration(tmp_path).json()["points"][0]
    assert point["issue_number"] == 101
    assert point["estimated_size"] == "M"
    assert abs(point["actual_minutes"] - 20.0) < 0.01     # 12 + 8
    assert point["estimated_minutes"] == 15               # configured default M


# ---------------------------------------------------------------------------
# AC-C2 — by_size aggregates
# ---------------------------------------------------------------------------

def test_calibration_by_size_aggregates(tmp_path):
    _write_state(tmp_path, "sprint-1", [
        _done_issue(101, coder_min=1, tester_min=0),
        _done_issue(102, coder_min=2, tester_min=0),
        _done_issue(103, coder_min=3, tester_min=0),
    ])
    for n in (101, 102, 103):
        _write_estimate(tmp_path, n, "L")
    entry = _calibration(tmp_path).json()["by_size"]["L"]
    assert entry["count"] == 3
    assert abs(entry["min_minutes"] - 1.0) < 0.01
    assert abs(entry["avg_minutes"] - 2.0) < 0.01
    assert abs(entry["max_minutes"] - 3.0) < 0.01
    assert entry["configured_minutes"] == 30  # default L


def test_calibration_all_sizes_present_with_null_stats(tmp_path):
    _write_state(tmp_path, "sprint-1", [_done_issue(101)])
    _write_estimate(tmp_path, 101, "S")
    by_size = _calibration(tmp_path).json()["by_size"]
    for sz in ("S", "M", "L", "XL"):
        assert sz in by_size
    assert by_size["XL"]["count"] == 0
    assert by_size["XL"]["min_minutes"] is None
    assert by_size["XL"]["avg_minutes"] is None
    assert by_size["XL"]["max_minutes"] is None


def test_calibration_sprint_filter(tmp_path):
    _write_state(tmp_path, "sprint-1", [_done_issue(101)], start_timestamp="2026-01-10T10:00:00Z")
    _write_state(tmp_path, "sprint-2", [_done_issue(201)], start_timestamp="2026-02-10T10:00:00Z")
    _write_estimate(tmp_path, 101, "M")
    _write_estimate(tmp_path, 201, "M")
    data = _calibration(tmp_path, sprint="sprint-1").json()
    assert data["by_size"]["M"]["count"] == 1
    assert data["points"][0]["issue_number"] == 101


# ---------------------------------------------------------------------------
# AC-T1 / AC-T2 — trends: velocity + failure-rate per sprint, chronological
# ---------------------------------------------------------------------------

def test_trends_velocity_and_failure_per_sprint(tmp_path):
    _write_state(tmp_path, "sprint-1", [
        _done_issue(101), _done_issue(102),
        {"number": 103, "status": "failed"},
    ], start_timestamp="2026-01-10T10:00:00Z")
    _write_state(tmp_path, "sprint-2", [
        _done_issue(201),
    ], start_timestamp="2026-02-10T10:00:00Z")
    rows = _trends(tmp_path, from_="2026-01-01", to="2026-12-31",
                   **{"from": "2026-01-01"}).json()
    by_label = {r["sprint_label"]: r for r in rows}
    assert by_label["sprint-1"]["ticket_outcomes_breakdown"]["done"] == 2
    assert by_label["sprint-1"]["ticket_outcomes_breakdown"]["failed"] == 1
    assert by_label["sprint-2"]["ticket_outcomes_breakdown"]["done"] == 1


# ---------------------------------------------------------------------------
# AC-E1 / AC-E2 — populated vs empty states
# ---------------------------------------------------------------------------

def test_populated_state_when_sprint_matches(tmp_path):
    _write_state(tmp_path, "sprint-1", [_done_issue(101)])
    d = _metrics(tmp_path).json()
    assert d["first_pass_rate"]["total_completed"] >= 1


def test_empty_state_when_no_sprint_matches_scope(tmp_path):
    _write_state(tmp_path, "sprint-1", [_done_issue(101)],
                 start_timestamp="2026-01-10T10:00:00Z")
    # Scope window with no sprint inside it.
    d = _metrics(tmp_path, since="2025-01-01", until="2025-12-31").json()
    assert d["first_pass_rate"]["total_completed"] == 0
    cal = _calibration(tmp_path, since="2025-01-01", until="2025-12-31").json()
    assert cal["points"] == []
    assert all(cal["by_size"][sz]["count"] == 0 for sz in ("S", "M", "L", "XL"))


# ---------------------------------------------------------------------------
# AC-M2 — tester_attempt_count persisted to status json (sprint_manager)
# ---------------------------------------------------------------------------

def test_issue_state_serializes_tester_attempt_count():
    import services.sprint_manager.sprint_manager as sm
    iss = sm.IssueState(number=1, title="T")
    assert iss.tester_attempt_count == 0
    d = iss.to_dict()
    assert "tester_attempt_count" in d
    iss.tester_attempt_count = 2
    restored = sm.IssueState.from_dict(iss.to_dict())
    assert restored.tester_attempt_count == 2


def test_tester_attempt_count_increment_present_in_dispatch():
    """The tester-dispatch lifecycle must bump tester_attempt_count (AC-M2)."""
    src = (_SM_ROOT / "sprint_manager.py").read_text(encoding="utf-8")
    # The increment must live next to the tester_dispatched transition.
    idx = src.index('set_agent_status("tester_dispatched")')
    window = src[idx:idx + 400]
    assert "tester_attempt_count += 1" in window


# ---------------------------------------------------------------------------
# Calibration cache — archive bootstrap + incremental live ingest
# ---------------------------------------------------------------------------

def _write_archive_state(
    project_root: Path,
    sprint_label: str,
    issues: list[dict],
    *,
    start_timestamp: str = "2026-01-10T10:00:00Z",
) -> None:
    import re
    m = re.search(r"(\d+)", sprint_label)
    n = m.group(1) if m else sprint_label
    archive_dir = project_root / ".commander" / "sprints" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "sprint_label": sprint_label,
        "sprint_number": int(n),
        "start_timestamp": start_timestamp,
        "issues": issues,
    }
    (archive_dir / f"sprint-{n}-state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )


def test_calibration_archive_bootstrap_once(tmp_path):
    """Archived state files are ingested once into calibration_cache.json."""
    _write_archive_state(tmp_path, "sprint-1", [_done_issue(101, coder_min=10, tester_min=5)])
    _write_estimate(tmp_path, 101, "S")
    data = _calibration(tmp_path).json()
    assert data["by_size"]["S"]["count"] == 1
    assert data["by_size"]["S"]["avg_minutes"] == 15.0

    cache_path = tmp_path / ".commander" / "calibration_cache.json"
    assert cache_path.is_file()
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cache["archive_bootstrap_done"] is True
    assert len(cache["processed"]) == 1

    # Second request must not re-scan archive — delete archived file and confirm
    # counts still come from cache.
    archive_file = tmp_path / ".commander" / "sprints" / "archive" / "sprint-1-state.json"
    archive_file.unlink()
    archive_file.parent.rmdir()
    data2 = _calibration(tmp_path).json()
    assert data2["by_size"]["S"]["count"] == 1


def test_calibration_incremental_live_without_full_rescan(tmp_path):
    """New live sprint tickets extend aggregates without re-reading archive."""
    _write_archive_state(tmp_path, "sprint-1", [_done_issue(101, coder_min=10, tester_min=5)])
    _write_estimate(tmp_path, 101, "M")
    first = _calibration(tmp_path).json()
    assert first["by_size"]["M"]["count"] == 1
    assert first["by_size"]["M"]["avg_minutes"] == 15.0

    _write_state(tmp_path, "sprint-2", [_done_issue(102, coder_min=20, tester_min=10)])
    _write_estimate(tmp_path, 102, "M")
    second = _calibration(tmp_path).json()
    assert second["by_size"]["M"]["count"] == 2
    assert second["by_size"]["M"]["avg_minutes"] == 22.5
    assert second["by_size"]["M"]["min_minutes"] == 15.0
    assert second["by_size"]["M"]["max_minutes"] == 30.0
    assert len(second["points"]) == 2


def test_calibration_filtered_scan_includes_archive(tmp_path):
    """Scoped sprint filter still reads matching files from archive/."""
    _write_archive_state(
        tmp_path, "sprint-42", [_done_issue(101, coder_min=12, tester_min=3)],
        start_timestamp="2026-02-10T10:00:00Z",
    )
    _write_estimate(tmp_path, 101, "L")
    data = _calibration(tmp_path, sprint="sprint-42").json()
    assert data["by_size"]["L"]["count"] == 1
    assert data["points"][0]["issue_number"] == 101
