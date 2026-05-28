from sqlalchemy import (
    Column,
    Integer,
    Text,
    ForeignKey,
    UniqueConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import ENUM, TIMESTAMP

from services.sprint_manager.neon_db import Base

sprint_status_enum = ENUM(
    "pending", "running", "complete", "cancelled",
    name="sprint_status_enum",
)

sprint_ticket_status_enum = ENUM(
    "pending", "running", "done", "failed", "skipped",
    name="sprint_ticket_status_enum",
)


class Sprint(Base):
    __tablename__ = "sprints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    label = Column(Text, unique=True, nullable=False)
    goal = Column(Text, nullable=False)
    status = Column(sprint_status_enum, nullable=False, server_default="pending")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")
    started_at = Column(TIMESTAMP(timezone=True), nullable=True)
    completed_at = Column(TIMESTAMP(timezone=True), nullable=True)
    cancelled_at = Column(TIMESTAMP(timezone=True), nullable=True)
    project = Column(Text, nullable=False)


class SprintTicket(Base):
    __tablename__ = "sprint_tickets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sprint_id = Column(
        Integer,
        ForeignKey("sprints.id", ondelete="CASCADE"),
        nullable=False,
    )
    issue_number = Column(Integer, nullable=False)
    position = Column(Integer, nullable=False)
    status = Column(sprint_ticket_status_enum, nullable=False, server_default="pending")
    started_at = Column(TIMESTAMP(timezone=True), nullable=True)
    completed_at = Column(TIMESTAMP(timezone=True), nullable=True)
    agent_active = Column(Text, nullable=True)
    elapsed_seconds = Column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("sprint_id", "issue_number", name="uq_sprint_tickets_sprint_issue"),
        Index("ix_sprint_tickets_sprint_position", "sprint_id", "position"),
    )
