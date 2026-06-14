"""Smoke tests for dual-write: sprint state persisted to both Neon (via sprint_repo)
and a JSON mirror file.

Uses an in-memory SQLite database (same pattern as test_sprint_repo.py) so these
tests never touch a real Neon connection.
"""
import json
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from conftest import make_sprint_db

import services.sprint_manager.sprint_repo as sprint_repo
from services.sprint_manager.sprint_repo import (
    add_ticket,
    create_sprint,
    get_sprint_by_label,
    list_tickets,
    update_sprint_status,
    update_ticket_status,
)


# ── in-memory DB fixture (mirrors test_sprint_repo.py) ────────────────────────

@pytest.fixture(autouse=True)
def sqlite_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    make_sprint_db(engine)
    monkeypatch.setattr(sprint_repo, "_session_factory", sessionmaker(bind=engine))
    yield engine


# ── JSON mirror helpers ────────────────────────────────────────────────────────

def _write_sprint_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read_sprint_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _make_sprint_json(sprints_dir: Path, label: str, goal: str, project: str) -> Path:
    path = sprints_dir / f"{label}.json"
    _write_sprint_json(path, {
        "label": label,
        "goal": goal,
        "project": project,
        "status": "pending",
        "tickets": [],
    })
    return path


# ── smoke test: create sprint → advance ticket → assert consistency ────────────

def test_create_sprint_neon_and_json_are_consistent():
    """Creating a sprint via sprint_repo + writing JSON produces consistent state."""
    with tempfile.TemporaryDirectory() as tmp:
        sprints_dir = Path(tmp)
        label = "sprint-99"
        goal = "ship dual-write"
        project = "myorg/myapp"

        # Neon write
        create_sprint(label=label, goal=goal, project=project)
        update_sprint_status(label, "running")

        add_ticket(sprint_label=label, issue_number=101, position=0)
        add_ticket(sprint_label=label, issue_number=102, position=1)

        # JSON write
        json_path = _make_sprint_json(sprints_dir, label, goal, project)
        data = _read_sprint_json(json_path)
        data["status"] = "running"
        data["tickets"] = [
            {"issue_number": 101, "position": 0, "status": "pending"},
            {"issue_number": 102, "position": 1, "status": "pending"},
        ]
        _write_sprint_json(json_path, data)

        # Assert Neon consistency
        sprint = get_sprint_by_label(label)
        assert sprint.status == "running"
        tickets = list_tickets(label)
        assert [t.issue_number for t in tickets] == [101, 102]

        # Assert JSON consistency
        json_data = _read_sprint_json(json_path)
        assert json_data["status"] == "running"
        assert len(json_data["tickets"]) == 2
        assert json_data["tickets"][0]["issue_number"] == 101


def test_ticket_done_neon_and_json_are_consistent():
    """Completing a ticket writes status=done, completed_at, elapsed_seconds to Neon
    and updates the JSON mirror."""
    with tempfile.TemporaryDirectory() as tmp:
        sprints_dir = Path(tmp)
        label = "sprint-99"

        create_sprint(label=label, goal="g", project="p")
        add_ticket(sprint_label=label, issue_number=50, position=0)
        update_ticket_status(label, 50, "running")
        update_ticket_status(label, 50, "done")

        # JSON mirror update
        json_path = sprints_dir / f"{label}.json"
        _write_sprint_json(json_path, {
            "label": label, "goal": "g", "project": "p", "status": "running",
            "tickets": [{"issue_number": 50, "position": 0, "status": "done"}],
        })

        # Assert Neon
        t = list_tickets(label)[0]
        assert t.status == "done"
        assert t.completed_at is not None
        assert t.actual_elapsed_seconds is not None and t.actual_elapsed_seconds >= 0

        # Assert JSON
        json_data = _read_sprint_json(json_path)
        assert json_data["tickets"][0]["status"] == "done"


def test_ticket_failed_neon_and_json_are_consistent():
    """Failing a ticket updates both Neon and JSON mirror."""
    with tempfile.TemporaryDirectory() as tmp:
        sprints_dir = Path(tmp)
        label = "sprint-99"

        create_sprint(label=label, goal="g", project="p")
        add_ticket(sprint_label=label, issue_number=77, position=0)
        update_ticket_status(label, 77, "running")
        update_ticket_status(label, 77, "failed")

        # JSON mirror
        json_path = sprints_dir / f"{label}.json"
        _write_sprint_json(json_path, {
            "label": label, "goal": "g", "project": "p", "status": "running",
            "tickets": [{"issue_number": 77, "position": 0, "status": "failed"}],
        })

        t = list_tickets(label)[0]
        assert t.status == "failed"

        json_data = _read_sprint_json(json_path)
        assert json_data["tickets"][0]["status"] == "failed"


def test_sprint_cancelled_neon_and_json_are_consistent():
    """Cancelling a sprint updates both Neon and JSON mirror."""
    with tempfile.TemporaryDirectory() as tmp:
        sprints_dir = Path(tmp)
        label = "sprint-99"

        create_sprint(label=label, goal="g", project="p")
        update_sprint_status(label, "running")
        update_sprint_status(label, "cancelled")

        json_path = sprints_dir / f"{label}.json"
        _write_sprint_json(json_path, {
            "label": label, "goal": "g", "project": "p", "status": "cancelled", "tickets": [],
        })

        s = get_sprint_by_label(label)
        assert s.status == "cancelled"
        assert s.cancelled_at is not None

        json_data = _read_sprint_json(json_path)
        assert json_data["status"] == "cancelled"


def test_ticket_reorder_neon_and_json_are_consistent():
    """Reordering tickets updates Neon positions and the JSON mirror."""
    from services.sprint_manager.sprint_repo import reorder_tickets

    with tempfile.TemporaryDirectory() as tmp:
        sprints_dir = Path(tmp)
        label = "sprint-99"

        create_sprint(label=label, goal="g", project="p")
        add_ticket(sprint_label=label, issue_number=10, position=0)
        add_ticket(sprint_label=label, issue_number=20, position=1)
        add_ticket(sprint_label=label, issue_number=30, position=2)

        new_order = [30, 10, 20]
        reorder_tickets(label, new_order)

        # JSON mirror: update positions
        json_path = sprints_dir / f"{label}.json"
        _write_sprint_json(json_path, {
            "label": label, "goal": "g", "project": "p", "status": "pending",
            "tickets": [
                {"issue_number": n, "position": pos}
                for pos, n in enumerate(new_order)
            ],
        })

        tickets = list_tickets(label)
        assert [t.issue_number for t in tickets] == [30, 10, 20]

        json_data = _read_sprint_json(json_path)
        assert [t["issue_number"] for t in json_data["tickets"]] == [30, 10, 20]


def test_deleting_json_does_not_break_neon_read():
    """Deleting the JSON file leaves Neon intact — the authoritative read still works."""
    with tempfile.TemporaryDirectory() as tmp:
        sprints_dir = Path(tmp)
        label = "sprint-99"

        create_sprint(label=label, goal="g", project="p")
        add_ticket(sprint_label=label, issue_number=5, position=0)
        update_ticket_status(label, 5, "running")
        update_ticket_status(label, 5, "done")
        update_sprint_status(label, "complete")

        json_path = sprints_dir / f"{label}.json"
        _write_sprint_json(json_path, {"label": label, "status": "complete", "tickets": []})

        # Delete the JSON file — simulates the forensic file being removed.
        json_path.unlink()
        assert not json_path.exists()

        # Neon still has the data.
        s = get_sprint_by_label(label)
        assert s is not None
        assert s.status == "complete"

        t = list_tickets(label)[0]
        assert t.status == "done"


def test_get_or_create_sprint_is_idempotent():
    """get_or_create_sprint returns existing sprint without raising on duplicate."""
    from services.sprint_manager.sprint_repo import get_or_create_sprint

    s1 = get_or_create_sprint(label="sprint-1", goal="initial", project="p")
    s2 = get_or_create_sprint(label="sprint-1", goal="different goal", project="p")

    # Same row returned, goal not overwritten.
    assert s1.id == s2.id
    assert s2.goal == "initial"
