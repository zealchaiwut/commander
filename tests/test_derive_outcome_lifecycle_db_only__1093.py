"""TDD tests for _derive_outcome_lifecycle DB-only migration (issue #1093).

Verifies that the function reads parent canonical state and children
exclusively from the DB sprints table — no disk globs, no GitHub label lookups.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO / "apps" / "dashboard"))

import db  # noqa: E402
import server as srv  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Each test gets a fresh in-memory DB — no cross-test pollution."""
    db_file = tmp_path / "test_1093.db"
    original = db.DB_PATH
    db.DB_PATH = db_file
    db.init_db()
    yield
    db.DB_PATH = original


# ── AC3 / UAT1 ────────────────────────────────────────────────────────────────

def test_terminal_parent_unsettled_child_returns_partial_finished():
    """Parent completed + child still running → partial_finished."""
    db.record_sprint_start("sprint-10", project="test/repo")
    db.record_sprint_finish("sprint-10")
    db.record_sprint_start("sprint-10.1", project="test/repo", parent_label="sprint-10")
    # child is 'running' (unsettled)

    lc = srv._derive_outcome_lifecycle(
        "sprint-10", Path("/unused"), "test/repo", "", "", 0,
    )
    assert lc == "partial_finished"


# ── AC4 / UAT2 ────────────────────────────────────────────────────────────────

def test_terminal_parent_all_children_settled_returns_db_state():
    """Parent completed + all children completed → completed (DB state)."""
    db.record_sprint_start("sprint-11", project="test/repo")
    db.record_sprint_finish("sprint-11")
    db.record_sprint_start("sprint-11.1", project="test/repo", parent_label="sprint-11")
    db.record_sprint_finish("sprint-11.1")
    db.record_sprint_start("sprint-11.2", project="test/repo", parent_label="sprint-11")
    db.record_sprint_finish("sprint-11.2")

    lc = srv._derive_outcome_lifecycle(
        "sprint-11", Path("/unused"), "test/repo", "", "", 0,
    )
    assert lc == "completed"


def test_terminal_parent_child_ready_to_merge_is_settled():
    """ready_to_merge child counts as settled (in _CHILD_SETTLED_STATES)."""
    db.record_sprint_start("sprint-20", project="test/repo")
    db.record_sprint_finish("sprint-20")
    db.record_sprint_start("sprint-20.1", project="test/repo", parent_label="sprint-20")
    db.record_sprint_ready_to_merge("sprint-20.1")

    lc = srv._derive_outcome_lifecycle(
        "sprint-20", Path("/unused"), "test/repo", "", "", 0,
    )
    assert lc == "completed"


# ── AC5 / UAT3 ────────────────────────────────────────────────────────────────

def test_nonterminal_parent_returns_running_no_child_check():
    """Non-terminal parent (running) → returns 'running' directly; no child check."""
    db.record_sprint_start("sprint-12", project="test/repo")
    # still running
    db.record_sprint_start("sprint-12.1", project="test/repo", parent_label="sprint-12")
    # child unsettled too, but should not matter

    lc = srv._derive_outcome_lifecycle(
        "sprint-12", Path("/unused"), "test/repo", "", "", 0,
    )
    assert lc == "running"


# ── AC7 — no regression for no-children case ──────────────────────────────────

def test_terminal_parent_no_children_returns_db_state():
    """Terminal parent with no children → DB state directly."""
    db.record_sprint_start("sprint-13", project="test/repo")
    db.record_sprint_finish("sprint-13")

    lc = srv._derive_outcome_lifecycle(
        "sprint-13", Path("/unused"), "test/repo", "", "", 0,
    )
    assert lc == "completed"


def test_needs_rework_parent_no_children_returns_needs_rework():
    """needs_rework parent with no children → needs_rework."""
    db.record_sprint_start("sprint-14", project="test/repo")
    db.record_sprint_needs_rework("sprint-14")

    lc = srv._derive_outcome_lifecycle(
        "sprint-14", Path("/unused"), "test/repo", "", "", 0,
    )
    assert lc == "needs_rework"


def test_no_db_row_falls_back_to_canonical_pane_state():
    """No DB row for the sprint → falls back to canonical_lifecycle(pane_state)."""
    lc = srv._derive_outcome_lifecycle(
        "sprint-99", Path("/unused"), "test/repo", "", "completed", 0,
    )
    assert lc == "completed"


def test_no_db_row_unknown_pane_state_returns_unknown():
    """No DB row, empty pane_state → 'unknown'."""
    lc = srv._derive_outcome_lifecycle(
        "sprint-99", Path("/unused"), "test/repo", "", "", 0,
    )
    assert lc == "unknown"


# ── AC6 — _has_rework_tickets removed from code path ─────────────────────────

def test_has_rework_tickets_not_called(monkeypatch):
    """_has_rework_tickets must not be called by _derive_outcome_lifecycle."""
    db.record_sprint_start("sprint-30", project="test/repo")
    db.record_sprint_finish("sprint-30")

    called = []
    monkeypatch.setattr(srv, "_has_rework_tickets", lambda *a, **kw: called.append(1) or False)

    srv._derive_outcome_lifecycle("sprint-30", Path("/unused"), "test/repo", "", "", 0)
    assert called == [], "_has_rework_tickets must not be called from _derive_outcome_lifecycle"


# ── AC2 — no disk glob (_child_sprint_labels_from_plans) ─────────────────────

def test_child_sprint_labels_from_plans_not_called(monkeypatch):
    """_child_sprint_labels_from_plans must not be called from this function."""
    db.record_sprint_start("sprint-31", project="test/repo")
    db.record_sprint_finish("sprint-31")
    db.record_sprint_start("sprint-31.1", project="test/repo", parent_label="sprint-31")
    db.record_sprint_finish("sprint-31.1")

    called = []
    if hasattr(srv, "_child_sprint_labels_from_plans"):
        monkeypatch.setattr(
            srv, "_child_sprint_labels_from_plans",
            lambda *a, **kw: called.append(1) or [],
        )

    srv._derive_outcome_lifecycle("sprint-31", Path("/unused"), "test/repo", "", "", 0)
    assert called == [], "_child_sprint_labels_from_plans must not be called from _derive_outcome_lifecycle"


# ── AC1 — no GitHub label lookup ─────────────────────────────────────────────

def test_github_label_lookup_not_called(monkeypatch):
    """_get_sprint_issues (GitHub) must not be called by _derive_outcome_lifecycle."""
    db.record_sprint_start("sprint-32", project="test/repo")
    db.record_sprint_finish("sprint-32")
    db.record_sprint_start("sprint-32.1", project="test/repo", parent_label="sprint-32")
    # child running (unsettled)

    called = []
    monkeypatch.setattr(srv, "_get_sprint_issues", lambda *a, **kw: called.append(1) or [])

    lc = srv._derive_outcome_lifecycle("sprint-32", Path("/unused"), "test/repo", "", "", 0)
    assert called == [], "_get_sprint_issues (GitHub) must not be called from _derive_outcome_lifecycle"
    assert lc == "partial_finished"
