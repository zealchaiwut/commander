"""Tests for mis-sizing flag detection (issue #578).

Covers:
  AC1 - History records estimated vs actual size per ticket/label
  AC2 - Configurable tier threshold for mis-sizing events
  AC3 - Tickets auto-flagged when label has >= N mis-sizing events
  AC4/5 - Flag includes ticket ID, current estimate, historical avg, event count, labels
  AC6 - Reviewer actions: approved, reestimated, dismissed
  AC7 - Sprint start blocked while pending flags remain (has_unresolved_flags)
  AC8 - Flag history and reviewer actions logged for audit
  AC9 - Detection re-runs on generate (idempotent, preserves actions)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_SM = Path(__file__).parent.parent / "services" / "sprint_manager"
if str(_SM) not in sys.path:
    sys.path.insert(0, str(_SM))

import mis_sizing


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_commander(tmp_path: Path) -> Path:
    commander = tmp_path / ".commander"
    commander.mkdir(parents=True, exist_ok=True)
    return commander


def _write_estimate(commander: Path, issue_num: int, size: str) -> None:
    est_dir = commander / "estimates"
    est_dir.mkdir(exist_ok=True)
    (est_dir / f"issue-{issue_num}.json").write_text(
        json.dumps({"issue_number": issue_num, "size": size, "minutes": mis_sizing.CANONICAL_MINUTES[size]}),
        encoding="utf-8",
    )


def _make_completed_tickets(
    issues: list[dict],
) -> list[dict]:
    """Build completed-ticket records for build_history_from_completed.

    Each issue dict: {number, sprint, estimated_size, elapsed_minutes, labels}
    """
    base_ts = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()
    out = []
    for iss in issues:
        start_ts = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()
        # elapsed_minutes encoded via start → end offsets
        end_dt = datetime(
            2026, 1, 1, 0, int(iss["elapsed_minutes"]), 0, tzinfo=timezone.utc
        )
        out.append({
            "number": iss["number"],
            "sprint": iss.get("sprint", "sprint-1"),
            "estimated_size": iss["estimated_size"],
            "coder_started_at": start_ts,
            "tester_finished_at": end_dt.isoformat(),
            "status_changed_at": end_dt.isoformat(),
            "labels": iss.get("labels", []),
        })
    return out


# ── AC1: history records estimated vs actual size per label ───────────────────

def test_build_history_records_mis_sizing_events():
    """AC1 — history accumulates events per label after sprint close."""
    tickets = _make_completed_tickets([
        # S estimated, ~35 min actual → actual=L, tier_diff=2 (mis-sized)
        {"number": 1, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 35, "labels": ["backend-auth"]},
        # M estimated, ~20 min actual → actual=M, tier_diff=0 (correct)
        {"number": 2, "sprint": "sprint-1", "estimated_size": "M",
         "elapsed_minutes": 20, "labels": ["backend-auth"]},
    ])
    history = mis_sizing.build_history_from_completed(tickets)
    events = history["events_by_label"].get("backend-auth", [])
    # Both issues produce events for backend-auth, regardless of tier_diff
    assert len(events) == 2
    assert any(e["issue"] == 1 and e["estimated_size"] == "S" and e["actual_size"] == "L" for e in events)
    assert any(e["issue"] == 2 and e["tier_diff"] == 0 for e in events)


def test_build_history_skips_meta_labels():
    """AC1 — meta-labels (size-*, sprint-N, in-progress, etc.) are excluded."""
    tickets = _make_completed_tickets([
        {"number": 3, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 45, "labels": ["size-S", "sprint-10", "in-progress", "my-feature"]},
    ])
    history = mis_sizing.build_history_from_completed(tickets)
    assert "size-S" not in history["events_by_label"]
    assert "sprint-10" not in history["events_by_label"]
    assert "in-progress" not in history["events_by_label"]
    assert "my-feature" in history["events_by_label"]


def test_build_history_skips_missing_timestamps():
    """AC1 — tickets without timing data are skipped."""
    records = [{
        "number": 4, "sprint": "sprint-1", "estimated_size": "S",
        "coder_started_at": None, "tester_finished_at": None,
        "status_changed_at": None, "labels": ["backend-auth"],
    }]
    history = mis_sizing.build_history_from_completed(records)
    assert history["events_by_label"] == {}


# ── AC2: configurable tier threshold ─────────────────────────────────────────

def test_load_config_defaults(tmp_path):
    """AC2 — default threshold is 2 tiers, min_events is 2."""
    commander = _make_commander(tmp_path)
    config = mis_sizing.load_config(commander)
    assert config == {"tier_threshold": 2, "min_events": 2}


def test_save_and_load_config(tmp_path):
    """AC2 — config persists and round-trips correctly."""
    commander = _make_commander(tmp_path)
    mis_sizing.save_config(commander, {"tier_threshold": 1, "min_events": 3})
    config = mis_sizing.load_config(commander)
    assert config["tier_threshold"] == 1
    assert config["min_events"] == 3


# ── AC3: auto-flag when label has >= N mis-sizing events ─────────────────────

def test_compute_flags_flags_matching_label(tmp_path):
    """AC3 — ticket with flagged label appears in flags list."""
    commander = _make_commander(tmp_path)
    _write_estimate(commander, 10, "S")

    # Build history: 2 mis-sizing events (S→L, tier_diff=2) for backend-auth
    history = mis_sizing.build_history_from_completed(_make_completed_tickets([
        {"number": 101, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 35, "labels": ["backend-auth"]},
        {"number": 102, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 40, "labels": ["backend-auth"]},
        {"number": 103, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 38, "labels": ["backend-auth"]},
    ]))
    config = {"tier_threshold": 2, "min_events": 2}
    sprint_issues = [{"number": 10, "title": "New ticket", "labels": [{"name": "backend-auth"}]}]

    flags = mis_sizing.compute_flags(sprint_issues, history, config, commander / "estimates")
    assert len(flags) == 1
    assert flags[0]["issue_number"] == 10
    assert "backend-auth" in flags[0]["driving_labels"]


def test_compute_flags_no_flag_insufficient_events(tmp_path):
    """AC3 — ticket not flagged when label has fewer than min_events mis-sizing events."""
    commander = _make_commander(tmp_path)
    _write_estimate(commander, 10, "S")

    # Only 1 mis-sizing event — below min_events=2
    history = mis_sizing.build_history_from_completed(_make_completed_tickets([
        {"number": 101, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 35, "labels": ["backend-auth"]},
    ]))
    config = {"tier_threshold": 2, "min_events": 2}
    sprint_issues = [{"number": 10, "title": "New ticket", "labels": [{"name": "backend-auth"}]}]
    flags = mis_sizing.compute_flags(sprint_issues, history, config, commander / "estimates")
    assert len(flags) == 0


def test_compute_flags_threshold_change_removes_flags(tmp_path):
    """AC3 — raising min_events above available count removes flags (UAT step 7)."""
    commander = _make_commander(tmp_path)
    _write_estimate(commander, 10, "S")

    history = mis_sizing.build_history_from_completed(_make_completed_tickets([
        {"number": 101, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 35, "labels": ["backend-auth"]},
        {"number": 102, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 38, "labels": ["backend-auth"]},
        {"number": 103, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 40, "labels": ["backend-auth"]},
    ]))
    sprint_issues = [{"number": 10, "title": "New ticket", "labels": [{"name": "backend-auth"}]}]

    config_n2 = {"tier_threshold": 2, "min_events": 2}
    assert len(mis_sizing.compute_flags(sprint_issues, history, config_n2, commander / "estimates")) == 1

    config_n5 = {"tier_threshold": 2, "min_events": 5}
    assert len(mis_sizing.compute_flags(sprint_issues, history, config_n5, commander / "estimates")) == 0


# ── AC4/5: flag content ───────────────────────────────────────────────────────

def test_flag_contains_required_fields(tmp_path):
    """AC5 — flag dict includes all required fields."""
    commander = _make_commander(tmp_path)
    _write_estimate(commander, 10, "S")

    history = mis_sizing.build_history_from_completed(_make_completed_tickets([
        {"number": 101, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 35, "labels": ["backend-auth"]},
        {"number": 102, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 40, "labels": ["backend-auth"]},
    ]))
    config = {"tier_threshold": 2, "min_events": 2}
    sprint_issues = [{"number": 10, "title": "Reauth service", "labels": [{"name": "backend-auth"}]}]
    flags = mis_sizing.compute_flags(sprint_issues, history, config, commander / "estimates")
    f = flags[0]
    assert f["issue_number"] == 10
    assert f["current_estimate"] == "S"
    assert f["current_estimate_minutes"] == 5
    assert f["historical_avg_actual_size"] is not None
    assert f["historical_avg_actual_minutes"] is not None
    assert f["mis_sizing_event_count"] >= 2
    assert "backend-auth" in f["driving_labels"]
    assert f["status"] == "pending"


# ── AC6: reviewer actions ─────────────────────────────────────────────────────

def test_record_action_approved(tmp_path):
    """AC6 — 'approved' action changes flag status and logs it."""
    commander = _make_commander(tmp_path)
    _write_estimate(commander, 10, "S")

    history = mis_sizing.build_history_from_completed(_make_completed_tickets([
        {"number": 101, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 35, "labels": ["backend-auth"]},
        {"number": 102, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 38, "labels": ["backend-auth"]},
    ]))
    sprint_issues = [{"number": 10, "title": "New ticket", "labels": [{"name": "backend-auth"}]}]
    mis_sizing.generate_and_save_flags(commander, "sprint-44", sprint_issues, history)

    data = mis_sizing.record_action(commander, "sprint-44", 10, "approved")
    flag = next(f for f in data["flags"] if f["issue_number"] == 10)
    assert flag["status"] == "approved"
    assert flag["action_taken_at"] is not None
    assert len(data["audit_log"]) == 1
    assert data["audit_log"][0]["action"] == "approved"


def test_record_action_reestimated(tmp_path):
    """AC6 — 'reestimated' stores new_size in flag and audit log."""
    commander = _make_commander(tmp_path)
    _write_estimate(commander, 10, "S")

    history = mis_sizing.build_history_from_completed(_make_completed_tickets([
        {"number": 101, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 35, "labels": ["backend-auth"]},
        {"number": 102, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 38, "labels": ["backend-auth"]},
    ]))
    sprint_issues = [{"number": 10, "title": "New ticket", "labels": [{"name": "backend-auth"}]}]
    mis_sizing.generate_and_save_flags(commander, "sprint-44", sprint_issues, history)

    data = mis_sizing.record_action(commander, "sprint-44", 10, "reestimated", new_size="M")
    flag = next(f for f in data["flags"] if f["issue_number"] == 10)
    assert flag["status"] == "reestimated"
    assert flag["new_size"] == "M"
    assert data["audit_log"][0]["new_size"] == "M"
    assert data["audit_log"][0]["old_size"] == "S"


def test_record_action_dismissed_with_note(tmp_path):
    """AC6 — 'dismissed' stores optional note."""
    commander = _make_commander(tmp_path)
    _write_estimate(commander, 10, "S")

    history = mis_sizing.build_history_from_completed(_make_completed_tickets([
        {"number": 101, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 35, "labels": ["backend-auth"]},
        {"number": 102, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 38, "labels": ["backend-auth"]},
    ]))
    sprint_issues = [{"number": 10, "title": "New ticket", "labels": [{"name": "backend-auth"}]}]
    mis_sizing.generate_and_save_flags(commander, "sprint-44", sprint_issues, history)

    data = mis_sizing.record_action(commander, "sprint-44", 10, "dismissed", note="one-off spike")
    flag = next(f for f in data["flags"] if f["issue_number"] == 10)
    assert flag["status"] == "dismissed"
    assert flag["action_note"] == "one-off spike"


def test_record_action_invalid_action(tmp_path):
    commander = _make_commander(tmp_path)
    with pytest.raises(ValueError, match="Invalid action"):
        mis_sizing.record_action(commander, "sprint-44", 10, "approve_all")


def test_record_action_reestimate_missing_size(tmp_path):
    commander = _make_commander(tmp_path)
    with pytest.raises(ValueError, match="Invalid new_size"):
        mis_sizing.record_action(commander, "sprint-44", 10, "reestimated", new_size=None)


# ── AC7: sprint start blocked while pending flags remain ─────────────────────

def test_has_unresolved_flags_pending(tmp_path):
    """AC7 — returns True when flags remain pending."""
    commander = _make_commander(tmp_path)
    _write_estimate(commander, 10, "S")

    history = mis_sizing.build_history_from_completed(_make_completed_tickets([
        {"number": 101, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 35, "labels": ["backend-auth"]},
        {"number": 102, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 38, "labels": ["backend-auth"]},
    ]))
    sprint_issues = [{"number": 10, "title": "New ticket", "labels": [{"name": "backend-auth"}]}]
    mis_sizing.generate_and_save_flags(commander, "sprint-44", sprint_issues, history)

    assert mis_sizing.has_unresolved_flags(commander, "sprint-44") is True


def test_has_unresolved_flags_resolved(tmp_path):
    """AC7 — returns False once all flags are resolved."""
    commander = _make_commander(tmp_path)
    _write_estimate(commander, 10, "S")

    history = mis_sizing.build_history_from_completed(_make_completed_tickets([
        {"number": 101, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 35, "labels": ["backend-auth"]},
        {"number": 102, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 38, "labels": ["backend-auth"]},
    ]))
    sprint_issues = [{"number": 10, "title": "New ticket", "labels": [{"name": "backend-auth"}]}]
    mis_sizing.generate_and_save_flags(commander, "sprint-44", sprint_issues, history)
    mis_sizing.record_action(commander, "sprint-44", 10, "approved")

    assert mis_sizing.has_unresolved_flags(commander, "sprint-44") is False


def test_has_unresolved_flags_no_flags(tmp_path):
    """AC7 — returns False when no flags exist at all."""
    commander = _make_commander(tmp_path)
    assert mis_sizing.has_unresolved_flags(commander, "sprint-44") is False


# ── AC8: audit log ────────────────────────────────────────────────────────────

def test_audit_log_accumulates_entries(tmp_path):
    """AC8 — each action appends a new entry to audit_log."""
    commander = _make_commander(tmp_path)
    _write_estimate(commander, 10, "S")
    _write_estimate(commander, 11, "S")

    history = mis_sizing.build_history_from_completed(_make_completed_tickets([
        {"number": 101, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 35, "labels": ["backend-auth", "api"]},
        {"number": 102, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 38, "labels": ["backend-auth", "api"]},
    ]))
    sprint_issues = [
        {"number": 10, "title": "Ticket A", "labels": [{"name": "backend-auth"}]},
        {"number": 11, "title": "Ticket B", "labels": [{"name": "api"}]},
    ]
    mis_sizing.generate_and_save_flags(commander, "sprint-44", sprint_issues, history)
    mis_sizing.record_action(commander, "sprint-44", 10, "approved")
    data = mis_sizing.record_action(commander, "sprint-44", 11, "reestimated", new_size="M")

    assert len(data["audit_log"]) == 2
    assert {e["action"] for e in data["audit_log"]} == {"approved", "reestimated"}


# ── AC9: idempotent generation, preserves actions ─────────────────────────────

def test_generate_preserves_existing_actions(tmp_path):
    """AC9 — re-generating flags preserves already-resolved actions."""
    commander = _make_commander(tmp_path)
    _write_estimate(commander, 10, "S")

    history = mis_sizing.build_history_from_completed(_make_completed_tickets([
        {"number": 101, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 35, "labels": ["backend-auth"]},
        {"number": 102, "sprint": "sprint-1", "estimated_size": "S",
         "elapsed_minutes": 38, "labels": ["backend-auth"]},
    ]))
    sprint_issues = [{"number": 10, "title": "New ticket", "labels": [{"name": "backend-auth"}]}]
    mis_sizing.generate_and_save_flags(commander, "sprint-44", sprint_issues, history)
    mis_sizing.record_action(commander, "sprint-44", 10, "approved")

    # Re-generate (simulates label change trigger)
    data = mis_sizing.generate_and_save_flags(commander, "sprint-44", sprint_issues, history)
    flag = next(f for f in data["flags"] if f["issue_number"] == 10)
    assert flag["status"] == "approved", "Action should be preserved on re-generation"


def test_minutes_to_size_boundaries():
    """Tier bucketing at boundaries — unified on sizing.letter_from_minutes (#766).

    mis_sizing._minutes_to_size was removed in #766; the canonical reverse lookup
    is sizing.letter_from_minutes, and the XL threshold was raised 60→90.
    """
    from sizing import letter_from_minutes
    assert letter_from_minutes(4) == "S"
    assert letter_from_minutes(5) == "S"
    assert letter_from_minutes(15) == "M"
    assert letter_from_minutes(29) == "M"
    assert letter_from_minutes(30) == "L"
    assert letter_from_minutes(59) == "L"
    assert letter_from_minutes(89) == "L"   # raised XL boundary: 60 is now L
    assert letter_from_minutes(90) == "XL"
    assert letter_from_minutes(100) == "XL"
