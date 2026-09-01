"""Engine / session plumbing and the bootstrap DDL for required extensions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from repo_troubleshooter.config import get_settings

REQUIRED_EXTENSIONS = ("pg_trgm", "vector")


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True, future=True)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ensure_extensions(engine: Engine | None = None) -> list[str]:
    """Create the extensions the retrieval layer will need. Idempotent."""
    engine = engine or get_engine()
    created: list[str] = []
    with engine.begin() as conn:
        for ext in REQUIRED_EXTENSIONS:
            conn.execute(text(f'CREATE EXTENSION IF NOT EXISTS "{ext}"'))
            created.append(ext)
    return created


def ping() -> str:
    with get_engine().connect() as conn:
        return conn.execute(text("SELECT version()")).scalar_one()
