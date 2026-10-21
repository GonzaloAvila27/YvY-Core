"""Station repository — read/insert operations on ``public.station``.

The repo is stateless: each method takes an ``AsyncSession`` explicitly,
so the same instance can serve concurrent requests with their own
transactions. The session lifecycle is the caller's responsibility.

All methods return / accept domain VOs (``Station``), never ORM rows.
The ``_orm_to_station`` helper does the one-way mapping.

Consumers:

- HMAC validation (application layer) calls ``get_by_uuid`` to verify
  that the envelope's ``station_uuid`` is known.
- CLI ``create-station`` calls ``insert`` to register a new IEA.
"""

from __future__ import annotations

from uuid import UUID as PyUUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yvy_server.domain.station import Station
from yvy_server.infrastructure.db import StationORM


class StationRepository:
    """Read/insert operations on ``public.station``."""

    async def get_by_uuid(
        self,
        session: AsyncSession,
        station_uuid: PyUUID,
    ) -> Station | None:
        """Return the ``Station`` with that UUID, or ``None`` if not found.

        Does NOT filter by ``deactivated_at`` — the caller decides what to
        do with a deactivated station (current policy: reject AUTH because
        the credential lookup will also fail; deactivated stations have
        all their credentials deactivated by CLI).
        """
        stmt = select(StationORM).where(StationORM.station_uuid == station_uuid)
        result = await session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _orm_to_station(orm) if orm is not None else None

    async def code_exists(self, session: AsyncSession, code: str) -> bool:
        """True si ya hay una estación con ese ``code`` (UNIQUE en la tabla)."""
        stmt = select(StationORM.station_uuid).where(StationORM.code == code).limit(1)
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def insert(self, session: AsyncSession, station: Station) -> None:
        """Insert a new station.

        Used by CLI ``create-station``. Doesn't return anything — the
        caller already has the ``Station`` VO with the UUID it generated.
        Server defaults populate ``created_at``; ``deactivated_at`` stays
        NULL by default.

        ``latitude``/``longitude`` (columnas post-M0) se copian de
        ``location_lat``/``location_lon``, igual que hace la 003 con las
        estaciones existentes. ``province`` usa el default de la tabla
        (``'Misiones'``) si no se informa.
        """
        orm = StationORM(
            station_uuid=station.station_uuid,
            name=station.name,
            description=station.description,
            location_lat=station.location_lat,
            location_lon=station.location_lon,
            location_name=station.location_name,
            code=station.code,
            locality=station.locality,
            latitude=station.location_lat,
            longitude=station.location_lon,
        )
        if station.province is not None:
            orm.province = station.province
        session.add(orm)


def _orm_to_station(orm: StationORM) -> Station:
    """Map ``StationORM`` to the domain ``Station`` VO."""
    return Station(
        station_uuid=orm.station_uuid,
        name=orm.name,
        description=orm.description,
        location_lat=orm.location_lat,
        location_lon=orm.location_lon,
        location_name=orm.location_name,
        created_at=orm.created_at,
        deactivated_at=orm.deactivated_at,
        code=orm.code,
        locality=orm.locality,
        province=orm.province,
    )
