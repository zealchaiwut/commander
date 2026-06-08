"""add events table for structured log events

Revision ID: f6a7b8c9d0e1
Revises: e5f6a1b2c3d4
Create Date: 2026-06-08 00:00:00.000000

Creates the events table used by the Logs screen data layer.
All types are compatible with both SQLite and PostgreSQL.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("action_id", sa.Text(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("source IN ('agent', 'dashboard', 'github')", name="ck_events_source"),
    )
    op.create_index("ix_events_project_ts", "events", ["project", sa.text("timestamp DESC")])
    op.create_index("ix_events_project_target", "events", ["project", "target"])
    op.create_index("ix_events_action_id", "events", ["action_id"])


def downgrade() -> None:
    op.drop_index("ix_events_action_id", table_name="events")
    op.drop_index("ix_events_project_target", table_name="events")
    op.drop_index("ix_events_project_ts", table_name="events")
    op.drop_table("events")
