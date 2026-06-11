"""add project_todos table for per-project to-do lists (issue #843)

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-06-11 00:00:00.000000

A lightweight, durable per-project scratchpad that lives outside the ticket
backlog. Deliberately not ticket-like — no labels, assignees, or due dates,
only free text, a done flag, and an ordering position. Uses only portable
column types (Integer / Text / Boolean) so the migration applies cleanly on
SQLite as well as Postgres (matching agent_runs, issue #764 AC1).

``promoted_issue_number`` is reserved for a future planning bridge and is
nullable; it is intentionally never read, written, or exposed in #843.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "k1l2m3n4o5p6"
down_revision: Union[str, None] = "j0k1l2m3n4o5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_todos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("promoted_issue_number", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_project_todos_project_position",
        "project_todos",
        ["project", "position"],
    )


def downgrade() -> None:
    op.drop_index("ix_project_todos_project_position", table_name="project_todos")
    op.drop_table("project_todos")
