"""Tests for issue #863 — Add scheduled overnight sprint queue with sequential execution.

Each test is anchored to a specific acceptance criterion from the issue. The
queue/scheduling logic lives in ``services.sprint_manager.sprint_scheduler`` and
is exercised through injected collaborators (no live server, subprocesses, or
wall clock).

Acceptance criteria covered:
  AC1  — Scheduled run time accepts HH:MM; empty disables all auto-scheduling.
  AC2  — Run-on-schedule is meaningful only on Approved sprints.
  AC3  — Run-on-schedule defaults off for all sprints.
  AC4  — At the scheduled time, collect approved opted-in sprints by board order.
  AC5  — Sprints execute one at a time; next waits for previous to complete.
  AC6  — Respect the one-sprint-per-project lock; skip gracefully if busy.
  AC7  — Any failure halts the queue immediately and surfaces an alert.
  AC8  — Manual Run Sprint is unaffected by schedule configuration.
  AC9  — Empty scheduled run time => nothing auto-runs regardless of toggles.
  AC10 — Two opted-in approved sprints run sequentially and complete.
"""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from services.sprint_manager import sprint_scheduler as sch  # noqa: E402


# ── In-memory settings store so storage tests don't touch the real KV ────────
@pytest.fixture
def mem_store(monkeypatch):
    store: dict = {}

    def _key(scope, key, project):
        return f"{scope}|{project or ''}|{key}"

    def fake_get_scoped(scope, key, project=None):
        return store.get(_key(scope, key, project), {})

    def fake_set(scope, key, value, project=None):
        store[_key(scope, key, project)] = value

    monkeypatch.setattr(sch.settings_repo, "get_setting_scoped", fake_get_scoped)
    monkeypatch.setattr(sch.settings_repo, "set_setting", fake_set)
    return store


def _sprint(label, status="approved", position=0):
    return {"label": label, "status": status, "position": position}


# ── AC1 — scheduled run time validation; empty disables ──────────────────────
def test_ac1_accepts_valid_24h_time():
    assert sch.normalize_time("01:00") == "01:00"
    assert sch.normalize_time("23:59") == "23:59"
    assert sch.normalize_time("00:00") == "00:00"
    assert sch.normalize_time(" 09:30 ") == "09:30"


def test_ac1_empty_is_disabled_sentinel():
    assert sch.normalize_time("") == ""
    assert sch.normalize_time("   ") == ""
    assert sch.normalize_time(None) == ""


def test_ac1_rejects_malformed_time():
    for bad in ["24:00", "1:00", "12:60", "0100", "9am", "12:5", "25:30"]:
        with pytest.raises(sch.ScheduleError):
            sch.normalize_time(bad)


def test_ac1_empty_time_never_fires():
    # AC1: leaving it empty disables auto-scheduling.
    assert sch.should_fire("", "01:00", None, "2026-06-13") is False
    assert sch.should_fire(None, "01:00", None, "2026-06-13") is False


# ── AC2 — approved-only ──────────────────────────────────────────────────────
def test_ac2_only_approved_status_is_eligible():
    assert sch.is_approved_status("approved") is True
    assert sch.is_approved_status("planned") is True   # lifecycle alias
    assert sch.is_approved_status("Approved") is True
    for other in ["draft", "running", "ready_to_merge", "needs_rework",
                  "completed", "", None]:
        assert sch.is_approved_status(other) is False


def test_ac2_non_approved_sprints_excluded_from_queue():
    sprints = [
        _sprint("sprint-1", status="running", position=0),
        _sprint("sprint-2", status="draft", position=1),
        _sprint("sprint-3", status="approved", position=2),
    ]
    toggles = {"sprint-1": True, "sprint-2": True, "sprint-3": True}
    # Only the approved sprint survives even though all three are toggled on.
    assert sch.collect_queue(sprints, toggles) == ["sprint-3"]


# ── AC3 — defaults off ───────────────────────────────────────────────────────
def test_ac3_run_on_schedule_defaults_off(mem_store):
    assert sch.get_run_on_schedule("owner/repo", "sprint-5") is False
    assert sch.get_run_on_schedule_map("owner/repo") == {}


def test_ac3_newly_approved_sprint_not_in_queue_until_opted_in():
    sprints = [_sprint("sprint-9", status="approved", position=0)]
    # No toggle set -> not queued.
    assert sch.collect_queue(sprints, {}) == []
    # Opted in -> queued.
    assert sch.collect_queue(sprints, {"sprint-9": True}) == ["sprint-9"]


# ── AC4 — collect approved opted-in, ordered by board position ───────────────
def test_ac4_queue_ordered_by_board_position():
    sprints = [
        _sprint("sprint-c", status="approved", position=2),
        _sprint("sprint-a", status="approved", position=0),
        _sprint("sprint-b", status="approved", position=1),
    ]
    toggles = {"sprint-a": True, "sprint-b": True, "sprint-c": True}
    assert sch.collect_queue(sprints, toggles) == ["sprint-a", "sprint-b", "sprint-c"]


def test_ac4_only_opted_in_collected():
    sprints = [
        _sprint("sprint-a", status="approved", position=0),
        _sprint("sprint-b", status="approved", position=1),
        _sprint("sprint-c", status="approved", position=2),
    ]
    toggles = {"sprint-a": True, "sprint-c": True}  # b not opted in
    assert sch.collect_queue(sprints, toggles) == ["sprint-a", "sprint-c"]


def test_ac4_should_fire_at_configured_time_only():
    assert sch.should_fire("01:00", "01:00", None, "2026-06-13") is True
    assert sch.should_fire("01:00", "00:59", None, "2026-06-13") is False
    # Already fired today -> no double fire.
    assert sch.should_fire("01:00", "01:00", "2026-06-13", "2026-06-13") is False
    # Fired yesterday -> fires again today.
    assert sch.should_fire("01:00", "01:00", "2026-06-12", "2026-06-13") is True


# ── AC5 — sequential execution; next waits for previous ──────────────────────
def test_ac5_runs_sequentially_in_order():
    events = []

    def runner(label):
        events.append(("start", label))
        events.append(("end", label))   # synchronous == "completed before next"
        return True

    res = sch.run_queue(["s1", "s2", "s3"], runner=runner, is_locked=lambda: None)
    assert res.ran == ["s1", "s2", "s3"]
    # Each sprint fully completes (start+end) before the next starts.
    assert events == [
        ("start", "s1"), ("end", "s1"),
        ("start", "s2"), ("end", "s2"),
        ("start", "s3"), ("end", "s3"),
    ]


def test_ac5_next_not_started_until_previous_completes():
    in_flight = {"count": 0}
    max_concurrent = {"v": 0}

    def runner(label):
        in_flight["count"] += 1
        max_concurrent["v"] = max(max_concurrent["v"], in_flight["count"])
        in_flight["count"] -= 1
        return True

    sch.run_queue(["s1", "s2"], runner=runner, is_locked=lambda: None)
    assert max_concurrent["v"] == 1  # never two at once


# ── AC6 — respect one-sprint-per-project lock ────────────────────────────────
def test_ac6_skips_when_already_locked():
    calls = []

    def runner(label):
        calls.append(label)
        return True

    res = sch.run_queue(
        ["s1", "s2"],
        runner=runner,
        is_locked=lambda: {"sprint_label": "sprint-running", "project": "owner/repo"},
    )
    assert res.skipped is True
    assert res.ran == []
    assert calls == []                       # no duplicate run
    assert "sprint-running" in res.skipped_reason


def test_ac6_runs_when_lock_free():
    res = sch.run_queue(["s1"], runner=lambda l: True, is_locked=lambda: None)
    assert res.skipped is False
    assert res.ran == ["s1"]


# ── AC7 — failure halts queue + alert ────────────────────────────────────────
def test_ac7_failure_halts_and_alerts():
    alerts = []

    def runner(label):
        return label != "s2"   # s2 fails

    res = sch.run_queue(
        ["s1", "s2", "s3"],
        runner=runner,
        is_locked=lambda: None,
        alert_fn=alerts.append,
    )
    assert res.ran == ["s1"]              # s3 never ran
    assert res.failed == "s2"
    assert res.halted is True
    assert len(alerts) == 1
    assert "s2" in alerts[0]["title"]
    assert res.alert is not None


def test_ac7_subsequent_sprints_do_not_run_after_failure():
    ran = []

    def runner(label):
        ran.append(label)
        return label != "s1"   # first sprint fails immediately

    res = sch.run_queue(["s1", "s2", "s3"], runner=runner, is_locked=lambda: None)
    assert ran == ["s1"]       # s2, s3 never invoked
    assert res.halted is True
    assert res.ran == []


# ── AC8 — manual run unaffected ──────────────────────────────────────────────
def test_ac8_fire_if_due_does_not_run_when_not_scheduled_time():
    ran = []
    res = sch.fire_if_due(
        "owner/repo",
        now_hhmm="14:00",
        today="2026-06-13",
        scheduled_time="01:00",          # configured, but not now
        last_fired_date=None,
        sprints_provider=lambda: [_sprint("s1")],
        runner=lambda l: ran.append(l) or True,
        is_locked=lambda: None,
    )
    assert res["fired"] is False
    assert ran == []   # scheduler stays out of the way of manual runs


# ── AC9 — empty scheduled time => nothing auto-runs ──────────────────────────
def test_ac9_empty_time_no_autorun_even_with_toggles(mem_store):
    sch.set_run_on_schedule("owner/repo", "sprint-1", True)
    sch.set_run_on_schedule("owner/repo", "sprint-2", True)
    ran = []
    res = sch.fire_if_due(
        "owner/repo",
        now_hhmm="01:00",
        today="2026-06-13",
        scheduled_time="",               # disabled
        last_fired_date=None,
        sprints_provider=lambda: [
            _sprint("sprint-1", position=0), _sprint("sprint-2", position=1),
        ],
        runner=lambda l: ran.append(l) or True,
        is_locked=lambda: None,
    )
    assert res["fired"] is False
    assert ran == []


# ── AC10 — two opted-in approved sprints run sequentially & complete ─────────
def test_ac10_two_sprints_run_sequentially_to_completion(mem_store):
    sch.set_run_on_schedule("owner/repo", "sprint-a", True)
    sch.set_run_on_schedule("owner/repo", "sprint-b", True)
    fired = []
    recorded = []

    res = sch.fire_if_due(
        "owner/repo",
        now_hhmm="01:00",
        today="2026-06-13",
        scheduled_time="01:00",
        last_fired_date=None,
        sprints_provider=lambda: [
            _sprint("sprint-b", status="approved", position=1),
            _sprint("sprint-a", status="approved", position=0),
        ],
        runner=lambda l: (fired.append(l) or True),
        is_locked=lambda: None,
        record_fired=recorded.append,
    )
    assert res["fired"] is True
    assert res["queue"] == ["sprint-a", "sprint-b"]      # board order
    assert fired == ["sprint-a", "sprint-b"]             # ran sequentially
    assert res["result"]["ran"] == ["sprint-a", "sprint-b"]
    assert res["result"]["halted"] is False
    assert recorded == ["2026-06-13"]                    # day marked fired


# ── Storage round-trips (AC1/AC3 persistence) ────────────────────────────────
def test_storage_scheduled_time_roundtrip(mem_store):
    assert sch.get_scheduled_time("owner/repo") == ""
    sch.set_scheduled_time("owner/repo", "02:30")
    assert sch.get_scheduled_time("owner/repo") == "02:30"
    # Clearing disables.
    sch.set_scheduled_time("owner/repo", "")
    assert sch.get_scheduled_time("owner/repo") == ""


def test_storage_rejects_bad_time(mem_store):
    with pytest.raises(sch.ScheduleError):
        sch.set_scheduled_time("owner/repo", "99:99")


def test_storage_toggle_roundtrip(mem_store):
    sch.set_run_on_schedule("owner/repo", "sprint-1", True)
    assert sch.get_run_on_schedule("owner/repo", "sprint-1") is True
    sch.set_run_on_schedule("owner/repo", "sprint-1", False)
    assert sch.get_run_on_schedule("owner/repo", "sprint-1") is False
    # Off is stored as absence so default-off holds.
    assert sch.get_run_on_schedule_map("owner/repo") == {}


def test_storage_toggle_is_per_project(mem_store):
    sch.set_run_on_schedule("owner/repo-a", "sprint-1", True)
    assert sch.get_run_on_schedule("owner/repo-a", "sprint-1") is True
    assert sch.get_run_on_schedule("owner/repo-b", "sprint-1") is False


def test_last_fired_roundtrip(mem_store):
    assert sch.get_last_fired_date("owner/repo") is None
    sch.set_last_fired_date("owner/repo", "2026-06-13")
    assert sch.get_last_fired_date("owner/repo") == "2026-06-13"
