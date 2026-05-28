"""Credential repository — read/insert on ``public.station_credentials``.

Consumers:

- HMAC validation calls ``get_active_by_station_and_version`` to fetch
  the key bytes for signature verification. If the lookup returns
  ``None`` (no row, or row is deactivated), the application raises
  ``CredentialNotFoundError`` → 401 AUTH.
- CLI ``create-station`` calls ``insert`` to register the initial
  ``key_version=1`` credential.
- (Future) CLI ``rotate-key`` will call ``insert`` for ``key_version=N+1``
  and a separate ``deactivate(...)`` for the old one (out of scope for 9.1).
"""
from __future__ import annotations

from uuid import UUID as PyUUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yvy_server.domain.station import StationCredential
from yvy_server.infrastructure.db import StationCredentialORM


class CredentialRepository:
    """Read/insert operations on ``public.station_credentials``."""

    async def get_active_by_station_and_version(
        self,
        session: AsyncSession,
        station_uuid: PyUUID,
        key_version: int,
    ) -> StationCredential | None:
        """Return the active credential row for this station+version.

        "Active" means ``deactivated_at IS NULL``. Returns ``None`` if
        no row matches, OR if the row exists but is deactivated.

        The unique constraint ``uq_station_credentials_station_uuid_key_version``
        guarantees at most one row matches, so we use ``scalar_one_or_none()``.
        """
        stmt = (
            select(StationCredentialORM)
            .where(
                StationCredentialORM.station_uuid == station_uuid,
                StationCredentialORM.key_version == key_version,
                StationCredentialORM.deactivated_at.is_(None),
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _orm_to_credential(orm) if orm is not None else None

    async def insert(
        self,
        session: AsyncSession,
        credential: StationCredential,
    ) -> None:
        """Insert a new credential row.

        ``credential.credential_id`` is ignored on insert (the DB assigns
        a BIGSERIAL value). ``created_at`` uses the server default.
        """
        session.add(
            StationCredentialORM(
                station_uuid=credential.station_uuid,
                key_version=credential.key_version,
                key_hmac_hex=credential.key_hmac_hex,
                reason=credential.reason,
            )
        )


def _orm_to_credential(orm: StationCredentialORM) -> StationCredential:
    """Map ``StationCredentialORM`` to the domain ``StationCredential`` VO."""
    return StationCredential(
        credential_id=orm.credential_id,
        station_uuid=orm.station_uuid,
        key_version=orm.key_version,
        key_hmac_hex=orm.key_hmac_hex,
        created_at=orm.created_at,
        deactivated_at=orm.deactivated_at,
        reason=orm.reason,
    )
