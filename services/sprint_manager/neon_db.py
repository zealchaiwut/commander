"""Neon engine/session plumbing — shared by a runtime path and an export-only one (issue #1695).

apps/dashboard/settings_repo.py imports this module for the settings KV
fallback when Neon is enabled (COMMANDER_DISABLE_NEON=0) — that IS a
legitimate runtime dependency; do not treat this whole module as export-only.
The sprint/project mirror path (sprint_repo.py, sync_projects_to_neon.py)
also depends on it but has no runtime caller of its own — see those modules'
docstrings and docs/architecture/1_state-and-source-of-truth.md §1.4.
"""
import os
from sqlalchemy import create_engine, Engine
from sqlalchemy.orm import sessionmaker, Session, DeclarativeBase


class Base(DeclarativeBase):
    pass


# Module-level engine cache (issue #758). Neon is an optional export target,
# not a live dependency — but when it IS used (the export script, settings sync),
# the engine is created once and reused rather than per call.
_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is not None:
        return _engine
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. "
            "Create a Neon project at https://neon.tech, copy the connection string, "
            "and set it as DATABASE_URL in your .env file."
        )
    _engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)
    return _engine


def get_session() -> Session:
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()
