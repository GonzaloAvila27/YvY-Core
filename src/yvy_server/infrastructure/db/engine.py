"""Async SQLAlchemy engine and session factory.

The engine is created from ``Settings.db_dsn`` (which we validated in
``config.py`` starts with ``postgresql+asyncpg://``). Pool size and
overflow come from settings too.

Pattern
-------

The recommended FastAPI lifecycle:

1. At startup (``lifespan``), call :func:`create_engine_and_session_factory`
   once. Store the engine and session factory on ``app.state``.
2. In handlers, get an ``AsyncSession`` from a dependency that uses the
   stored factory. Each request gets its own session, scoped to the
   request.
3. At shutdown, call :func:`dispose_engine` to gracefully close pooled
   connections.

For non-HTTP code (CLI commands, scripts, lifespan startup itself), use
the session factory directly::

    async with session_factory() as session:
        async with session.begin():
            ...
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

if TYPE_CHECKING:
    from yvy_server.config import Settings


# Type alias exposed so other modules don't need to import async_sessionmaker.
SessionFactory = async_sessionmaker[AsyncSession]


def create_engine_and_session_factory(
    settings: Settings,
) -> tuple[AsyncEngine, SessionFactory]:
    """Build the async engine and a session factory bound to it.

    Called once at app startup. The resulting engine maintains a pool;
    the session factory produces new ``AsyncSession`` instances on demand.

    Args:
        settings: Resolved ``Settings``. Reads ``db_dsn``, ``db_pool_size``
            and ``db_max_overflow``.

    Returns:
        ``(engine, session_factory)`` tuple. Callers typically store
        both on ``app.state`` for later use in dependencies and lifespan.
    """
    engine = create_async_engine(
        settings.db_dsn,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        # Disable SQLAlchemy's engine-level autocommit; we manage
        # transactions explicitly via ``session.begin()``.
        future=True,
        # Don't pre-ping every connection — overhead. asyncpg recycles
        # broken connections fairly well on its own. Revisit if we see
        # "server closed connection" errors in production.
        pool_pre_ping=False,
    )

    session_factory: SessionFactory = async_sessionmaker(
        bind=engine,
        # expire_on_commit=False keeps ORM instances usable after commit,
        # which matches how our repos return domain VOs (we read attrs
        # post-commit when mapping to VOs).
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )

    return engine, session_factory


async def dispose_engine(engine: AsyncEngine) -> None:
    """Gracefully close all pooled connections.

    Called on app shutdown. Idempotent.
    """
    await engine.dispose()


async def get_session(session_factory: SessionFactory) -> AsyncIterator[AsyncSession]:
    """Yield an ``AsyncSession`` scoped to one consumer.

    Used as a generator dependency in FastAPI handlers::

        async def session_dep(
            request: Request,
        ) -> AsyncIterator[AsyncSession]:
            factory = request.app.state.session_factory
            async for s in get_session(factory):
                yield s

        @router.post("/x")
        async def handler(
            session: Annotated[AsyncSession, Depends(session_dep)],
        ): ...

    The session commits/rolls back automatically when the consumer
    code uses ``async with session.begin():``. Otherwise the consumer
    is responsible for explicit ``await session.commit()`` /
    ``rollback()``.
    """
    async with session_factory() as session:
        try:
            yield session
        finally:
            # The async-context-manager already closes on exit; this is
            # just defensive. SQLAlchemy makes ``close`` idempotent.
            await session.close()
