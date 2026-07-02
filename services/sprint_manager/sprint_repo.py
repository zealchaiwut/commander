"""Neon-backed sprint/ticket CRUD — EXPORT-ONLY (issue #1695).

SQLite `sprints`/`sprint_ticket_order` (apps/dashboard/db.py) is the
authoritative runtime store for sprint lifecycle — see
docs/architecture/1_state-and-source-of-truth.md §1.4. This module has no
runtime caller; it exists solely for scripts/migrate_sprints_to_neon.py.
Do not import it from dashboard/server or sprint_manager runtime code
without first updating that doc and this docstring — see
tests/test_neon_export_only.py, which asserts the boundary.
"""
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


def rename_sprint(old_label: str, new_label: str) -> Sprint:
    """Rename a sprint by updating its label in the database."""
    with _open_session() as session:
        sprint = session.query(Sprint).filter_by(label=old_label).first()
        if sprint is None:
            raise SprintNotFound(f"Sprint {old_label!r} not found")
        sprint.label = new_label
        session.commit()
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
    sprint_label: str,
    issue_number: int,
    new_status: str,
    total_tokens: int = 0,
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
                elapsed = int((now - started).total_seconds())
                # Accumulate on re-close rather than overwrite so repeated
                # open→close cycles sum correctly.
                prior = ticket.actual_elapsed_seconds or 0
                ticket.actual_elapsed_seconds = prior + elapsed
            prior_tokens = ticket.total_tokens or 0
            ticket.total_tokens = prior_tokens + total_tokens
        session.commit()
        session.refresh(ticket)
        session.expunge(ticket)
        return ticket


def get_sprint_rollup(sprint_label: str) -> dict:
    """Return per-ticket metrics and sprint-level aggregates from the DB.

    Keys:
      tickets: list of {issue_number, actual_elapsed_seconds, total_tokens}
      sum_elapsed_seconds: int
      avg_elapsed_seconds: float | None
      sum_tokens: int
      avg_tokens: float | None
    """
    with _open_session() as session:
        sprint = session.query(Sprint).filter_by(label=sprint_label).first()
        if sprint is None:
            return {
                "tickets": [],
                "sum_elapsed_seconds": 0,
                "avg_elapsed_seconds": None,
                "sum_tokens": 0,
                "avg_tokens": None,
            }
        tickets = (
            session.query(SprintTicket)
            .filter_by(sprint_id=sprint.id)
            .order_by(SprintTicket.position.asc())
            .all()
        )
        rows = [
            {
                "issue_number": t.issue_number,
                "actual_elapsed_seconds": t.actual_elapsed_seconds,
                "total_tokens": t.total_tokens,
            }
            for t in tickets
        ]
        for t in tickets:
            session.expunge(t)

    elapsed_vals = [r["actual_elapsed_seconds"] for r in rows if r["actual_elapsed_seconds"] is not None]
    token_vals   = [r["total_tokens"] for r in rows if r["total_tokens"] is not None]
    return {
        "tickets": rows,
        "sum_elapsed_seconds": sum(elapsed_vals),
        "avg_elapsed_seconds": (sum(elapsed_vals) / len(elapsed_vals)) if elapsed_vals else None,
        "sum_tokens": sum(token_vals),
        "avg_tokens": (sum(token_vals) / len(token_vals)) if token_vals else None,
    }


def build_calibration_records(
    sprint_label: str,
    sizes: "dict[int, str]",
) -> "list[dict]":
    """Return calibration records [{size, actual_minutes, total_tokens}] for closed tickets.

    sizes: mapping of issue_number → size label (S/M/L/XL), from estimate files.
    Only tickets with actual_elapsed_seconds set are included.
    """
    rollup = get_sprint_rollup(sprint_label)
    records = []
    for row in rollup["tickets"]:
        num = row["issue_number"]
        elapsed = row["actual_elapsed_seconds"]
        if elapsed is None:
            continue
        size = sizes.get(num)
        if not size:
            continue
        rec: dict = {
            "size": size,
            "actual_minutes": round(elapsed / 60, 1),
        }
        if row["total_tokens"] is not None:
            rec["total_tokens"] = row["total_tokens"]
        records.append(rec)
    return records


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


def write_estimated_size(issue_number: int, size: str) -> None:
    """Write estimated_size to all sprint_tickets rows for *issue_number*.

    Updates every sprint that contains this issue. Best-effort: silently
    returns if no rows match.
    """
    with _open_session() as session:
        tickets = (
            session.query(SprintTicket)
            .filter_by(issue_number=issue_number)
            .all()
        )
        for ticket in tickets:
            ticket.estimated_size = size
        if tickets:
            session.commit()


def get_calibration_tickets(
    project: str,
    since_dt: Optional[datetime] = None,
    until_dt: Optional[datetime] = None,
    sprint_label: Optional[str] = None,
) -> List[dict]:
    """Return qualifying tickets for calibration analytics, scoped to a project.

    Includes only rows where estimated_size IS NOT NULL and
    actual_elapsed_seconds IS NOT NULL. Optionally filtered by
    completed_at date range and/or sprint label.

    Returns list of dicts with keys: issue_number, estimated_size,
    actual_elapsed_seconds, sprint_label.
    """
    from sqlalchemy import text as _text  # noqa: PLC0415

    params: dict = {"project": project}
    where_clauses = [
        "s.project = :project",
        "t.estimated_size IS NOT NULL",
        "t.actual_elapsed_seconds IS NOT NULL",
    ]

    if sprint_label is not None:
        where_clauses.append("s.label = :sprint_label")
        params["sprint_label"] = sprint_label

    if since_dt is not None:
        where_clauses.append("t.completed_at >= :since_dt")
        params["since_dt"] = since_dt.isoformat()

    if until_dt is not None:
        where_clauses.append("t.completed_at <= :until_dt")
        params["until_dt"] = until_dt.isoformat()

    where_sql = " AND ".join(where_clauses)
    sql = _text(
        f"SELECT t.issue_number, t.estimated_size, t.actual_elapsed_seconds, s.label"
        f" FROM sprint_tickets t"
        f" JOIN sprints s ON s.id = t.sprint_id"
        f" WHERE {where_sql}"
    )

    with _open_session() as session:
        rows = session.execute(sql, params).fetchall()

    return [
        {
            "issue_number": row[0],
            "estimated_size": row[1],
            "actual_elapsed_seconds": row[2],
            "sprint_label": row[3],
        }
        for row in rows
    ]


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
