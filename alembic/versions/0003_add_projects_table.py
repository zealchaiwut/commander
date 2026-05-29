"""add projects table

Revision ID: c3d4e5f6a1b2
Revises: b2c3d4e5f6a1
Create Date: 2026-05-29 00:00:00.000000

"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision: str = "c3d4e5f6a1b2"
down_revision: Union[str, None] = "b2c3d4e5f6a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROJECTS_JSON = (
    Path(__file__).parent.parent.parent / "apps" / "dashboard" / "projects.json"
)


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("repo", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repo", name="uq_projects_repo"),
    )

    bind = op.get_bind()
    if _PROJECTS_JSON.exists():
        try:
            projects = json.loads(_PROJECTS_JSON.read_text())
        except Exception:
            projects = []
        for proj in projects:
            repo = (proj.get("repo") or "").strip()
            if not repo:
                continue
            name = (proj.get("name") or repo.split("/")[-1]).strip()
            bind.execute(
                sa.text(
                    "INSERT INTO projects (repo, name) VALUES (:repo, :name)"
                    " ON CONFLICT (repo) DO NOTHING"
                ),
                {"repo": repo, "name": name},
            )


def downgrade() -> None:
    op.drop_table("projects")
