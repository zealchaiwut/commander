import os
from sqlalchemy import create_engine, Engine
from sqlalchemy.orm import sessionmaker, Session, DeclarativeBase


class Base(DeclarativeBase):
    pass


def get_engine() -> Engine:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. "
            "Create a Neon project at https://neon.tech, copy the connection string, "
            "and set it as DATABASE_URL in your .env file."
        )
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def get_session() -> Session:
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()
