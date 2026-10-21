"""PublicReadRepository — queries v_network_status and v_station_summary.

Read-only. No writes. Returns domain VOs, never ORM rows.

``v_station_summary.last_values`` trae los valores crudos del último payload
de pywws (pueden ser string, número o null). El cast seguro se hace acá
(``parse_last_values``): un valor que no parsea queda en ``None`` en lugar de
romper el endpoint.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from yvy_server.domain.public.models import LastValues, NetworkStatus, StationSummary

# pywws reporta la dirección del viento como índice de sector de 16 rumbos
# (0 = N, 4 = E, 8 = S, 12 = O). Grados = índice * 22.5.
_WIND_SECTORS = 16
_DEGREES_PER_SECTOR = Decimal("22.5")


def to_decimal(value: object) -> Decimal | None:
    """Cast seguro a ``Decimal``: acepta int, float o string numérico.

    Devuelve ``None`` para ``None``, booleanos, strings vacíos o no numéricos,
    NaN e infinitos.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value)) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            parsed = Decimal(value.strip())
        except InvalidOperation:
            return None
        return parsed if parsed.is_finite() else None
    return None


def to_int(value: object) -> int | None:
    """Cast seguro a ``int`` (redondea ``"46.0"`` → 46)."""
    parsed = to_decimal(value)
    return int(parsed.to_integral_value()) if parsed is not None else None


def wind_sector_to_degrees(value: object) -> Decimal | None:
    """Convierte el índice de sector de pywws (0-15) a grados (0-337.5).

    Un índice fuera de rango o no entero devuelve ``None``.
    """
    parsed = to_decimal(value)
    if parsed is None or parsed != parsed.to_integral_value():
        return None
    index = int(parsed)
    if not 0 <= index < _WIND_SECTORS:
        return None
    return index * _DEGREES_PER_SECTOR


def parse_last_values(raw: Mapping[str, Any] | None) -> LastValues | None:
    """Arma ``LastValues`` desde el JSON ``last_values`` de la vista."""
    if raw is None:
        return None
    return LastValues(
        temp=to_decimal(raw.get("temp")),
        humidity=to_int(raw.get("humidity")),
        pressure=to_decimal(raw.get("pressure")),
        wind_speed=to_decimal(raw.get("windSpeed")),
        wind_gust=to_decimal(raw.get("windGust")),
        wind_direction=wind_sector_to_degrees(raw.get("windDirIndex")),
        timestamp=raw.get("timestamp"),
    )


class PublicReadRepository:
    """Async repository for the public aggregation layer (C10)."""

    async def get_network_status(self, session: AsyncSession) -> NetworkStatus:
        row = (await session.execute(text("SELECT * FROM v_network_status"))).mappings().one()
        return NetworkStatus(
            total_nodes=row["total_nodes"] or 0,
            active_nodes=row["active_nodes"] or 0,
            offline_nodes=row["offline_nodes"] or 0,
            pending_nodes=row["pending_nodes"] or 0,
            total_records=row["total_records"] or 0,
            localities_count=row["localities_count"] or 0,
            last_sync_seconds=row["last_sync_seconds"],
            uptime_30d=float(row["uptime_30d"] or 0),
            generated_at=row["generated_at"] or datetime.now(UTC),
        )

    async def get_station_summaries(self, session: AsyncSession) -> list[StationSummary]:
        rows = (
            (await session.execute(text("SELECT * FROM v_station_summary ORDER BY code")))
            .mappings()
            .all()
        )
        return [row_to_summary(r) for r in rows]

    async def get_station_by_uuid(
        self, session: AsyncSession, station_uuid: UUID
    ) -> StationSummary | None:
        row = (
            (
                await session.execute(
                    text("SELECT * FROM v_station_summary WHERE station_uuid = :uuid"),
                    {"uuid": station_uuid},
                )
            )
            .mappings()
            .one_or_none()
        )
        return row_to_summary(row) if row else None

    async def get_station_by_code(self, session: AsyncSession, code: str) -> StationSummary | None:
        row = (
            (
                await session.execute(
                    text("SELECT * FROM v_station_summary WHERE code = :code"),
                    {"code": code},
                )
            )
            .mappings()
            .one_or_none()
        )
        return row_to_summary(row) if row else None


def row_to_summary(row: Mapping[Any, Any]) -> StationSummary:
    """Mapea una fila de ``v_station_summary`` al VO ``StationSummary``."""
    return StationSummary(
        station_uuid=row["station_uuid"],
        code=row["code"],
        name=row["name"],
        locality=row["locality"],
        province=row["province"] or "Misiones",
        latitude=to_decimal(row["latitude"]),
        longitude=to_decimal(row["longitude"]),
        active=bool(row["active"]),
        status=row["status"],
        last_sync_at=row["last_sync_at"],
        last_values=parse_last_values(row["last_values"]),
    )
