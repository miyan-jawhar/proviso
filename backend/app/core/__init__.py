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


settings = get_settings()

# Async engine for FastAPI routes
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
)

# Session factory
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db():
    """FastAPI dependency: yields an async database session."""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
