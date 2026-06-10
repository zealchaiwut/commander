"""add agent_runs table for per-agent duration tracking (issue #764)

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-06-10 00:00:00.000000

One row per dispatched agent (coder, tester, documenter, reviewer, estimator)
with its own start/finish timestamps and wall-clock duration. Uses only
portable column types (Integer / Text) so the migration applies cleanly on
SQLite as well as Postgres (issue #764 AC1).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "i9j0k1l2m3n4"
down_revision: Union[str, None] = "h8i9j0k1l2m3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("issue_number", sa.Integer(), nullable=False),
        sa.Column("sprint_label", sa.Text(), nullable=False),
        sa.Column("agent", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("finished_at", sa.Text(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_agent_runs_issue_agent", "agent_runs", ["issue_number", "agent"]
    )
    op.create_index("ix_agent_runs_sprint", "agent_runs", ["sprint_label"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_sprint", table_name="agent_runs")
    op.drop_index("ix_agent_runs_issue_agent", table_name="agent_runs")
    op.drop_table("agent_runs")
