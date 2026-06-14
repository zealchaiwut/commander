import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from conftest import make_sprint_db

import services.sprint_manager.sprint_repo as sprint_repo
from services.sprint_manager.sprint_repo import (
    SprintNotFound,
    TicketNotFound,
    add_ticket,
    create_sprint,
    get_sprint_by_label,
    list_sprints,
    list_tickets,
    reorder_tickets,
    update_sprint_status,
    update_ticket_status,
)


@pytest.fixture(autouse=True)
def sqlite_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    make_sprint_db(engine)
    monkeypatch.setattr(sprint_repo, "_session_factory", sessionmaker(bind=engine))
    yield engine


# ── create_sprint ─────────────────────────────────────────────────────────────

def test_create_sprint_returns_sprint_with_correct_fields():
    s = create_sprint(label="S-1", goal="ship auth", project="myapp")
    assert s.label == "S-1"
    assert s.goal == "ship auth"
    assert s.project == "myapp"
    assert s.status == "pending"


def test_create_sprint_is_persisted():
    create_sprint(label="S-1", goal="ship auth", project="myapp")
    fetched = get_sprint_by_label("S-1")
    assert fetched is not None
    assert fetched.label == "S-1"


# ── get_sprint_by_label ───────────────────────────────────────────────────────

def test_get_sprint_by_label_returns_correct_sprint():
    create_sprint(label="S-2", goal="ship payments", project="myapp")
    s = get_sprint_by_label("S-2")
    assert s is not None
    assert s.label == "S-2"
    assert s.goal == "ship payments"


def test_get_sprint_by_label_returns_none_for_missing():
    result = get_sprint_by_label("nonexistent")
    assert result is None


# ── list_sprints ──────────────────────────────────────────────────────────────

def test_list_sprints_returns_all_sprints():
    create_sprint(label="A", goal="g1", project="p")
    create_sprint(label="B", goal="g2", project="p")
    sprints = list_sprints()
    labels = {s.label for s in sprints}
    assert "A" in labels
    assert "B" in labels


def test_list_sprints_empty():
    assert list_sprints() == []


# ── update_sprint_status ──────────────────────────────────────────────────────

def test_update_sprint_status_to_running_sets_started_at():
    create_sprint(label="S-1", goal="g", project="p")
    update_sprint_status("S-1", "running")
    s = get_sprint_by_label("S-1")
    assert s.status == "running"
    assert s.started_at is not None
    assert s.completed_at is None
    assert s.cancelled_at is None


def test_update_sprint_status_to_complete_sets_completed_at():
    create_sprint(label="S-1", goal="g", project="p")
    update_sprint_status("S-1", "running")
    update_sprint_status("S-1", "complete")
    s = get_sprint_by_label("S-1")
    assert s.status == "complete"
    assert s.completed_at is not None
    assert s.cancelled_at is None


def test_update_sprint_status_to_cancelled_sets_cancelled_at():
    create_sprint(label="S-1", goal="g", project="p")
    update_sprint_status("S-1", "cancelled")
    s = get_sprint_by_label("S-1")
    assert s.status == "cancelled"
    assert s.cancelled_at is not None
    assert s.completed_at is None


def test_update_sprint_status_raises_for_missing_sprint():
    with pytest.raises(SprintNotFound):
        update_sprint_status("nonexistent", "running")


# ── add_ticket ────────────────────────────────────────────────────────────────

def test_add_ticket_returns_ticket_with_correct_fields():
    create_sprint(label="S-1", goal="g", project="p")
    t = add_ticket(sprint_label="S-1", issue_number=42, position=0)
    assert t.issue_number == 42
    assert t.position == 0
    assert t.status == "pending"


def test_add_ticket_is_persisted():
    create_sprint(label="S-1", goal="g", project="p")
    add_ticket(sprint_label="S-1", issue_number=42, position=0)
    tickets = list_tickets("S-1")
    assert len(tickets) == 1
    assert tickets[0].issue_number == 42


def test_add_ticket_raises_for_missing_sprint():
    with pytest.raises(SprintNotFound):
        add_ticket(sprint_label="ghost", issue_number=1, position=0)


# ── update_ticket_status ──────────────────────────────────────────────────────

def test_update_ticket_status_to_running_sets_started_at():
    create_sprint(label="S-1", goal="g", project="p")
    add_ticket(sprint_label="S-1", issue_number=42, position=0)
    update_ticket_status("S-1", 42, "running")
    tickets = list_tickets("S-1")
    t = tickets[0]
    assert t.status == "running"
    assert t.started_at is not None
    assert t.completed_at is None
    assert t.actual_elapsed_seconds is None


def test_update_ticket_status_to_done_sets_completed_at_and_elapsed():
    create_sprint(label="S-1", goal="g", project="p")
    add_ticket(sprint_label="S-1", issue_number=42, position=0)
    update_ticket_status("S-1", 42, "running")
    update_ticket_status("S-1", 42, "done")
    tickets = list_tickets("S-1")
    t = tickets[0]
    assert t.status == "done"
    assert t.completed_at is not None
    assert t.actual_elapsed_seconds is not None
    assert t.actual_elapsed_seconds >= 0


def test_update_ticket_status_to_failed_sets_elapsed():
    create_sprint(label="S-1", goal="g", project="p")
    add_ticket(sprint_label="S-1", issue_number=10, position=0)
    update_ticket_status("S-1", 10, "running")
    update_ticket_status("S-1", 10, "failed")
    tickets = list_tickets("S-1")
    assert tickets[0].actual_elapsed_seconds is not None


def test_update_ticket_status_to_skipped_sets_elapsed():
    create_sprint(label="S-1", goal="g", project="p")
    add_ticket(sprint_label="S-1", issue_number=7, position=0)
    update_ticket_status("S-1", 7, "running")
    update_ticket_status("S-1", 7, "skipped")
    tickets = list_tickets("S-1")
    assert tickets[0].actual_elapsed_seconds is not None


def test_update_ticket_status_raises_ticket_not_found():
    create_sprint(label="S-1", goal="g", project="p")
    with pytest.raises(TicketNotFound):
        update_ticket_status("S-1", 9999, "done")


def test_update_ticket_status_raises_sprint_not_found():
    with pytest.raises(SprintNotFound):
        update_ticket_status("ghost", 1, "done")


# ── list_tickets ──────────────────────────────────────────────────────────────

def test_list_tickets_returns_ordered_by_position():
    create_sprint(label="S-1", goal="g", project="p")
    add_ticket(sprint_label="S-1", issue_number=30, position=2)
    add_ticket(sprint_label="S-1", issue_number=10, position=0)
    add_ticket(sprint_label="S-1", issue_number=20, position=1)
    tickets = list_tickets("S-1")
    assert [t.issue_number for t in tickets] == [10, 20, 30]


def test_list_tickets_raises_for_missing_sprint():
    with pytest.raises(SprintNotFound):
        list_tickets("ghost")


# ── reorder_tickets ───────────────────────────────────────────────────────────

def test_reorder_tickets_updates_positions():
    create_sprint(label="S-1", goal="g", project="p")
    add_ticket(sprint_label="S-1", issue_number=10, position=0)
    add_ticket(sprint_label="S-1", issue_number=20, position=1)
    add_ticket(sprint_label="S-1", issue_number=30, position=2)
    reorder_tickets("S-1", [30, 10, 20])
    tickets = list_tickets("S-1")
    assert [t.issue_number for t in tickets] == [30, 10, 20]


def test_reorder_tickets_raises_for_missing_sprint():
    with pytest.raises(SprintNotFound):
        reorder_tickets("ghost", [1, 2])
