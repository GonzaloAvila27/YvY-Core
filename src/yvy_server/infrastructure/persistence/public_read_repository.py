"""PublicReadRepository — queries v_network_status and v_station_summary.

Read-only. No writes. Returns domain VOs, never ORM rows.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from yvy_server.domain.public.models import LastValues, NetworkStatus, StationSummary


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
            generated_at=row["generated_at"] or datetime.now(timezone.utc),
        )

    async def get_station_summaries(self, session: AsyncSession) -> list[StationSummary]:
        rows = (
            await session.execute(text("SELECT * FROM v_station_summary ORDER BY code"))
        ).mappings().all()
        return [self._row_to_summary(r) for r in rows]

    async def get_station_by_uuid(
        self, session: AsyncSession, station_uuid: UUID
    ) -> StationSummary | None:
        row = (
            await session.execute(
                text("SELECT * FROM v_station_summary WHERE station_uuid = :uuid"),
                {"uuid": station_uuid},
            )
        ).mappings().one_or_none()
        return self._row_to_summary(row) if row else None

    async def get_station_by_code(
        self, session: AsyncSession, code: str
    ) -> StationSummary | None:
        row = (
            await session.execute(
                text("SELECT * FROM v_station_summary WHERE code = :code"),
                {"code": code},
            )
        ).mappings().one_or_none()
        return self._row_to_summary(row) if row else None

    # ─── private ──────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_summary(row: object) -> StationSummary:
        lv_raw = row["last_values"]  # type: ignore[index]
        last_values: LastValues | None = None
        if lv_raw is not None:
            last_values = LastValues(
                temp=Decimal(str(lv_raw["temp"])) if lv_raw.get("temp") is not None else None,
                humidity=int(lv_raw["humidity"]) if lv_raw.get("humidity") is not None else None,
                pressure=Decimal(str(lv_raw["pressure"])) if lv_raw.get("pressure") is not None else None,
                wind_speed=Decimal(str(lv_raw["windSpeed"])) if lv_raw.get("windSpeed") is not None else None,
                wind_gust=Decimal(str(lv_raw["windGust"])) if lv_raw.get("windGust") is not None else None,
                wind_direction=Decimal(str(lv_raw["windDirection"])) if lv_raw.get("windDirection") is not None else None,
                timestamp=lv_raw.get("timestamp"),
            )
        return StationSummary(
            station_uuid=row["station_uuid"],  # type: ignore[index]
            code=row["code"],  # type: ignore[index]
            name=row["name"],  # type: ignore[index]
            locality=row["locality"],  # type: ignore[index]
            province=row["province"] or "Misiones",  # type: ignore[index]
            latitude=Decimal(str(row["latitude"])) if row["latitude"] is not None else None,  # type: ignore[index]
            longitude=Decimal(str(row["longitude"])) if row["longitude"] is not None else None,  # type: ignore[index]
            active=bool(row["active"]),  # type: ignore[index]
            status=row["status"],  # type: ignore[index]
            last_sync_at=row["last_sync_at"],  # type: ignore[index]
            last_values=last_values,
        )
