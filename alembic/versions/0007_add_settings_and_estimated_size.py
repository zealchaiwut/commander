"""add settings KV table and sprint_tickets.estimated_size

Revision ID: g7h8i9j0k1l2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-08 00:00:00.000000

Creates the settings key-value table for per-project config overrides and adds
the nullable estimated_size column to sprint_tickets for calibration tracking.
Seeds one global 'estimation' row with default size constants.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

revision: str = "g7h8i9j0k1l2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ESTIMATION_SEED = {
    "size_minutes": {"S": 5, "M": 15, "L": 30, "XL": 60},
    "buffer_pct": 20,
    "thin_ac_buffer_pct": 30,
}


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("project", sa.Text(), nullable=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope", "project", "key", name="uq_settings_scope_project_key"),
    )

    op.add_column(
        "sprint_tickets",
        sa.Column("estimated_size", sa.Text(), nullable=True),
    )

    bind = op.get_bind()
    import json
    bind.execute(
        sa.text(
            "INSERT INTO settings (scope, project, key, value)"
            " SELECT 'global', NULL, 'estimation', :val::jsonb"
            " WHERE NOT EXISTS ("
            "   SELECT 1 FROM settings WHERE scope='global' AND project IS NULL AND key='estimation'"
            " )"
        ),
        {"val": json.dumps(_ESTIMATION_SEED)},
    )


def downgrade() -> None:
    op.drop_column("sprint_tickets", "estimated_size")
    op.drop_table("settings")
