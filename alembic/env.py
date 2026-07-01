"""Alembic environment configuration for YVY Server.

Uses the async SQLAlchemy engine (asyncpg driver), reading the DSN from
``yvy_server.config.Settings`` so there's one source of truth.

The standard offline/online dichotomy of Alembic is preserved:

- **Offline mode** (``alembic upgrade --sql``) emits SQL without
  connecting to a DB. Useful for review/audit.
- **Online mode** (``alembic upgrade head``) connects to the DB via
  asyncpg.

A shared ``naming_convention`` on ``MetaData`` produces predictable
names for indexes, constraints, and foreign keys, which makes downgrades
and diffs robust.
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import MetaData, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import settings lazily to give a clear error if env is misconfigured.
from yvy_server.config import get_settings

# ─── Alembic config setup ──────────────────────────────────────────────

config = context.config

# Inject DSN from settings, overriding the placeholder in alembic.ini.
_settings = get_settings()
config.set_main_option("sqlalchemy.url", _settings.db_dsn)

# Configure logging from alembic.ini (CLI-friendly human format).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# ─── MetaData naming convention ────────────────────────────────────────
# Activated in step 5: ``Base.metadata`` is now the source of truth for
# autogenerate. The naming convention here lives on ``Base.metadata``
# (set in ``infrastructure/db/models.py``); we don't need a separate
# declaration here.
from yvy_server.infrastructure.db import Base

target_metadata: MetaData | None = Base.metadata


# ─── Migration runners ─────────────────────────────────────────────────


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Configures the context with just a URL — no DB connection. SQL is
    emitted to stdout instead of executed.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Include schemas so 'bronze' is recognized by autogenerate later.
        include_schemas=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    """Sync wrapper invoked by ``connection.run_sync``."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    """Run migrations in online mode with an async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online migrations.

    Wraps the async runner in ``asyncio.run`` because Alembic itself
    is sync at the top level.
    """
    asyncio.run(_run_async_migrations())


# ─── Dispatch ──────────────────────────────────────────────────────────

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
