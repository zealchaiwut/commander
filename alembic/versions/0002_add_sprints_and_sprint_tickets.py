"""add sprints and sprint_tickets

Revision ID: b2c3d4e5f6a1
Revises: a1b2c3d4e5f6
Create Date: 2026-05-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM, TIMESTAMP

revision: str = "b2c3d4e5f6a1"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

sprint_status_enum = ENUM(
    "pending", "running", "complete", "cancelled",
    name="sprint_status_enum",
)

sprint_ticket_status_enum = ENUM(
    "pending", "running", "done", "failed", "skipped",
    name="sprint_ticket_status_enum",
)


def upgrade() -> None:
    sprint_status_enum.create(op.get_bind(), checkfirst=True)
    sprint_ticket_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "sprints",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column(
            "status",
            ENUM("pending", "running", "complete", "cancelled", name="sprint_status_enum", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("cancelled_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("project", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("label", name="uq_sprints_label"),
    )

    op.create_table(
        "sprint_tickets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sprint_id", sa.Integer(), nullable=False),
        sa.Column("issue_number", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            ENUM("pending", "running", "done", "failed", "skipped", name="sprint_ticket_status_enum", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("started_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("agent_active", sa.Text(), nullable=True),
        sa.Column("elapsed_seconds", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["sprint_id"], ["sprints.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sprint_id", "issue_number", name="uq_sprint_tickets_sprint_issue"),
    )

    op.create_index(
        "ix_sprint_tickets_sprint_position",
        "sprint_tickets",
        ["sprint_id", "position"],
    )


def downgrade() -> None:
    op.drop_index("ix_sprint_tickets_sprint_position", table_name="sprint_tickets")
    op.drop_table("sprint_tickets")
    op.drop_table("sprints")

    sprint_ticket_status_enum.drop(op.get_bind(), checkfirst=True)
    sprint_status_enum.drop(op.get_bind(), checkfirst=True)
