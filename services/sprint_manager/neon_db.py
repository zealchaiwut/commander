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
