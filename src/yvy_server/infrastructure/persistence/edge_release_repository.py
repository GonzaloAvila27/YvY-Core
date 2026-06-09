"""Edge release repository — reads and writes ``public.edge_releases`` and
``public.sync_state``.

Core responsibilities:

- ``get_latest_active``: returns the most recently published active
  release (the one edge nodes should download).
- ``publish_release``: inserts a new release row AND atomically
  increments ``sync_state.sync_version`` in the same transaction.
- ``get_current_sync_version``: reads the singleton sync_version for
  inclusion in ingest response headers and ``/edge/sync`` responses.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from yvy_server.infrastructure.db import EdgeReleaseORM, SyncStateORM


class EdgeReleaseRepository:
    """Stateless repository for edge releases and sync state."""

    # ─── Sync version ────────────────────────────────────────────────────

    async def get_current_sync_version(self, session: AsyncSession) -> int:
        """Return the current global sync_version (0 if table is empty)."""
        result = await session.execute(
            select(SyncStateORM.sync_version).where(SyncStateORM.id == 1)
        )
        row = result.scalar_one_or_none()
        return int(row) if row is not None else 0

    async def increment_sync_version(self, session: AsyncSession) -> int:
        """Atomically increment sync_version and return the new value.

        Must be called inside an open transaction. The caller is
        responsible for the transaction boundary.
        """
        result = await session.execute(
            update(SyncStateORM)
            .where(SyncStateORM.id == 1)
            .values(sync_version=SyncStateORM.sync_version + 1)
            .returning(SyncStateORM.sync_version)
        )
        new_version = result.scalar_one()
        return int(new_version)

    # ─── Releases ────────────────────────────────────────────────────────

    async def get_latest_active(
        self, session: AsyncSession
    ) -> EdgeReleaseORM | None:
        """Return the most recently released active release, or None."""
        result = await session.execute(
            select(EdgeReleaseORM)
            .where(EdgeReleaseORM.is_active.is_(True))
            .order_by(text("released_at DESC"))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def publish_release(
        self,
        session: AsyncSession,
        *,
        edge_version: str,
        package_filename: str,
        package_sha256: str,
        package_size_bytes: int,
        min_edge_version: str,
        notes: str | None,
        released_at: datetime,
    ) -> tuple[EdgeReleaseORM, int]:
        """Insert a new release and atomically bump the sync_version.

        Must be called inside an open transaction.

        Returns:
            (release_orm, new_sync_version)
        """
        new_sync = await self.increment_sync_version(session)

        release = EdgeReleaseORM(
            edge_version=edge_version,
            package_filename=package_filename,
            package_sha256=package_sha256,
            package_size_bytes=package_size_bytes,
            min_edge_version=min_edge_version,
            notes=notes,
            released_at=released_at,
            is_active=True,
            sync_version=new_sync,
        )
        session.add(release)
        await session.flush()  # populate .id before return
        return release, new_sync
