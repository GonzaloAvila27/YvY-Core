from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Station:
    station_uuid: UUID
    name: str
    description: str | None
    location_lat: float | None
    location_lon: float | None
    location_name: str | None
    created_at: datetime | None = None
    deactivated_at: datetime | None = None
