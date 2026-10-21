"""Pydantic schemas for the public read API (/api/v1/public/*).

Separate from schemas.py (ingest) to keep each surface area small.
Shape matches the DATA-CONTRACT.md spec (Apéndice A of the plan).

Ningún campo que pueda llegar NULL con datos reales es obligatorio: un
campo obligatorio en NULL hace que el endpoint responda 500.

Los valores numéricos de ``lastValues`` y ``coordinates`` son ``float`` para que
se serialicen como number JSON (con ``Decimal`` Pydantic los emite como
string); ``null`` cuando no hay dato. Internamente (dominio y repositorio)
siguen siendo ``Decimal``; la conversión se hace en el router.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LastValuesSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    temp: float | None = Field(default=None, description="Temperatura exterior (°C).")
    humidity: float | None = Field(default=None, description="Humedad relativa exterior (%).")
    pressure: float | None = Field(
        default=None, description="Presión absoluta de la estación (hPa, pressure_abs de pywws)."
    )
    windSpeed: float | None = Field(default=None, description="Velocidad del viento (m/s).")
    windGust: float | None = Field(default=None, description="Ráfaga (m/s).")
    windDirection: float | None = Field(
        default=None,
        description=(
            "Dirección del viento en grados (0-337.5). Se convierte del índice "
            "de sector de pywws (0-15) como índice * 22.5."
        ),
    )
    # Campos del contrato que dependen de C13 (variables derivadas). Hoy
    # siempre null; se mantienen para que la web no tenga que cambiar.
    dewpoint: float | None = None
    vpd: float | None = None
    vpdZone: str | None = None
    timestamp: datetime | None = Field(default=None, description="Momento de la observación (UTC).")


class CoordinatesSchema(BaseModel):
    lat: float
    lng: float


class StationSchema(BaseModel):
    id: UUID
    code: str | None
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
