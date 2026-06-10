"""add risk_tier and model_used to agent_runs (issue #790)

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
Create Date: 2026-06-11 00:00:00.000000

Adds two nullable TEXT columns to agent_runs so each tester invocation can
record the pre-dispatch risk classification and the model that was selected.
Uses only portable column types (Integer / Text) so the migration applies
cleanly on SQLite as well as Postgres.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "j0k1l2m3n4o5"
down_revision: Union[str, None] = "i9j0k1l2m3n4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("risk_tier", sa.Text(), nullable=True))
    op.add_column("agent_runs", sa.Column("model_used", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "model_used")
    op.drop_column("agent_runs", "risk_tier")
