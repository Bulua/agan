from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base compatible with SQLite and PostgreSQL."""


def _ensure_sqlite_dir(url: str) -> None:
    if url.startswith("sqlite"):
        settings.data_dir.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_dir(settings.database_url)

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    future=True,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db() -> None:
    """Create all registered ORM tables and apply lightweight SQLite column upgrades."""
    import app.models.agent  # noqa: F401 - register agent models
    import app.models.capability  # noqa: F401 - register tool models
    import app.models.chat  # noqa: F401 - register chat models on metadata
    import app.models.llm  # noqa: F401 - register models on metadata
    import app.models.mcp  # noqa: F401 - register mcp models on metadata
    import app.models.session  # noqa: F401 - register session models on metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if settings.database_url.startswith("sqlite"):
            await _ensure_sqlite_columns(conn)


async def _ensure_sqlite_columns(conn) -> None:
    """Idempotently add newly introduced columns on existing dev SQLite DBs."""
    migrations = [
        ("chat_conversations", "agent_id", "VARCHAR(36)"),
        ("chat_conversations", "agent_name_snapshot", "VARCHAR(200) NOT NULL DEFAULT ''"),
        ("chat_conversations", "compacted_summary", "TEXT NOT NULL DEFAULT ''"),
        ("chat_conversations", "compacted_before_message_id", "VARCHAR(36)"),
        ("chat_messages", "tool_calls_json", "JSON NOT NULL DEFAULT '[]'"),
        ("agents", "mcp_tool_filters", "JSON NOT NULL DEFAULT '{}'"),
        ("agents", "skill_names", "JSON NOT NULL DEFAULT '[]'"),
    ]
    for table, col, col_def in migrations:
        rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
        existing = {r[1] for r in rows}
        if rows and col not in existing:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}"))


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a scoped AsyncSession."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
