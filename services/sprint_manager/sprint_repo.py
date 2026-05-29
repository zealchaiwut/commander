from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from services.sprint_manager.models import Sprint, SprintTicket
from services.sprint_manager.neon_db import get_session as _neon_get_session


class SprintNotFound(Exception):
    pass


class TicketNotFound(Exception):
    pass


# Tests can replace this with a sessionmaker bound to a test engine.
_session_factory = None


def _open_session():
    if _session_factory is not None:
        return _session_factory()
    return _neon_get_session()


def get_sprint_by_label(label: str) -> Optional[Sprint]:
    with _open_session() as session:
        sprint = session.query(Sprint).filter_by(label=label).first()
        if sprint is not None:
            session.expunge(sprint)
        return sprint


def list_sprints(project: Optional[str] = None) -> List[Sprint]:
    with _open_session() as session:
        q = session.query(Sprint)
        if project:
            q = q.filter_by(project=project)
        sprints = q.all()
        for s in sprints:
            session.expunge(s)
        return sprints


def get_or_create_sprint(label: str, goal: str, project: str) -> Sprint:
    """Return existing sprint or create a new one — idempotent."""
    existing = get_sprint_by_label(label)
    if existing is not None:
        return existing
    return create_sprint(label=label, goal=goal, project=project)


def create_sprint(label: str, goal: str, project: str) -> Sprint:
    with _open_session() as session:
        sprint = Sprint(label=label, goal=goal, project=project)
        session.add(sprint)
        session.commit()
        session.refresh(sprint)
        session.expunge(sprint)
        return sprint


def update_sprint_status(label: str, new_status: str) -> Sprint:
    with _open_session() as session:
        sprint = session.query(Sprint).filter_by(label=label).first()
        if sprint is None:
            raise SprintNotFound(f"Sprint {label!r} not found")
        now = datetime.now(timezone.utc)
        sprint.status = new_status
        if new_status == "running":
            sprint.started_at = now
        elif new_status == "complete":
            sprint.completed_at = now
        elif new_status == "cancelled":
            sprint.cancelled_at = now
        session.commit()
        session.expunge(sprint)
        return sprint


def add_ticket(sprint_label: str, issue_number: int, position: int) -> SprintTicket:
    with _open_session() as session:
        sprint = session.query(Sprint).filter_by(label=sprint_label).first()
        if sprint is None:
            raise SprintNotFound(f"Sprint {sprint_label!r} not found")
        ticket = SprintTicket(
            sprint_id=sprint.id,
            issue_number=issue_number,
            position=position,
        )
        session.add(ticket)
        session.commit()
        session.refresh(ticket)
        session.expunge(ticket)
        return ticket


def update_ticket_status(
    sprint_label: str, issue_number: int, new_status: str
) -> SprintTicket:
    with _open_session() as session:
        sprint = session.query(Sprint).filter_by(label=sprint_label).first()
        if sprint is None:
            raise SprintNotFound(f"Sprint {sprint_label!r} not found")
        ticket = (
            session.query(SprintTicket)
            .filter_by(sprint_id=sprint.id, issue_number=issue_number)
            .first()
        )
        if ticket is None:
            raise TicketNotFound(
                f"Issue #{issue_number} not found in sprint {sprint_label!r}"
            )
        now = datetime.now(timezone.utc)
        ticket.status = new_status
        if new_status == "running":
            ticket.started_at = now
        elif new_status in ("done", "failed", "skipped"):
            ticket.completed_at = now
            if ticket.started_at is not None:
                started = ticket.started_at
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                ticket.elapsed_seconds = int((now - started).total_seconds())
        session.commit()
        session.expunge(ticket)
        return ticket


def list_tickets(sprint_label: str) -> List[SprintTicket]:
    with _open_session() as session:
        sprint = session.query(Sprint).filter_by(label=sprint_label).first()
        if sprint is None:
            raise SprintNotFound(f"Sprint {sprint_label!r} not found")
        tickets = (
            session.query(SprintTicket)
            .filter_by(sprint_id=sprint.id)
            .order_by(SprintTicket.position.asc())
            .all()
        )
        for t in tickets:
            session.expunge(t)
        return tickets


def reorder_tickets(sprint_label: str, issue_numbers: List[int]) -> None:
    with _open_session() as session:
        sprint = session.query(Sprint).filter_by(label=sprint_label).first()
        if sprint is None:
            raise SprintNotFound(f"Sprint {sprint_label!r} not found")
        for position, issue_number in enumerate(issue_numbers):
            ticket = (
                session.query(SprintTicket)
                .filter_by(sprint_id=sprint.id, issue_number=issue_number)
                .first()
            )
            if ticket is not None:
                ticket.position = position
        session.commit()
