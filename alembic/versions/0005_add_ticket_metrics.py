"""add actual_elapsed_seconds and total_tokens to sprint_tickets

Revision ID: e5f6a1b2c3d4
Revises: d4e5f6a1b2c3
Create Date: 2026-06-04 00:00:00.000000

Renames elapsed_seconds → actual_elapsed_seconds (preserving data) and adds
total_tokens INTEGER NULL.  Pre-existing rows get NULL for total_tokens.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e5f6a1b2c3d4"
down_revision: Union[str, None] = "d4e5f6a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "sprint_tickets",
        "elapsed_seconds",
        new_column_name="actual_elapsed_seconds",
    )
    op.add_column(
        "sprint_tickets",
        sa.Column("total_tokens", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sprint_tickets", "total_tokens")
    op.alter_column(
        "sprint_tickets",
        "actual_elapsed_seconds",
        new_column_name="elapsed_seconds",
    )
