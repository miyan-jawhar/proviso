"""
Database Engine
================
SQLAlchemy async engine and session management.
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import get_settings


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""
    pass


def get_engine(settings=None):
    if settings is None:
        settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=settings.debug,
        pool_pre_ping=True,
    )


def get_session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# Default instances
_engine = None
_session_factory = None


def init_db(settings=None):
    global _engine, _session_factory
    _engine = get_engine(settings)
    _session_factory = get_session_factory(_engine)


async def get_db():
    """FastAPI dependency: yields an async database session."""
    if _session_factory is None:
        init_db()
    async with _session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
