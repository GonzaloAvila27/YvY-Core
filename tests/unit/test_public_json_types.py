"""Tipos JSON de /api/v1/public/*: los numéricos son number, null si no hay dato.

Se arma una app FastAPI solo con el router público y un repositorio falso en
``app.state`` (sin base de datos). Se verifica el JSON tal como lo recibe el
cliente: antes de este cambio lastValues y coordinates salían como string
porque eran Decimal.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from yvy_server.domain.public.models import LastValues, NetworkStatus, StationSummary
from yvy_server.presentation.api.dependencies import get_session_dep
from yvy_server.presentation.api.routers import public

pytestmark = pytest.mark.unit

_FULL = StationSummary(
    station_uuid=UUID("d9d14fce-2eb3-4521-a1cf-54022dded7d2"),
    code="IEA-07",
    name="IEA Nº7 Garupá",
    locality="Garupá",
    province="Misiones",
    latitude=Decimal("-27.366000"),
    longitude=Decimal("-55.896000"),
    active=True,
    status="online",
    last_sync_at=datetime(2026, 10, 4, 12, 5, 40, tzinfo=UTC),
    last_values=LastValues(
        temp=Decimal("16.7"),
        humidity=89,
        pressure=Decimal("996.9"),
        wind_speed=Decimal("2.7"),
        wind_gust=Decimal("3.7"),
        wind_direction=Decimal("135.0"),
        timestamp=datetime(2026, 10, 4, 12, 5, tzinfo=UTC),
    ),
)

# Estación con datos parciales: sin coordenadas y con lecturas faltantes.
_PARTIAL = StationSummary(
    station_uuid=UUID("e45774f2-4a98-4486-86c5-953217a27a07"),
    code="IEA-13",
    name="IEA 13 -Guarani",
    locality=None,
    province="Misiones",
    latitude=None,
    longitude=None,
    active=True,
    status="offline",
    last_sync_at=datetime(2026, 10, 16, 13, 24, 43, tzinfo=UTC),
    last_values=LastValues(
        temp=Decimal("20.8"),
        humidity=None,
        pressure=None,
        wind_speed=Decimal("0.0"),
        wind_gust=None,
        wind_direction=None,
        timestamp=datetime(2026, 10, 16, 13, 24, tzinfo=UTC),
    ),
)

_NETWORK = NetworkStatus(
    total_nodes=4,
    active_nodes=1,
    offline_nodes=3,
    pending_nodes=0,
    total_records=119125,
    localities_count=1,
    last_sync_seconds=12,
    uptime_30d=0.3104,
    generated_at=datetime(2026, 10, 22, 12, 0, tzinfo=UTC),
)

_NUMERIC_LAST_VALUES = ("temp", "humidity", "pressure", "windSpeed", "windGust", "windDirection")


class _FakeRepo:
    async def get_network_status(self, session: Any) -> NetworkStatus:
        return _NETWORK

    async def get_station_summaries(self, session: Any) -> list[StationSummary]:
        return [_FULL, _PARTIAL]

    async def get_station_by_uuid(self, session: Any, station_uuid: UUID) -> StationSummary | None:
        return next((s for s in (_FULL, _PARTIAL) if s.station_uuid == station_uuid), None)

    async def get_station_by_code(self, session: Any, code: str) -> StationSummary | None:
        return next((s for s in (_FULL, _PARTIAL) if s.code == code), None)


async def _no_session() -> AsyncIterator[None]:
    yield None


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(public.router)
    app.state.public_read_repo = _FakeRepo()
    app.dependency_overrides[get_session_dep] = _no_session
    return TestClient(app)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _assert_station_types(st: dict[str, Any]) -> None:
    coords = st["coordinates"]
    if coords is not None:
        assert isinstance(coords["lat"], float)
        assert isinstance(coords["lng"], float)
    lv = st["lastValues"]
    for field in _NUMERIC_LAST_VALUES:
        assert lv[field] is None or isinstance(lv[field], float), (field, lv[field])
    for field in ("dewpoint", "vpd"):
        assert lv[field] is None


class TestPublicJsonTypes:
    def test_network_numericos_son_number(self, client: TestClient) -> None:
        body = client.get("/api/v1/public/network").json()
        for field in (
            "totalNodes",
            "activeNodes",
            "offlineNodes",
            "pendingNodes",
            "totalRecords",
            "localitiesCount",
            "lastSyncSeconds",
            "uptime30d",
        ):
            assert _is_number(body[field]), (field, body[field])
        assert isinstance(body["uptime30d"], float)

    def test_stations_numericos_son_number(self, client: TestClient) -> None:
        resp = client.get("/api/v1/public/stations")
        assert resp.status_code == 200
        stations = resp.json()["stations"]
        assert len(stations) == 2
        for st in stations:
            _assert_station_types(st)

        full = stations[0]
        assert full["coordinates"] == {"lat": -27.366, "lng": -55.896}
        assert full["lastValues"]["temp"] == 16.7
        assert full["lastValues"]["humidity"] == 89.0
        assert full["lastValues"]["windDirection"] == 135.0

    def test_stations_null_donde_no_hay_dato(self, client: TestClient) -> None:
        partial = client.get("/api/v1/public/stations").json()["stations"][1]
        assert partial["coordinates"] is None
        lv = partial["lastValues"]
        assert lv["temp"] == 20.8
        assert lv["windSpeed"] == 0.0
        for field in ("humidity", "pressure", "windGust", "windDirection"):
            assert lv[field] is None

    @pytest.mark.parametrize("station_id", ["IEA-07", "d9d14fce-2eb3-4521-a1cf-54022dded7d2"])
    def test_station_por_id_numericos_son_number(self, client: TestClient, station_id: str) -> None:
        resp = client.get(f"/api/v1/public/stations/{station_id}")
        assert resp.status_code == 200
        (st,) = resp.json()["stations"]
        _assert_station_types(st)
        assert st["lastValues"]["pressure"] == 996.9

    def test_station_por_id_con_nulls(self, client: TestClient) -> None:
        (st,) = client.get("/api/v1/public/stations/IEA-13").json()["stations"]
        _assert_station_types(st)
        assert st["coordinates"] is None
        assert st["lastValues"]["pressure"] is None

    def test_estructura_sin_cambios(self, client: TestClient) -> None:
        """Mismos nombres de campos que antes del cambio de tipos."""
        st = client.get("/api/v1/public/stations").json()["stations"][0]
        assert set(st) == {
            "id",
            "code",
            "name",
            "locality",
            "province",
            "coordinates",
            "status",
            "lastSyncAt",
            "lastValues",
        }
        assert set(st["lastValues"]) == {
            "temp",
            "humidity",
            "pressure",
            "windSpeed",
            "windGust",
            "windDirection",
            "dewpoint",
            "vpd",
            "vpdZone",
            "timestamp",
        }
