from sqlalchemy import (
    Column,
    Integer,
    Text,
    ForeignKey,
    UniqueConstraint,
    Index,
    JSON,
)
from sqlalchemy.dialects.postgresql import ENUM, TIMESTAMP

from services.sprint_manager.neon_db import Base


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    repo = Column(Text, unique=True, nullable=False)
    name = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")


class ProjectEnvironment(Base):
    __tablename__ = "project_environments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    env = Column(Text, nullable=False)
    local_directory = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")

    __table_args__ = (
        UniqueConstraint("project_id", "env", name="uq_project_environments_project_env"),
        Index("ix_project_environments_project_id", "project_id"),
    )

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
    actual_elapsed_seconds = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    estimated_size = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("sprint_id", "issue_number", name="uq_sprint_tickets_sprint_issue"),
        Index("ix_sprint_tickets_sprint_position", "sprint_id", "position"),
    )


class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scope = Column(Text, nullable=False)
    project = Column(Text, nullable=True)
    key = Column(Text, nullable=False)
    value = Column(JSON, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default="now()")

    __table_args__ = (
        UniqueConstraint("scope", "project", "key", name="uq_settings_scope_project_key"),
    )
