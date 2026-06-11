"""Tests for issue #856 — post-sprint reconciliation check for loose ends.

Each test is anchored to a specific acceptance criterion. The reconciliation
logic is pure and dependency-injected: GitHub data (summary issues, PR info,
ticket labels) is passed in as plain dicts, and the event sink / clock are
injected, so no GitHub API is touched in these tests.

AC1  After a sprint finishes (explicit Finish or natural end), a reconciliation
     pass runs automatically — exposed as run_reconciliation() and wired into
     the sprint-finish path in sprint_manager.main().
AC2  Reconciliation checks a summary issue exists for the EXACT sprint label.
AC3  Reconciliation checks whether the sprint PR merged; if not, the reason
     (e.g. merge conflict) is captured and conflicting files are listed.
AC4  Reconciliation checks no stale sprint status labels remain on tickets.
AC5  Results are surfaced on the history card (history API exposes a
     reconciliation block with unresolved items marked).
AC6  Results are emitted as an activity-log event tied to the sprint.
AC7  A cleanly-closed sprint shows an all-clear (all_clear=True) checklist.
AC8  A failing sprint shows exactly what is unresolved and why, without
     requiring GitHub (detail strings carry reason + files; surfaced locally).
AC9  Idempotent: re-running on an already-clean record produces no duplicate
     log event and no duplicate history entry.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.sprint_manager import reconciliation as rec  # noqa: E402


# ── fixtures / helpers ────────────────────────────────────────────────────────

def _summary_issue(label):
    return {"title": f"Sprint {label.split('-')[-1]} Executive Summary",
            "labels": [{"name": "docs"}, {"name": label}, {"name": "sprint-summary"}]}


class _Sink:
    """Captures emitted activity-log events."""
    def __init__(self):
        self.events = []

    def __call__(self, *, type, target, actor, detail, project, action_id=None):
        self.events.append({"type": type, "target": target, "actor": actor,
                            "detail": detail, "project": project, "action_id": action_id})


def _fixed_clock():
    return "2026-06-11T00:00:00"


# ── AC2: summary-issue check ──────────────────────────────────────────────────

def test_ac2_summary_issue_present_passes():
    """AC2: a summary issue carrying the sprint label passes the check."""
    r = rec.check_summary_issue("sprint-65", [_summary_issue("sprint-65")])
    assert r["name"] == "summary_issue"
    assert r["ok"] is True


def test_ac2_summary_issue_missing_fails():
    """AC2: no summary issue at all → check fails and says so."""
    r = rec.check_summary_issue("sprint-65", [])
    assert r["ok"] is False
    assert "sprint-65" in r["detail"]


def test_ac2_summary_issue_must_match_exact_label():
    """AC2: a summary for a DIFFERENT sprint does not satisfy this sprint."""
    r = rec.check_summary_issue("sprint-65", [_summary_issue("sprint-64")])
    assert r["ok"] is False


# ── AC3: sprint-PR check ──────────────────────────────────────────────────────

def test_ac3_pr_merged_passes():
    """AC3: a merged sprint PR passes."""
    r = rec.check_sprint_pr({"number": 42, "merged": True})
    assert r["name"] == "sprint_pr"
    assert r["ok"] is True
    assert r["pr_number"] == 42


def test_ac3_pr_conflict_captures_reason_and_files():
    """AC3/AC8: an unmerged PR captures the reason and lists conflicting files."""
    r = rec.check_sprint_pr({
        "number": 42, "merged": False,
        "reason": "merge conflict",
        "conflicting_files": ["src/foo.ts", "src/bar.ts"],
    })
    assert r["ok"] is False
    assert r["pr_number"] == 42
    assert r["reason"] == "merge conflict"
    assert r["conflicting_files"] == ["src/foo.ts", "src/bar.ts"]
    # AC8: the human-readable detail states the reason AND names the files.
    assert "merge conflict" in r["detail"]
    assert "src/foo.ts" in r["detail"] and "src/bar.ts" in r["detail"]
    assert "#42" in r["detail"]


# ── AC4: stale-label check ────────────────────────────────────────────────────

def test_ac4_no_stale_labels_passes():
    """AC4: tickets that ended in UAT (awaiting sign-off) are not stale."""
    r = rec.check_stale_labels([
        {"number": 1, "labels": [{"name": "UAT"}]},
        {"number": 2, "labels": [{"name": "UAT"}]},
    ])
    assert r["name"] == "stale_labels"
    assert r["ok"] is True
    assert r["tickets"] == []


def test_ac4_stale_labels_detected_and_listed():
    """AC4/AC8: lingering in-flight labels are flagged with ticket + label."""
    r = rec.check_stale_labels([
        {"number": 11, "labels": [{"name": "in-progress"}]},
        {"number": 12, "labels": [{"name": "SIT"}]},
        {"number": 13, "labels": [{"name": "UAT"}]},
    ])
    assert r["ok"] is False
    offenders = {t["issue"]: t["labels"] for t in r["tickets"]}
    assert offenders == {11: ["in-progress"], 12: ["SIT"]}
    assert "11" in r["detail"] and "in-progress" in r["detail"]


# ── AC7: clean sprint → all-clear ─────────────────────────────────────────────

def test_ac7_clean_sprint_all_clear():
    """AC7: all three checks pass → all_clear is True."""
    result = rec.reconcile_sprint(
        "sprint-65",
        summary_issues=[_summary_issue("sprint-65")],
        pr_info={"number": 42, "merged": True},
        tickets=[{"number": 1, "labels": [{"name": "UAT"}]}],
    )
    assert result["all_clear"] is True
    assert all(c["ok"] for c in result["checks"])
    assert len(result["checks"]) == 3


def test_ac8_failing_sprint_reports_each_unresolved_item():
    """AC8: any failure → all_clear False and each failed check explains itself."""
    result = rec.reconcile_sprint(
        "sprint-65",
        summary_issues=[],
        pr_info={"number": 42, "merged": False, "reason": "merge conflict",
                 "conflicting_files": ["src/foo.ts"]},
        tickets=[{"number": 7, "labels": [{"name": "in-progress"}]}],
    )
    assert result["all_clear"] is False
    by_name = {c["name"]: c for c in result["checks"]}
    assert by_name["summary_issue"]["ok"] is False
    assert by_name["sprint_pr"]["ok"] is False and "src/foo.ts" in by_name["sprint_pr"]["detail"]
    assert by_name["stale_labels"]["ok"] is False and "7" in by_name["stale_labels"]["detail"]


# ── AC1 + AC6: orchestrator runs, persists, emits event ───────────────────────

def test_ac1_run_reconciliation_persists_record(tmp_path):
    """AC1: run_reconciliation writes a reconciliation block into the state file."""
    state_path = tmp_path / "sprint-65-state.json"
    state_path.write_text(json.dumps({"sprint_label": "sprint-65", "issues": []}))
    sink = _Sink()
    result = rec.run_reconciliation(
        sprint_label="sprint-65", sprint_number=65, project="o/r",
        state_path=state_path,
        summary_issues=[_summary_issue("sprint-65")],
        pr_info={"number": 42, "merged": True},
        tickets=[{"number": 1, "labels": [{"name": "UAT"}]}],
        emit_event=sink, now=_fixed_clock,
    )
    assert result["all_clear"] is True
    persisted = json.loads(state_path.read_text())["reconciliation"]
    assert persisted["all_clear"] is True
    assert persisted["sprint_label"] == "sprint-65"
    assert "checked_at" in persisted


def test_ac6_emits_activity_event_tied_to_sprint(tmp_path):
    """AC6: a reconciliation event is emitted, tied to the sprint label."""
    state_path = tmp_path / "sprint-65-state.json"
    sink = _Sink()
    rec.run_reconciliation(
        sprint_label="sprint-65", sprint_number=65, project="o/r",
        state_path=state_path,
        summary_issues=[], pr_info={"number": 42, "merged": True},
        tickets=[], emit_event=sink, now=_fixed_clock,
    )
    assert len(sink.events) == 1
    ev = sink.events[0]
    assert ev["target"] == "sprint-65"
    assert ev["project"] == "o/r"
    assert "all_clear" in ev["detail"] and "checks" in ev["detail"]


def test_ac1_wired_into_sprint_finish_path():
    """AC1: sprint_manager.main() invokes run_reconciliation on the finish path."""
    src = (REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py").read_text()
    assert "run_reconciliation" in src, "reconciliation must be wired into sprint_manager"


# ── AC9: idempotency ──────────────────────────────────────────────────────────

def test_ac9_rerun_clean_sprint_no_duplicate_event_or_entry(tmp_path):
    """AC9: re-running on an unchanged result emits no second event and keeps
    a single reconciliation entry in the state file."""
    state_path = tmp_path / "sprint-65-state.json"
    state_path.write_text(json.dumps({"sprint_label": "sprint-65", "issues": []}))
    sink = _Sink()
    kwargs = dict(
        sprint_label="sprint-65", sprint_number=65, project="o/r",
        state_path=state_path,
        summary_issues=[_summary_issue("sprint-65")],
        pr_info={"number": 42, "merged": True},
        tickets=[{"number": 1, "labels": [{"name": "UAT"}]}],
        emit_event=sink, now=_fixed_clock,
    )
    rec.run_reconciliation(**kwargs)
    rec.run_reconciliation(**kwargs)

    # Exactly one event across both runs.
    assert len(sink.events) == 1
    # The state file holds a single reconciliation object, not a list/dup.
    persisted = json.loads(state_path.read_text())
    assert isinstance(persisted["reconciliation"], dict)
    # checked_at is preserved (not refreshed) when nothing changed.
    assert persisted["reconciliation"]["checked_at"] == _fixed_clock()


def test_ac9_changed_result_emits_again(tmp_path):
    """AC9 (converse): when the outcome actually changes, a new event fires —
    idempotency must not suppress a genuine state change."""
    state_path = tmp_path / "sprint-65-state.json"
    state_path.write_text(json.dumps({"sprint_label": "sprint-65", "issues": []}))
    sink = _Sink()
    # First pass: PR unmerged (failure).
    rec.run_reconciliation(
        sprint_label="sprint-65", sprint_number=65, project="o/r", state_path=state_path,
        summary_issues=[_summary_issue("sprint-65")],
        pr_info={"number": 42, "merged": False, "reason": "merge conflict",
                 "conflicting_files": ["a.py"]},
        tickets=[], emit_event=sink, now=_fixed_clock,
    )
    # Second pass: PR now merged (resolved).
    rec.run_reconciliation(
        sprint_label="sprint-65", sprint_number=65, project="o/r", state_path=state_path,
        summary_issues=[_summary_issue("sprint-65")],
        pr_info={"number": 42, "merged": True},
        tickets=[], emit_event=sink, now=_fixed_clock,
    )
    assert len(sink.events) == 2


# ── AC5: history surfacing ────────────────────────────────────────────────────

def test_ac5_history_service_surfaces_reconciliation(tmp_path):
    """AC5: the sprint-history enrichment reads the reconciliation block from
    the state file so the history card can render the checklist."""
    from apps.dashboard.routers import sprint_history_service as svc

    sprints_dir = tmp_path
    state = {
        "sprint_label": "sprint-65",
        "issues": [],
        "reconciliation": {
            "sprint_label": "sprint-65",
            "all_clear": False,
            "checked_at": "2026-06-11T00:00:00",
            "checks": [{"name": "summary_issue", "ok": False, "detail": "missing"}],
        },
    }
    (sprints_dir / "sprint-65-state.json").write_text(json.dumps(state))
    enrich = svc._enrich_from_state("sprint-65", sprints_dir)
    assert enrich["reconciliation"]["all_clear"] is False
    assert enrich["reconciliation"]["checks"][0]["ok"] is False


def test_ac5_history_item_model_has_reconciliation_field():
    """AC5: the API response model carries a reconciliation field."""
    from apps.dashboard.routers.sprint_history import SprintHistoryItem
    item = SprintHistoryItem(lifecycle_state="finished",
                             reconciliation={"all_clear": True, "checks": []})
    assert item.reconciliation == {"all_clear": True, "checks": []}
