"""Value Objects for the public read layer.

All are frozen dataclasses — no business logic, just typed containers
that repositories return and use cases forward to the presentation layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class LastValues:
    """Snapshot of the most recent sensor reading for a station."""

    temp: Decimal | None
    humidity: int | None
    pressure: Decimal | None
    wind_speed: Decimal | None
    wind_gust: Decimal | None
    wind_direction: Decimal | None  # grados (convertido del índice de sector de pywws)
    timestamp: datetime | None


@dataclass(frozen=True, slots=True)
class StationSummary:
    """Public summary of a single station — feeds /public/stations."""

    station_uuid: UUID
    code: str | None
    name: str
    locality: str | None
    province: str
    latitude: Decimal | None
    longitude: Decimal | None
    active: bool
    status: str  # 'online' | 'sync_pending' | 'offline' | 'pending'
    last_sync_at: datetime | None
    last_values: LastValues | None


@dataclass(frozen=True, slots=True)
class NetworkStatus:
    """Aggregated network KPIs — feeds /public/network."""

    total_nodes: int
    active_nodes: int
    offline_nodes: int
    pending_nodes: int
    total_records: int
    localities_count: int
    last_sync_seconds: int | None
    uptime_30d: float
    generated_at: datetime
