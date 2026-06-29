"""Tests for issue #1167 — Fix misleading running_remaining_min name and
serial-mode summing in timeline projected_finish.

AC coverage:
  AC1  — `running_remaining_min` is renamed to `running_remaining`; the old
          name must not appear in timeline_service.py source.
  AC2  — In serial mode, projected_finish accounts for the SUM of all running
          issues' remaining times (not the max), so it cannot under-project
          when two issues run concurrently.
  AC3  — In pipeline mode, projected_finish continues to use the MAX of running
          issues' remaining times (overlapping execution — no change in behaviour).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVICES_DIR = REPO_ROOT / "services" / "sprint_manager"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR), str(SERVICES_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from apps.dashboard.routers import timeline_service  # noqa: E402

_TIMELINE_SRC = (REPO_ROOT / "apps" / "dashboard" / "routers" / "timeline_service.py").read_text()

_BASE = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)


# ── helpers ───────────────────────────────────────────────────────────────────

def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _make_issue(number: int, labels: list[str], size: str = "M") -> dict:
    all_labels = list(labels) + [f"size-{size}", "sprint-99"]
    return {
        "number": number,
        "title": f"Issue {number}",
        "state": "open",
        "labels": [{"name": lbl} for lbl in all_labels],
        "url": f"https://github.com/owner/repo/issues/{number}",
    }


def _make_run(
    issue_number: int,
    agent: str,
    started_at: datetime,
    duration_seconds: int | None = None,
    finished_at: datetime | None = None,
) -> dict:
    fin = finished_at or (
        started_at + timedelta(seconds=duration_seconds) if duration_seconds else None
    )
    return {
        "id": 1,
        "issue_number": issue_number,
        "sprint_label": "sprint-99",
        "agent": agent,
        "started_at": _iso(started_at),
        "finished_at": _iso(fin) if fin else None,
        "duration_seconds": duration_seconds or ((fin - started_at).seconds if fin else None),
        "outcome": "passed" if fin else None,
        "total_tokens": 1000,
        "risk_tier": None,
        "model_used": None,
        "routing_reason": None,
        "attempt_kind": "initial",
    }


def _default_settings(**overrides) -> dict:
    base = {
        "estimation_s_minutes": 5,
        "estimation_m_minutes": 15,
        "estimation_l_minutes": 30,
        "estimation_xl_minutes": 60,
        "estimation_buffer_pct": 0,
        "pipeline_mode": False,
        "reviewer_enabled": False,
    }
    base.update(overrides)
    return base


@pytest.fixture
def patch_timeline(monkeypatch):
    """Monkeypatch injectable helpers in timeline_service for isolated unit tests."""

    def _setup(
        issues: list[dict],
        agent_runs: list[dict] | None = None,
        settings: dict | None = None,
        sprint_row: dict | None = None,
    ):
        monkeypatch.setattr(timeline_service, "_get_sprint_issues", lambda lbl, proj: issues)
        monkeypatch.setattr(
            timeline_service, "_get_agent_runs", lambda lbl, proj=None: agent_runs or []
        )
        eff_settings = _default_settings(**(settings or {}))
        monkeypatch.setattr(timeline_service, "_get_settings", lambda proj: eff_settings)
        monkeypatch.setattr(
            timeline_service,
            "_get_sprint_row",
            lambda lbl, proj=None: sprint_row or {"label": "sprint-99", "started_at": _iso(_BASE), "state": "running"},
        )
        monkeypatch.setattr(timeline_service, "_get_calibration_records", lambda: [])
        monkeypatch.setattr(timeline_service, "_get_launch_issue_order", lambda lbl, proj: [])

    return _setup


# ── AC1: variable renamed ─────────────────────────────────────────────────────

def test_ac1_old_variable_name_absent_from_source():
    """Source must not contain `running_remaining_min` — the old misleading name."""
    assert "running_remaining_min" not in _TIMELINE_SRC, (
        "running_remaining_min still present in timeline_service.py — rename to running_remaining"
    )


def test_ac1_new_variable_name_present_in_source():
    """Source must use `running_remaining` (without _min suffix)."""
    assert "running_remaining" in _TIMELINE_SRC, (
        "running_remaining not found in timeline_service.py"
    )


# ── AC2: serial mode sums remaining of all running issues ─────────────────────

def test_ac2_serial_two_running_issues_sums_remaining(patch_timeline, monkeypatch):
    """Serial mode: projected_finish = now + sum(remaining1, remaining2) + wrap_up."""
    # Issue 1: in-progress, 15-min estimate, coder started 5 min ago → remaining = 10
    # Issue 2: SIT, 15-min estimate, coder started 3 min ago → remaining = 12
    fake_now = _BASE + timedelta(minutes=10)
    monkeypatch.setattr(timeline_service, "_server_now", lambda: fake_now)

    issue1_start = fake_now - timedelta(minutes=5)
    issue2_start = fake_now - timedelta(minutes=3)

    issues = [
        _make_issue(1, ["in-progress"], size="M"),
        _make_issue(2, ["SIT"], size="M"),
    ]
    runs = [
        _make_run(1, "coder", issue1_start),   # open run, 5 min elapsed → remaining 10
        _make_run(2, "coder", issue2_start),   # open run, 3 min elapsed → remaining 12
    ]
    patch_timeline(
        issues=issues,
        agent_runs=runs,
        settings={"pipeline_mode": False, "estimation_m_minutes": 15, "reviewer_enabled": False},
    )

    result = timeline_service.get_timeline("sprint-99", "owner/repo")

    pf_raw = datetime.fromisoformat(result["projected_finish"])
    if pf_raw.tzinfo is None:
        pf_raw = pf_raw.replace(tzinfo=timezone.utc)

    wu = result["wrap_up_estimate"]
    wu_total = wu["documenter"] + wu.get("reviewer", 0)

    # remaining1=10, remaining2=12; serial → sum = 22; wrap_up = 4*2=8 → total=30
    expected_min = 10.0 + 12.0 + wu_total
    expected = fake_now + timedelta(minutes=expected_min)
    delta = abs((pf_raw - expected).total_seconds())
    assert delta < 5, (
        f"Serial two-running-issue finish: got {pf_raw}, expected {expected} "
        f"(sum of remaining + wrap_up). Delta={delta}s. "
        "Likely still using max() instead of sum()."
    )


def test_ac2_serial_two_running_larger_minus_smaller_not_used(patch_timeline, monkeypatch):
    """Serial: projected_finish > max(remaining) + wrap_up (sum > max when both > 0)."""
    fake_now = _BASE + timedelta(minutes=10)
    monkeypatch.setattr(timeline_service, "_server_now", lambda: fake_now)

    # Both issues have elapsed < estimate, so both have positive remaining
    issue1_start = fake_now - timedelta(minutes=2)   # remaining = 13
    issue2_start = fake_now - timedelta(minutes=7)   # remaining = 8

    issues = [
        _make_issue(1, ["in-progress"], size="M"),
        _make_issue(2, ["SIT"], size="M"),
    ]
    runs = [
        _make_run(1, "coder", issue1_start),
        _make_run(2, "coder", issue2_start),
    ]
    patch_timeline(
        issues=issues,
        agent_runs=runs,
        settings={"pipeline_mode": False, "estimation_m_minutes": 15, "reviewer_enabled": False},
    )

    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    pf_raw = datetime.fromisoformat(result["projected_finish"])
    if pf_raw.tzinfo is None:
        pf_raw = pf_raw.replace(tzinfo=timezone.utc)

    wu = result["wrap_up_estimate"]
    wu_total = wu["documenter"] + wu.get("reviewer", 0)

    # max_remaining = 13; sum_remaining = 21
    # If using max: finish = now + 13 + wu_total
    # If using sum: finish = now + 21 + wu_total
    max_finish = fake_now + timedelta(minutes=13 + wu_total)
    # projected should be > max_finish (because it sums both)
    assert pf_raw > max_finish, (
        f"Serial projected_finish {pf_raw} should exceed max-based finish {max_finish}. "
        "Still using max() instead of sum()."
    )


def test_ac2_serial_single_running_issue_unchanged(patch_timeline, monkeypatch):
    """Serial mode with a single running issue: behaviour is unchanged (sum==max for 1 item)."""
    fake_now = _BASE + timedelta(minutes=5)
    monkeypatch.setattr(timeline_service, "_server_now", lambda: fake_now)

    issue1_start = fake_now - timedelta(minutes=5)  # remaining = 10

    issues = [_make_issue(1, ["in-progress"], size="M")]
    runs = [_make_run(1, "coder", issue1_start)]
    patch_timeline(
        issues=issues,
        agent_runs=runs,
        settings={"pipeline_mode": False, "estimation_m_minutes": 15, "reviewer_enabled": False},
    )

    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    pf_raw = datetime.fromisoformat(result["projected_finish"])
    if pf_raw.tzinfo is None:
        pf_raw = pf_raw.replace(tzinfo=timezone.utc)

    wu = result["wrap_up_estimate"]
    wu_total = wu["documenter"] + wu.get("reviewer", 0)

    expected = fake_now + timedelta(minutes=10 + wu_total)
    delta = abs((pf_raw - expected).total_seconds())
    assert delta < 5, (
        f"Single running issue serial: got {pf_raw}, expected {expected}. Delta={delta}s."
    )


# ── AC3: pipeline mode uses max of running remaining ─────────────────────────

def test_ac3_pipeline_two_running_issues_uses_max(patch_timeline, monkeypatch):
    """Pipeline mode: projected_finish uses MAX of running remaining (overlapping execution)."""
    fake_now = _BASE + timedelta(minutes=10)
    monkeypatch.setattr(timeline_service, "_server_now", lambda: fake_now)

    issue1_start = fake_now - timedelta(minutes=5)   # remaining = 10
    issue2_start = fake_now - timedelta(minutes=3)   # remaining = 12

    issues = [
        _make_issue(1, ["in-progress"], size="M"),
        _make_issue(2, ["SIT"], size="M"),
    ]
    runs = [
        _make_run(1, "coder", issue1_start),
        _make_run(2, "coder", issue2_start),
    ]
    patch_timeline(
        issues=issues,
        agent_runs=runs,
        settings={"pipeline_mode": True, "estimation_m_minutes": 15, "reviewer_enabled": False},
    )

    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    pf_raw = datetime.fromisoformat(result["projected_finish"])
    if pf_raw.tzinfo is None:
        pf_raw = pf_raw.replace(tzinfo=timezone.utc)

    wu = result["wrap_up_estimate"]
    wu_total = wu["documenter"] + wu.get("reviewer", 0)

    # pipeline: max_remaining=12; no queued issues → pipeline_queue_time=0
    expected_min = 12.0 + 0.0 + wu_total
    expected = fake_now + timedelta(minutes=expected_min)
    delta = abs((pf_raw - expected).total_seconds())
    assert delta < 5, (
        f"Pipeline two-running-issue finish: got {pf_raw}, expected {expected}. "
        f"Delta={delta}s."
    )


def test_ac3_pipeline_finish_less_than_serial_with_two_running(patch_timeline, monkeypatch):
    """Pipeline finish < serial finish when two issues are running (sum > max)."""
    fake_now = _BASE + timedelta(minutes=10)
    monkeypatch.setattr(timeline_service, "_server_now", lambda: fake_now)

    issue1_start = fake_now - timedelta(minutes=2)   # remaining = 13
    issue2_start = fake_now - timedelta(minutes=7)   # remaining = 8

    issues = [
        _make_issue(1, ["in-progress"], size="M"),
        _make_issue(2, ["SIT"], size="M"),
    ]
    runs = [
        _make_run(1, "coder", issue1_start),
        _make_run(2, "coder", issue2_start),
    ]

    patch_timeline(
        issues=issues,
        agent_runs=runs,
        settings={"pipeline_mode": False, "estimation_m_minutes": 15, "reviewer_enabled": False},
    )
    serial_result = timeline_service.get_timeline("sprint-99", "owner/repo")

    patch_timeline(
        issues=issues,
        agent_runs=runs,
        settings={"pipeline_mode": True, "estimation_m_minutes": 15, "reviewer_enabled": False},
    )
    pipeline_result = timeline_service.get_timeline("sprint-99", "owner/repo")

    def _parse(s: str) -> datetime:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    serial_pf = _parse(serial_result["projected_finish"])
    pipeline_pf = _parse(pipeline_result["projected_finish"])
    assert pipeline_pf < serial_pf, (
        f"Pipeline finish {pipeline_pf} must be < serial finish {serial_pf} "
        "when two issues are running (max < sum)."
    )
