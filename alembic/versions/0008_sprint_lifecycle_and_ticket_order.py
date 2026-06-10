"""persist sprint lifecycle state and ticket order (issue #757)

Adds the durable lifecycle columns to `sprints` (state, ended_at, end_reason,
parent_label), extends the sprint status enum with `failed`, and creates the
`sprint_ticket_order` table.  This mirrors the SQLite authority schema
(apps/dashboard/db.py) into the optional Neon/Postgres layer.

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-06-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM, TIMESTAMP

revision: str = "h8i9j0k1l2m3"
down_revision: Union[str, None] = "g7h8i9j0k1l2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `failed` is a valid lifecycle state (no writer yet — reserved for the
    # future watchdog recovery sprint). ALTER TYPE ... ADD VALUE cannot run
    # inside a transaction block, so commit first then add with autocommit.
    op.execute("COMMIT")
    op.execute("ALTER TYPE sprint_status_enum ADD VALUE IF NOT EXISTS 'failed'")

    # New durable lifecycle columns on the existing sprints table.
    op.add_column(
        "sprints",
        sa.Column(
            "state",
            ENUM(
                "pending", "running", "complete", "cancelled", "failed",
                name="sprint_status_enum", create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column("sprints", sa.Column("ended_at", TIMESTAMP(timezone=True), nullable=True))
    op.add_column("sprints", sa.Column("end_reason", sa.Text(), nullable=True))
    op.add_column("sprints", sa.Column("parent_label", sa.Text(), nullable=True))

    op.create_table(
        "sprint_ticket_order",
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("issue", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("label", "issue", name="pk_sprint_ticket_order"),
    )
    op.create_index(
        "ix_sprint_ticket_order_label_position",
        "sprint_ticket_order",
        ["label", "position"],
    )


def downgrade() -> None:
    op.drop_index("ix_sprint_ticket_order_label_position", table_name="sprint_ticket_order")
    op.drop_table("sprint_ticket_order")

    op.drop_column("sprints", "parent_label")
    op.drop_column("sprints", "end_reason")
    op.drop_column("sprints", "ended_at")
    op.drop_column("sprints", "state")
    # Note: Postgres cannot drop an individual enum value; `failed` remains on
    # sprint_status_enum after downgrade. This is harmless and standard practice.
