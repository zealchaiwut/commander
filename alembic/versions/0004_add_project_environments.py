"""add project_environments table

Revision ID: d4e5f6a1b2c3
Revises: c3d4e5f6a1b2
Create Date: 2026-05-29 00:00:00.000000

downgrade removes ONLY project_environments; the projects table is untouched.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision: str = "d4e5f6a1b2c3"
down_revision: Union[str, None] = "c3d4e5f6a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROJECTS_JSON = (
    Path(__file__).parent.parent.parent / "apps" / "dashboard" / "projects.json"
)


def upgrade() -> None:
    op.create_table(
        "project_environments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("env", sa.Text(), nullable=False),
        sa.Column("local_directory", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "env",
            name="uq_project_environments_project_env",
        ),
    )
    op.create_index(
        "ix_project_environments_project_id",
        "project_environments",
        ["project_id"],
    )

    bind = op.get_bind()
    if _PROJECTS_JSON.exists():
        try:
            projects = json.loads(_PROJECTS_JSON.read_text())
        except (json.JSONDecodeError, OSError):
            projects = []
        for proj in projects:
            repo = (proj.get("repo") or "").strip()
            if not repo:
                continue
            environments = proj.get("environments") or {}
            if not environments:
                continue
            pid_row = bind.execute(
                sa.text("SELECT id FROM projects WHERE repo = :repo"),
                {"repo": repo},
            ).fetchone()
            if not pid_row:
                continue
            project_id = pid_row[0]
            for env, local_dir in environments.items():
                if not env or not local_dir:
                    continue
                bind.execute(
                    sa.text(
                        "INSERT INTO project_environments"
                        " (project_id, env, local_directory)"
                        " VALUES (:project_id, :env, :local_directory)"
                        " ON CONFLICT (project_id, env) DO NOTHING"
                    ),
                    {"project_id": project_id, "env": env, "local_directory": local_dir},
                )


def downgrade() -> None:
    op.drop_index(
        "ix_project_environments_project_id",
        table_name="project_environments",
    )
    op.drop_table("project_environments")
