"""Pydantic schemas for the public read API (/api/v1/public/*).

Separate from schemas.py (ingest) to keep each surface area small.
Shape matches the DATA-CONTRACT.md spec (Apéndice A of the plan).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class LastValuesSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    temp: Decimal | None = None
    humidity: int | None = None
    pressure: Decimal | None = None
    windSpeed: Decimal | None = None
    windGust: Decimal | None = None
    windDirection: Decimal | None = None
    timestamp: datetime | None = None


class CoordinatesSchema(BaseModel):
    lat: Decimal
    lng: Decimal


class StationSchema(BaseModel):
    id: UUID
    code: str
    name: str
    locality: str | None
    province: str
    coordinates: CoordinatesSchema | None
    status: str
    lastSyncAt: datetime | None
    lastValues: LastValuesSchema | None


class NetworkStatusResponse(BaseModel):
    totalNodes: int
    activeNodes: int
    offlineNodes: int
    pendingNodes: int
    totalRecords: int
    localitiesCount: int
    lastSyncSeconds: int | None
    uptime30d: float
    generatedAt: datetime


class StationsResponse(BaseModel):
    stations: list[StationSchema]
    generatedAt: datetime
