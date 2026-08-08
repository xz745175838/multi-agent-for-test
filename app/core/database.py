"""Async SQLAlchemy engine and session helpers."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


engine = create_async_engine(
    settings.async_database_url,
    echo=settings.debug,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    # item already in memory could be used afterwards, instead of expired after commiting,
    # usually set to False in async projects, not use true by default, avoiding query db for a simple return user
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async DB session; rollback on error, always close."""
    # each query call this line once, no shared session
    session = AsyncSessionLocal()
    try:
        yield session
        # inject session to router params and pause here, router keep running like query db and commit
    except Exception:
        await session.rollback()
        raise
    finally:
        # close session no matter succeed or failed, avoiding run out of connection pool
        # after runing this line, the connection returns to pool
        await session.close()
