"""Acceptance tests for issue #861 — Plan next sprint planning engine.

Each test is anchored to a specific acceptance criterion from the issue. The
planner (`services.sprint_manager.sprint_planner`) is pure and
dependency-injected, so every selection/ordering rule is exercised here without
touching GitHub, the estimator subprocess, or the filesystem.
"""
from __future__ import annotations

import pytest

from services.sprint_manager.sprint_planner import (
    PENDING_SIGNOFF_STATUS,
    STATUS_CONFLICT,
    STATUS_EMPTY,
    STATUS_NO_MILESTONE,
    STATUS_OK,
    PlanTicket,
    active_milestone,
    carry_over_tickets,
    dag_order,
    plan_next_sprint,
    select_milestone_backlog,
)

SIZE_MIN = {"S": 5, "M": 15, "L": 30, "XL": 60}


def _ident(tickets):
    """order_fn that leaves selection order untouched (isolates packing logic)."""
    return list(tickets)


def _sized(number, size, **kw):
    return PlanTicket(number=number, size=size, minutes=SIZE_MIN[size], **kw)


# ── AC1: button only when an active milestone exists ──────────────────────────

def test_no_active_milestone_returns_no_milestone_status():
    """AC1: with no active milestone there is nothing to plan."""
    res = plan_next_sprint(
        has_active_milestone=False, capacity_minutes=180,
        carry_over=[], backlog=[_sized(1, "S")], next_sprint_number=67,
        order_fn=_ident, size_minutes=SIZE_MIN,
    )
    assert res.status == STATUS_NO_MILESTONE
    assert res.selected == []


def test_active_milestone_picks_nearest_open_due_date():
    """AC1: the active milestone is the open milestone with the nearest due date."""
    milestones = [
        {"title": "Q3", "state": "open", "dueOn": "2026-09-01T00:00:00Z"},
        {"title": "Q2", "state": "open", "dueOn": "2026-06-01T00:00:00Z"},
        {"title": "Done", "state": "closed", "dueOn": "2026-01-01T00:00:00Z"},
    ]
    assert active_milestone(milestones)["title"] == "Q2"
    assert active_milestone([]) is None
    assert active_milestone([{"title": "X", "state": "closed"}]) is None


# ── AC3: only the active milestone's open backlog is considered ───────────────

def test_select_milestone_backlog_filters_milestone_state_and_column():
    """AC3: other milestones, closed issues, and non-backlog columns are excluded."""
    issues = [
        {"number": 1, "state": "open", "milestone": {"title": "M1"}, "col": "backlog"},
        {"number": 2, "state": "open", "milestone": {"title": "M2"}, "col": "backlog"},
        {"number": 3, "state": "closed", "milestone": {"title": "M1"}, "col": "backlog"},
        {"number": 4, "state": "open", "milestone": {"title": "M1"}, "col": "in-progress"},
        {"number": 5, "state": "open", "milestone": None, "col": "backlog"},
    ]
    out = select_milestone_backlog(
        issues, "M1", is_backlog=lambda i: i["col"] == "backlog")
    assert [i["number"] for i in out] == [1]


# ── AC4: carry-over tickets first ─────────────────────────────────────────────

def test_carry_over_tickets_are_the_unfinished_ones():
    """AC4: carry-over = unfinished tickets from the most recent completed sprint."""
    prev = [
        {"number": 10, "done": False},
        {"number": 11, "done": True},
        {"number": 12, "done": False},
    ]
    out = carry_over_tickets(prev, is_finished=lambda i: i["done"])
    assert [i["number"] for i in out] == [10, 12]


def test_carry_over_selected_before_backlog():
    """AC4: carry-over tickets take priority over fresh backlog selection."""
    carry = [_sized(10, "M", carry_over=True)]
    backlog = [_sized(1, "M"), _sized(2, "M")]
    res = plan_next_sprint(
        has_active_milestone=True, capacity_minutes=30,  # fits exactly two M (15+15)
        carry_over=carry, backlog=backlog, next_sprint_number=67,
        order_fn=_ident, size_minutes=SIZE_MIN,
    )
    assert res.status == STATUS_OK
    # capacity 30 == 10(M) + 1(M); ticket 2 must be dropped, carry-over kept.
    assert [t.number for t in res.selected] == [10, 1]
    assert res.selected[0].carry_over is True


# ── AC5: capacity is respected ────────────────────────────────────────────────

def test_total_minutes_never_exceed_capacity():
    """AC5: selected minutes ≤ capacity; an over-budget ticket is skipped."""
    backlog = [_sized(1, "L"), _sized(2, "L"), _sized(3, "S")]  # 30, 30, 5
    res = plan_next_sprint(
        has_active_milestone=True, capacity_minutes=35,
        carry_over=[], backlog=backlog, next_sprint_number=67,
        order_fn=_ident, size_minutes=SIZE_MIN,
    )
    assert res.status == STATUS_OK
    assert res.total_minutes <= 35
    # 30 (first L) fits; second L would push to 60 → skipped; S (5) fits → 35.
    assert [t.number for t in res.selected] == [1, 3]
    assert res.total_minutes == 35


# ── AC6: estimated tickets preferred over unsized ─────────────────────────────

def test_estimated_preferred_over_unsized():
    """AC6: when capacity is tight, sized tickets are taken before unsized ones."""
    estimated = _sized(1, "L")            # 30
    unsized = PlanTicket(number=2)        # no size
    calls = []

    def estimate_fn(t):
        calls.append(t.number)
        return _sized(t.number, "S")

    res = plan_next_sprint(
        has_active_milestone=True, capacity_minutes=30,  # only room for the L
        carry_over=[], backlog=[unsized, estimated], next_sprint_number=67,
        estimate_fn=estimate_fn, order_fn=_ident, size_minutes=SIZE_MIN,
    )
    assert res.status == STATUS_OK
    # The estimated L is taken first and fills capacity; unsized #2 never fits.
    assert [t.number for t in res.selected] == [1]


# ── AC7: unsized tickets estimated lazily; un-estimatable excluded ────────────

def test_unsized_estimated_when_capacity_remains():
    """AC7: an unsized ticket that fits remaining capacity is estimated and added."""
    unsized = PlanTicket(number=2)

    def estimate_fn(t):
        return _sized(t.number, "S")  # 5 min

    res = plan_next_sprint(
        has_active_milestone=True, capacity_minutes=30,
        carry_over=[], backlog=[_sized(1, "M"), unsized], next_sprint_number=67,
        estimate_fn=estimate_fn, order_fn=_ident, size_minutes=SIZE_MIN,
    )
    assert res.status == STATUS_OK
    assert {t.number for t in res.selected} == {1, 2}
    assert res.selected[-1].size == "S"  # newly estimated


def test_unestimatable_ticket_is_excluded_not_blocking():
    """AC7: a ticket the estimator cannot size is excluded, not fatal to the draft."""
    good = _sized(1, "S")
    bad = PlanTicket(number=2)

    def estimate_fn(t):
        return None  # estimator timed out / failed

    res = plan_next_sprint(
        has_active_milestone=True, capacity_minutes=180,
        carry_over=[], backlog=[good, bad], next_sprint_number=67,
        estimate_fn=estimate_fn, order_fn=_ident, size_minutes=SIZE_MIN,
    )
    assert res.status == STATUS_OK
    assert [t.number for t in res.selected] == [1]  # bad excluded, draft survives


def test_estimator_not_called_once_capacity_exhausted():
    """AC7: unsized tickets are only run through the estimator while capacity remains."""
    calls = []

    def estimate_fn(t):
        calls.append(t.number)
        return _sized(t.number, "S")

    res = plan_next_sprint(
        has_active_milestone=True, capacity_minutes=30,  # filled by the L below
        carry_over=[], backlog=[_sized(1, "L"), PlanTicket(number=2)],
        next_sprint_number=67, estimate_fn=estimate_fn, order_fn=_ident,
        size_minutes=SIZE_MIN,
    )
    assert res.status == STATUS_OK
    assert calls == []  # capacity gone before reaching the unsized ticket


# ── AC8: dependency (DAG) ordering ────────────────────────────────────────────

def test_dag_order_places_dependency_before_dependent():
    """AC8: a ticket never appears before a ticket it depends on."""
    # #1 owns shared.py (first to touch it); #2 also touches it → #2 depends on #1.
    a = PlanTicket(number=1, files=["shared.py"])
    b = PlanTicket(number=2, files=["shared.py", "b.py"])
    ordered = dag_order([a, b])
    nums = [t.number for t in ordered]
    assert nums.index(1) < nums.index(2)
    assert set(nums) == {1, 2}  # no ticket lost


def test_dag_order_degrades_on_cycle_without_dropping_tickets():
    """AC8: a dependency cycle degrades to input order, never drops tickets."""
    # Two tickets each owning a file the other also touches → mutual dependency.
    a = PlanTicket(number=1, files=["f.py", "g.py"])
    b = PlanTicket(number=2, files=["g.py", "f.py"])
    ordered = dag_order([a, b])
    assert {t.number for t in ordered} == {1, 2}


def test_plan_uses_injected_order_fn():
    """AC8: the planner orders its selection through the injected order function."""
    backlog = [_sized(1, "S"), _sized(2, "S")]
    res = plan_next_sprint(
        has_active_milestone=True, capacity_minutes=180,
        carry_over=[], backlog=backlog, next_sprint_number=67,
        order_fn=lambda ts: list(reversed(ts)), size_minutes=SIZE_MIN,
    )
    assert [t.number for t in res.selected] == [2, 1]


# ── AC9: pending sign-off status constant exists & is distinct ────────────────

def test_pending_signoff_status_is_distinct():
    """AC9: the planned sprint's lifecycle status is a distinct sentinel."""
    assert PENDING_SIGNOFF_STATUS == "pending-signoff"
    assert PENDING_SIGNOFF_STATUS not in {"draft", "running", "complete", "cancelled"}


# ── AC10: empty-state guards ──────────────────────────────────────────────────

def test_zero_capacity_yields_empty_no_sprint():
    """AC10: zero capacity → informative empty result, no selection."""
    res = plan_next_sprint(
        has_active_milestone=True, capacity_minutes=0,
        carry_over=[], backlog=[_sized(1, "S")], next_sprint_number=67,
        order_fn=_ident, size_minutes=SIZE_MIN,
    )
    assert res.status == STATUS_EMPTY
    assert res.selected == []
    assert "capacity" in res.reason.lower()


def test_no_eligible_tickets_yields_empty():
    """AC10: no eligible tickets → empty result with a message, no sprint."""
    res = plan_next_sprint(
        has_active_milestone=True, capacity_minutes=180,
        carry_over=[], backlog=[], next_sprint_number=67,
        order_fn=_ident, size_minutes=SIZE_MIN,
    )
    assert res.status == STATUS_EMPTY
    assert res.selected == []
    assert res.reason


# ── AC11: idempotent — existing pending-signoff draft prompts, no duplicate ───

def test_existing_pending_sprint_returns_conflict():
    """AC11: when a pending-sign-off draft exists, planning prompts rather than duplicating."""
    res = plan_next_sprint(
        has_active_milestone=True, capacity_minutes=180,
        carry_over=[], backlog=[_sized(1, "S")], next_sprint_number=67,
        existing_pending_label="sprint-67", order_fn=_ident, size_minutes=SIZE_MIN,
    )
    assert res.status == STATUS_CONFLICT
    assert res.existing_pending_label == "sprint-67"
    assert res.selected == []  # nothing created
