"""Tests for issue #1146 — Build timeline data endpoint for running sprint pane.

AC coverage:
  AC1  — GET /api/sprints/{label}/timeline returns HTTP 200 with an ordered
          segment list per issue: each entry has agent, start, end, duration
          for every coder/tester segment including retry rounds.
  AC2  — Segment times sourced from DB agent_runs; no render-time disk reads.
  AC3  — Each issue object includes status (done | running | queued).
  AC4  — Each issue object includes calibrated_estimate from Settings size→time
          mapping refined by Analytics actuals; never a hardcoded constant.
  AC5  — Running issues include estimated_remaining = calibrated_estimate minus
          elapsed; queued issues carry their full calibrated_estimate only.
  AC6  — Documenter/reviewer returned as sprint-level wrap_up_estimate object,
          never attached to individual issues.
  AC7  — wrap_up_estimate includes reviewer only when reviewer_enabled in Settings.
  AC8  — Response includes projected_finish timestamp.
  AC9  — projected_finish respects run mode: serial = sequential queue,
          pipeline = overlapping coder/tester phases (earlier finish).
  AC10 — Response includes sprint_started_at and server_now timestamps.
  AC11 — GitHub is the state truth (issue status); DB is the metrics truth
          (segment times).
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


# ── helpers ───────────────────────────────────────────────────────────────────

def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


_BASE = datetime(2026, 6, 1, 8, 0, 0, tzinfo=timezone.utc)


def _make_issue(
    number: int,
    labels: list[str],
    size: str = "M",
    state: str = "open",
) -> dict:
    all_labels = list(labels) + [f"size-{size}", "sprint-99"]
    return {
        "number": number,
        "title": f"Issue {number}",
        "state": state,
        "labels": [{"name": lbl} for lbl in all_labels],
        "url": f"https://github.com/owner/repo/issues/{number}",
    }


def _make_run(
    issue_number: int,
    agent: str,
    started_at: datetime,
    duration_seconds: int | None = None,
    finished_at: datetime | None = None,
    attempt_kind: str = "initial",
) -> dict:
    fin = finished_at or (started_at + timedelta(seconds=duration_seconds) if duration_seconds else None)
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
        "attempt_kind": attempt_kind,
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


# ── import the service under test ─────────────────────────────────────────────

from apps.dashboard.routers import timeline_service  # noqa: E402


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def patch_timeline(monkeypatch):
    """Inject helpers for github issues, db agent_runs, settings, sprint row."""

    def _setup(
        issues: list[dict],
        agent_runs: list[dict] | None = None,
        settings: dict | None = None,
        sprint_row: dict | None = None,
        calibration_records: list | None = None,
    ):
        monkeypatch.setattr(
            timeline_service,
            "_get_sprint_issues",
            lambda label, project: issues,
        )
        monkeypatch.setattr(
            timeline_service,
            "_get_agent_runs",
            lambda label: agent_runs or [],
        )
        eff_settings = _default_settings(**(settings or {}))
        monkeypatch.setattr(
            timeline_service,
            "_get_settings",
            lambda project: eff_settings,
        )
        monkeypatch.setattr(
            timeline_service,
            "_get_sprint_row",
            lambda label: sprint_row or {
                "label": "sprint-99",
                "started_at": _iso(_BASE),
                "state": "running",
            },
        )
        monkeypatch.setattr(
            timeline_service,
            "_get_calibration_records",
            lambda: calibration_records or [],
        )

    return _setup


# ── AC1: endpoint returns segment list with required fields ───────────────────

def test_ac1_segments_have_required_fields(patch_timeline):
    """Each segment must have agent, start, end, duration."""
    issues = [
        _make_issue(10, ["UAT"]),  # done
    ]
    coder_start = _BASE + timedelta(minutes=5)
    coder_end = coder_start + timedelta(minutes=10)
    tester_start = coder_end + timedelta(minutes=2)
    tester_end = tester_start + timedelta(minutes=8)
    runs = [
        _make_run(10, "coder", coder_start, finished_at=coder_end),
        _make_run(10, "tester", tester_start, finished_at=tester_end),
    ]
    patch_timeline(issues=issues, agent_runs=runs)

    result = timeline_service.get_timeline("sprint-99", "owner/repo")

    assert "issues" in result
    iss = result["issues"][0]
    assert len(iss["segments"]) == 2

    seg = iss["segments"][0]
    assert "agent" in seg
    assert "start" in seg
    assert "end" in seg
    assert "duration" in seg
    assert seg["agent"] == "coder"
    assert seg["start"] == _iso(coder_start)
    assert seg["end"] == _iso(coder_end)
    assert seg["duration"] == (coder_end - coder_start).seconds


def test_ac1_segments_ordered_by_wall_clock(patch_timeline):
    """Segments must be in wall-clock (chronological) order."""
    issues = [_make_issue(10, ["UAT"])]
    coder_start = _BASE
    coder_end = _BASE + timedelta(minutes=12)
    tester_start = coder_end + timedelta(minutes=1)
    tester_end = tester_start + timedelta(minutes=8)
    runs = [
        # Inserted in reversed order to test sorting
        _make_run(10, "tester", tester_start, finished_at=tester_end),
        _make_run(10, "coder", coder_start, finished_at=coder_end),
    ]
    patch_timeline(issues=issues, agent_runs=runs)

    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    segs = result["issues"][0]["segments"]
    assert segs[0]["agent"] == "coder"
    assert segs[1]["agent"] == "tester"


def test_ac1_retry_segments_included(patch_timeline):
    """Retry rounds appear as additional segments in wall-clock order."""
    issues = [_make_issue(10, ["in-progress"])]
    coder_start = _BASE
    coder_end = _BASE + timedelta(minutes=10)
    retry_start = coder_end + timedelta(minutes=2)
    retry_end = retry_start + timedelta(minutes=8)
    runs = [
        _make_run(10, "coder", coder_start, finished_at=coder_end, attempt_kind="initial"),
        _make_run(10, "coder", retry_start, finished_at=retry_end, attempt_kind="fix_round"),
    ]
    patch_timeline(issues=issues, agent_runs=runs)

    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    segs = result["issues"][0]["segments"]
    assert len(segs) == 2
    assert segs[0]["start"] == _iso(coder_start)
    assert segs[1]["start"] == _iso(retry_start)


# ── AC2: segments sourced from DB, not disk ────────────────────────────────────

def test_ac2_segments_from_db_agent_runs(patch_timeline):
    """Segment times must come from the DB agent_runs records."""
    issues = [_make_issue(10, ["UAT"])]
    t0 = _BASE
    t1 = t0 + timedelta(seconds=720)
    runs = [_make_run(10, "coder", t0, finished_at=t1)]
    patch_timeline(issues=issues, agent_runs=runs)

    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    seg = result["issues"][0]["segments"][0]
    assert seg["start"] == _iso(t0)
    assert seg["end"] == _iso(t1)
    assert seg["duration"] == 720


def test_ac2_issue_with_no_runs_has_empty_segments(patch_timeline):
    """Issue with no agent_runs in DB returns empty segments list."""
    issues = [_make_issue(20, ["backlog"])]
    patch_timeline(issues=issues, agent_runs=[])

    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    iss = result["issues"][0]
    assert iss["segments"] == []


# ── AC3: each issue has status ────────────────────────────────────────────────

def test_ac3_done_status_from_github_labels(patch_timeline):
    """Closed or UAT-approved/UAT-labelled issues map to status=done."""
    issues = [
        _make_issue(1, ["UAT"]),
        _make_issue(2, [], state="closed"),
    ]
    patch_timeline(issues=issues, agent_runs=[])
    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    statuses = {i["number"]: i["status"] for i in result["issues"]}
    assert statuses[1] == "done"
    assert statuses[2] == "done"


def test_ac3_running_status_from_github_labels(patch_timeline):
    """in-progress or SIT-labelled issues map to status=running."""
    issues = [
        _make_issue(3, ["in-progress"]),
        _make_issue(4, ["SIT"]),
    ]
    patch_timeline(issues=issues, agent_runs=[])
    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    statuses = {i["number"]: i["status"] for i in result["issues"]}
    assert statuses[3] == "running"
    assert statuses[4] == "running"


def test_ac3_queued_status_for_backlog_issues(patch_timeline):
    """Backlog issues in the sprint map to status=queued."""
    issues = [_make_issue(5, [])]  # no in-progress/SIT/UAT/done label
    patch_timeline(issues=issues, agent_runs=[])
    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    assert result["issues"][0]["status"] == "queued"


def test_ac3_status_values_are_valid(patch_timeline):
    """All returned status values must be done | running | queued."""
    VALID = {"done", "running", "queued"}
    issues = [
        _make_issue(1, ["UAT"]),
        _make_issue(2, ["in-progress"]),
        _make_issue(3, []),
    ]
    patch_timeline(issues=issues, agent_runs=[])
    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    for iss in result["issues"]:
        assert iss["status"] in VALID, f"unexpected status: {iss['status']}"


# ── AC4: calibrated_estimate ──────────────────────────────────────────────────

def test_ac4_calibrated_estimate_from_settings(patch_timeline):
    """calibrated_estimate uses Settings size→time values, not hardcoded constants."""
    issues = [_make_issue(10, [], size="L")]
    patch_timeline(
        issues=issues,
        settings={"estimation_l_minutes": 45},  # non-default value
    )
    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    est = result["issues"][0]["calibrated_estimate"]
    assert est == 45, f"expected 45 (from settings), got {est}"


def test_ac4_calibrated_estimate_nonzero(patch_timeline):
    """calibrated_estimate must be a non-zero positive number."""
    issues = [_make_issue(10, [], size="S")]
    patch_timeline(issues=issues)
    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    est = result["issues"][0]["calibrated_estimate"]
    assert isinstance(est, (int, float))
    assert est > 0


def test_ac4_calibrated_estimate_uses_analytics_actuals_when_sufficient(patch_timeline):
    """When analytics actuals have >= 3 samples for a size, use calibrated median."""
    issues = [_make_issue(10, [], size="M")]
    # 3 samples of size M → calibration should kick in
    cal_records = [
        {"size": "M", "actual_minutes": 20},
        {"size": "M", "actual_minutes": 22},
        {"size": "M", "actual_minutes": 18},
    ]
    patch_timeline(issues=issues, calibration_records=cal_records)
    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    est = result["issues"][0]["calibrated_estimate"]
    # Median of [18, 20, 22] = 20; not the settings value of 15
    assert est == 20, f"expected calibrated median 20, got {est}"


def test_ac4_calibrated_estimate_falls_back_to_settings_when_few_samples(patch_timeline):
    """With < 3 samples, calibrated_estimate falls back to Settings value."""
    issues = [_make_issue(10, [], size="M")]
    # Only 2 samples — not enough
    cal_records = [
        {"size": "M", "actual_minutes": 25},
        {"size": "M", "actual_minutes": 30},
    ]
    patch_timeline(
        issues=issues,
        settings={"estimation_m_minutes": 15},
        calibration_records=cal_records,
    )
    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    est = result["issues"][0]["calibrated_estimate"]
    assert est == 15, f"expected settings fallback 15, got {est}"


def test_ac4_unsized_issue_has_estimate_zero_or_default(patch_timeline):
    """Issue with no size label gets a sensible default estimate (not error)."""
    issues = [{"number": 99, "title": "No size", "state": "open",
               "labels": [{"name": "sprint-99"}], "url": ""}]
    patch_timeline(issues=issues)
    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    iss = result["issues"][0]
    # Must not raise; calibrated_estimate must be present (may be 0 or a default)
    assert "calibrated_estimate" in iss
    assert iss["calibrated_estimate"] >= 0


# ── AC5: estimated_remaining ──────────────────────────────────────────────────

def test_ac5_running_issue_has_estimated_remaining(patch_timeline, monkeypatch):
    """Running issues include estimated_remaining < calibrated_estimate."""
    issues = [_make_issue(10, ["in-progress"], size="M")]
    # Elapsed = 5 min of a 15-min estimate → remaining ≈ 10
    run_start = _BASE
    runs = [_make_run(10, "coder", run_start, duration_seconds=None)]  # no end → still running

    # Fix server_now so elapsed is deterministic
    fake_now = run_start + timedelta(minutes=5)
    monkeypatch.setattr(timeline_service, "_server_now", lambda: fake_now)

    patch_timeline(
        issues=issues,
        agent_runs=runs,
        settings={"estimation_m_minutes": 15, "estimation_buffer_pct": 0},
    )
    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    iss = result["issues"][0]
    assert "estimated_remaining" in iss
    assert iss["estimated_remaining"] > 0
    assert iss["estimated_remaining"] < iss["calibrated_estimate"]


def test_ac5_queued_issue_has_no_estimated_remaining(patch_timeline):
    """Queued issues must not have estimated_remaining (or it is None)."""
    issues = [_make_issue(20, [], size="M")]
    patch_timeline(issues=issues, agent_runs=[])
    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    iss = result["issues"][0]
    assert iss.get("estimated_remaining") is None


def test_ac5_done_issue_has_no_estimated_remaining(patch_timeline):
    """Done issues must not have estimated_remaining."""
    issues = [_make_issue(30, ["UAT"])]
    patch_timeline(issues=issues, agent_runs=[])
    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    iss = result["issues"][0]
    assert iss.get("estimated_remaining") is None


# ── AC6: wrap_up_estimate is sprint-level, not per-issue ─────────────────────

def test_ac6_wrap_up_estimate_is_sprint_level(patch_timeline):
    """wrap_up_estimate is in the top-level response, not inside any issue."""
    issues = [_make_issue(10, ["UAT"])]
    patch_timeline(issues=issues)
    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    assert "wrap_up_estimate" in result
    for iss in result["issues"]:
        assert "wrap_up_estimate" not in iss


def test_ac6_no_documenter_or_reviewer_in_issue_segments(patch_timeline):
    """Issue segments must only contain coder and tester agents."""
    issues = [_make_issue(10, ["UAT"])]
    runs = [
        _make_run(10, "coder", _BASE, finished_at=_BASE + timedelta(minutes=10)),
        _make_run(10, "tester", _BASE + timedelta(minutes=12), finished_at=_BASE + timedelta(minutes=18)),
    ]
    patch_timeline(issues=issues, agent_runs=runs)
    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    for iss in result["issues"]:
        for seg in iss["segments"]:
            assert seg["agent"] in ("coder", "tester"), f"unexpected agent: {seg['agent']}"


# ── AC7: wrap_up_estimate includes reviewer only when reviewer_enabled ─────────

def test_ac7_reviewer_included_when_enabled(patch_timeline):
    """wrap_up_estimate contains reviewer when reviewer_enabled=True."""
    issues = [_make_issue(10, ["UAT"])]
    patch_timeline(issues=issues, settings={"reviewer_enabled": True})
    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    wu = result["wrap_up_estimate"]
    assert "documenter" in wu
    assert "reviewer" in wu
    assert wu["reviewer"] > 0


def test_ac7_reviewer_absent_when_disabled(patch_timeline):
    """wrap_up_estimate has no reviewer when reviewer_enabled=False."""
    issues = [_make_issue(10, ["UAT"])]
    patch_timeline(issues=issues, settings={"reviewer_enabled": False})
    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    wu = result["wrap_up_estimate"]
    assert "documenter" in wu
    assert "reviewer" not in wu


def test_ac7_wrap_up_always_has_documenter(patch_timeline):
    """wrap_up_estimate always includes documenter regardless of reviewer setting."""
    for enabled in (True, False):
        issues = [_make_issue(10, ["UAT"])]
        patch_timeline(issues=issues, settings={"reviewer_enabled": enabled})
        result = timeline_service.get_timeline("sprint-99", "owner/repo")
        assert "documenter" in result["wrap_up_estimate"]


# ── AC8: projected_finish is present ─────────────────────────────────────────

def test_ac8_projected_finish_present(patch_timeline):
    """Response must include projected_finish."""
    issues = [_make_issue(10, ["UAT"])]
    patch_timeline(issues=issues)
    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    assert "projected_finish" in result


def test_ac8_projected_finish_is_iso_timestamp(patch_timeline):
    """projected_finish must be a parseable ISO-8601 timestamp string."""
    issues = [_make_issue(10, ["UAT"])]
    patch_timeline(issues=issues)
    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    pf = result["projected_finish"]
    # Must parse without raising (handle naive or aware timestamps)
    raw = str(pf).replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    assert dt is not None


# ── AC9: projected_finish respects run mode ───────────────────────────────────

def test_ac9_pipeline_finish_earlier_than_serial(patch_timeline, monkeypatch):
    """pipeline projected_finish must be earlier than serial for multiple queued issues."""
    issues = [
        _make_issue(1, ["in-progress"], size="M"),
        _make_issue(2, [], size="M"),
        _make_issue(3, [], size="M"),
    ]

    fake_now = _BASE + timedelta(minutes=2)
    monkeypatch.setattr(timeline_service, "_server_now", lambda: fake_now)

    # Serial run
    patch_timeline(
        issues=issues,
        agent_runs=[_make_run(1, "coder", _BASE, duration_seconds=None)],
        settings={"pipeline_mode": False, "estimation_m_minutes": 15},
    )
    serial_result = timeline_service.get_timeline("sprint-99", "owner/repo")

    # Pipeline run
    patch_timeline(
        issues=issues,
        agent_runs=[_make_run(1, "coder", _BASE, duration_seconds=None)],
        settings={"pipeline_mode": True, "estimation_m_minutes": 15},
    )
    pipeline_result = timeline_service.get_timeline("sprint-99", "owner/repo")

    def _parse_pf(s: str) -> datetime:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    serial_pf = _parse_pf(serial_result["projected_finish"])
    pipeline_pf = _parse_pf(pipeline_result["projected_finish"])
    assert pipeline_pf < serial_pf, (
        f"pipeline finish {pipeline_pf} should be < serial finish {serial_pf}"
    )


def test_ac9_serial_projected_is_sequential_sum(patch_timeline, monkeypatch):
    """Serial projected_finish = now + sum(queued estimates) + wrap_up (no running issue)."""
    issues = [
        _make_issue(1, [], size="M"),  # queued, 15 min
        _make_issue(2, [], size="M"),  # queued, 15 min
    ]
    fake_now = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(timeline_service, "_server_now", lambda: fake_now)

    patch_timeline(
        issues=issues,
        agent_runs=[],
        settings={
            "pipeline_mode": False,
            "estimation_m_minutes": 15,
            "estimation_buffer_pct": 0,
            "reviewer_enabled": False,
        },
    )
    result = timeline_service.get_timeline("sprint-99", "owner/repo")

    pf_raw = datetime.fromisoformat(result["projected_finish"].replace("Z", "+00:00"))
    # Normalise to UTC regardless of whether the string had tzinfo
    if pf_raw.tzinfo is None:
        pf_raw = pf_raw.replace(tzinfo=timezone.utc)
    wu = result["wrap_up_estimate"]
    wu_total = wu["documenter"] + wu.get("reviewer", 0)

    expected_min = 15 + 15 + wu_total  # sum of queued estimates + wrap-up
    expected = fake_now + timedelta(minutes=expected_min)

    # Allow a small tolerance for elapsed calculations
    delta = abs((pf_raw - expected).total_seconds())
    assert delta < 5, f"projected_finish {pf_raw} differs from expected {expected} by {delta}s"


# ── AC10: sprint_started_at and server_now ────────────────────────────────────

def test_ac10_sprint_started_at_present(patch_timeline):
    """Response must include sprint_started_at from the DB sprints row."""
    issues = [_make_issue(10, ["UAT"])]
    sprint_row = {"label": "sprint-99", "started_at": "2026-06-01T06:00:00", "state": "running"}
    patch_timeline(issues=issues, sprint_row=sprint_row)
    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    assert result["sprint_started_at"] == "2026-06-01T06:00:00"


def test_ac10_server_now_present(patch_timeline, monkeypatch):
    """Response must include server_now timestamp."""
    issues = [_make_issue(10, ["UAT"])]
    fake_now = datetime(2026, 6, 1, 12, 30, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(timeline_service, "_server_now", lambda: fake_now)
    patch_timeline(issues=issues)
    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    assert "server_now" in result
    # server_now should reflect the fake_now
    sn_raw = datetime.fromisoformat(result["server_now"].replace("Z", "+00:00"))
    if sn_raw.tzinfo is None:
        sn_raw = sn_raw.replace(tzinfo=timezone.utc)
    assert abs((sn_raw - fake_now).total_seconds()) < 2


# ── AC11: GitHub = state truth, DB = metrics truth ────────────────────────────

def test_ac11_status_from_github_not_db(patch_timeline):
    """Issue status is derived from GitHub labels, not from DB agent_runs."""
    # Issue has UAT label (done) but has an open agent_run (no finished_at)
    issues = [_make_issue(10, ["UAT"])]
    runs = [_make_run(10, "coder", _BASE, duration_seconds=None)]  # still "running" in DB
    patch_timeline(issues=issues, agent_runs=runs)

    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    # GitHub labels say "done"; DB agent_run says "open" — must use GitHub
    assert result["issues"][0]["status"] == "done"


def test_ac11_segments_from_db_not_disk(patch_timeline):
    """Segment data comes from _get_agent_runs (DB), not file reads."""
    # If the service reads from disk it would need to access a real log file.
    # We verify by patching _get_agent_runs to return known data and checking
    # the result matches — if it tried disk reads with no actual files it would
    # return empty or raise.
    issues = [_make_issue(10, ["UAT"])]
    t0 = _BASE
    t1 = t0 + timedelta(minutes=7)
    runs = [_make_run(10, "coder", t0, finished_at=t1)]
    patch_timeline(issues=issues, agent_runs=runs)

    result = timeline_service.get_timeline("sprint-99", "owner/repo")
    seg = result["issues"][0]["segments"][0]
    assert seg["duration"] == 7 * 60


# ── settings schema: reviewer_enabled field ───────────────────────────────────

def test_reviewer_enabled_field_in_settings_schema():
    """reviewer_enabled must exist in KNOWN_FIELDS with default=False."""
    from settings_schema import KNOWN_FIELDS
    assert "reviewer_enabled" in KNOWN_FIELDS, (
        "reviewer_enabled must be added to settings_schema.KNOWN_FIELDS"
    )
    meta = KNOWN_FIELDS["reviewer_enabled"]
    assert meta["secret"] is False
    assert meta["default"] is False
