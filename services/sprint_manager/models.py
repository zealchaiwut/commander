"""Neon ORM models — mixed export-only and runtime-shared (issue #1695).

`Setting` and `ProjectTodo` back the runtime settings/todo KV fallback used
by apps/dashboard/{settings_repo,todo_repo}.py when Neon is enabled — those
classes ARE reachable from dashboard runtime and this module is a legitimate
shared dependency of that path.

`Project`, `ProjectEnvironment`, `Sprint`, `SprintTicket`, and `AgentRun` back
the export-only sprint/project mirror (services/sprint_manager/sprint_repo.py,
sync_projects_to_neon.py) — SQLite is the authoritative runtime store for
that data (docs/architecture/1_state-and-source-of-truth.md §1.4). Do not add
a dashboard/server runtime caller for those specific classes without updating
that doc; see tests/test_neon_export_only.py.
"""
from sqlalchemy import (
    Boolean,
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


class AgentRun(Base):
    __tablename__ = "agent_runs"

    # Portable column types only (Integer / Text) so the table and its Alembic
    # migration apply cleanly on SQLite as well as Postgres (issue #764).
    # Timestamps are stored as ISO-8601 UTC strings, matching the local SQLite
    # dashboard store that records these rows at dispatch time.
    id = Column(Integer, primary_key=True, autoincrement=True)
    issue_number = Column(Integer, nullable=False)
    sprint_label = Column(Text, nullable=False)
    agent = Column(Text, nullable=False)
    started_at = Column(Text, nullable=False)
    finished_at = Column(Text, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    outcome = Column(Text, nullable=True)
    total_tokens = Column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_agent_runs_issue_agent", "issue_number", "agent"),
        Index("ix_agent_runs_sprint", "sprint_label"),
    )


class ProjectTodo(Base):
    """A lightweight, durable per-project to-do item (issue #843).

    A simple scratchpad scoped to each project, deliberately *not* ticket-like:
    no labels, assignees, or due dates — only free text, a done flag, and an
    ordering position. Portable column types only (Integer / Text / Boolean,
    ISO-8601 string timestamps) so the table and its migration apply cleanly on
    SQLite as well as Postgres, matching ``AgentRun``.

    ``promoted_issue_number`` is reserved for a future planning bridge that will
    let a todo graduate into a real GitHub issue. It exists and is nullable but
    is intentionally never read, written, or exposed by any endpoint in #843.
    """

    __tablename__ = "project_todos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project = Column(Text, nullable=False)
    text = Column(Text, nullable=False)
    done = Column(Boolean, nullable=False, default=False)
    position = Column(Integer, nullable=False)
    created_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)
    # Reserved for a future planning bridge — unused in #843.
    promoted_issue_number = Column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_project_todos_project_position", "project", "position"),
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
