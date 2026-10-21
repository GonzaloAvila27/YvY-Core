"""Tests de C-2 y C-3 contra el payload real de producción (sensor_raw, pywws).

- C-2: prioridad del timestamp de observación en ``BronzeRepository``:
  ``observed_at_utc`` → ``observed_at`` → ``sent_at_utc``.
- C-3: cast seguro de los valores de ``lastValues`` (strings numéricos, null,
  basura), conversión de ``wind_dir`` (índice de sector) a grados y schemas
  públicos sin obligatorios que puedan llegar NULL.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from yvy_server.domain.ingest import EntityType, IngestEvent
from yvy_server.infrastructure.persistence.bronze_repository import BronzeRepository
from yvy_server.infrastructure.persistence.public_read_repository import (
    parse_last_values,
    row_to_summary,
    to_decimal,
    to_int,
    wind_sector_to_degrees,
)
from yvy_server.presentation.api.routers.public import _to_station_schema

pytestmark = pytest.mark.unit

# Payload real de bronze.sensor_raw (2026-10-19), con event_id de ejemplo.
REAL_SENSOR_PAYLOAD: dict[str, Any] = {
    "observed_at": "2026-10-19T17:30:00+00:00",
    "temp_outdoor": "21.6",
    "hum_outdoor": 46,
    "temp_indoor": "20.1",
    "hum_indoor": 51,
    "pressure_abs": "978.9",
    "pressure_rel": None,
    "wind_speed": "0.3",
    "wind_gust": "0.7",
    "wind_dir": 1,
    "rain_1h": None,
    "rain_24h": None,
    "rain_total": "1028.1",
    "uv_index": None,
    "solar_radiation": None,
    "is_valid": True,
    "pywws_mode": "live",
    "validation_notes": None,
    "event_id": "00000000-0000-0000-0000-000000000000",
}

SENT_AT = datetime(2026, 10, 19, 18, 0, tzinfo=UTC)


def _event(payload: dict[str, Any]) -> IngestEvent:
    return IngestEvent(
        event_id=uuid4(),
        station_uuid=uuid4(),
        entity_type=EntityType.SENSOR_READING,
        schema_version="1.0",
        payload=payload,
        sent_at_utc=SENT_AT,
        received_at_utc=SENT_AT,
        received_from_ip=None,
        key_version_used=1,
        producer_version="test",
    )


# ─── C-2: timestamp de observación ───────────────────────────────────────


class TestObservedAtPriority:
    def test_payload_real_usa_observed_at(self) -> None:
        got = BronzeRepository._extract_observed_at(_event(REAL_SENSOR_PAYLOAD))
        assert got == datetime(2026, 10, 19, 17, 30, tzinfo=UTC)

    def test_observed_at_utc_tiene_prioridad(self) -> None:
        payload = {
            **REAL_SENSOR_PAYLOAD,
            "observed_at_utc": "2026-10-19T12:00:00Z",
        }
        got = BronzeRepository._extract_observed_at(_event(payload))
        assert got == datetime(2026, 10, 19, 12, 0, tzinfo=UTC)

    def test_observed_at_utc_invalido_cae_a_observed_at(self) -> None:
        payload = {**REAL_SENSOR_PAYLOAD, "observed_at_utc": "no-es-fecha"}
        got = BronzeRepository._extract_observed_at(_event(payload))
        assert got == datetime(2026, 10, 19, 17, 30, tzinfo=UTC)

    def test_sin_claves_cae_a_sent_at_utc(self) -> None:
        payload = {k: v for k, v in REAL_SENSOR_PAYLOAD.items() if k != "observed_at"}
        assert BronzeRepository._extract_observed_at(_event(payload)) == SENT_AT

    def test_valor_no_string_cae_a_sent_at_utc(self) -> None:
        payload = {**REAL_SENSOR_PAYLOAD, "observed_at": 1729358400}
        assert BronzeRepository._extract_observed_at(_event(payload)) == SENT_AT

    def test_sin_zona_se_toma_como_utc(self) -> None:
        payload = {**REAL_SENSOR_PAYLOAD, "observed_at": "2026-10-19T10:00:00"}
        got = BronzeRepository._extract_observed_at(_event(payload))
        assert got == datetime(2026, 10, 19, 10, 0, tzinfo=UTC)


# ─── C-3: cast seguro ────────────────────────────────────────────────────


class TestSafeCasts:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("21.6", Decimal("21.6")),
            (" 978.9 ", Decimal("978.9")),
            (46, Decimal(46)),
            (0.3, Decimal("0.3")),
            (Decimal("1.5"), Decimal("1.5")),
            ("-3", Decimal(-3)),
        ],
    )
    def test_to_decimal_valores_validos(self, value: object, expected: Decimal) -> None:
        assert to_decimal(value) == expected

    @pytest.mark.parametrize(
        "value",
        [None, "", "abc", "21,6", "NaN", "Infinity", float("nan"), float("inf"), True, [1]],
    )
    def test_to_decimal_invalidos_son_none(self, value: object) -> None:
        assert to_decimal(value) is None

    def test_to_int(self) -> None:
        assert to_int(46) == 46
        assert to_int("46") == 46
        assert to_int("46.0") == 46
        assert to_int(None) is None
        assert to_int("x") is None

    @pytest.mark.parametrize(
        ("index", "degrees"),
        [(0, Decimal("0")), (1, Decimal("22.5")), (4, Decimal("90")), (15, Decimal("337.5"))],
    )
    def test_wind_sector_a_grados(self, index: int, degrees: Decimal) -> None:
        assert wind_sector_to_degrees(index) == degrees

    @pytest.mark.parametrize("value", [16, -1, 1.5, "1.5", None, "N", 157.5])
    def test_wind_sector_fuera_de_rango_es_none(self, value: object) -> None:
        assert wind_sector_to_degrees(value) is None

    def test_wind_sector_acepta_string_entero(self) -> None:
        assert wind_sector_to_degrees("8") == Decimal("180")


# ─── C-3: lastValues y schema público ────────────────────────────────────


def _view_last_values(payload: dict[str, Any]) -> dict[str, Any]:
    """Replica el jsonb_build_object de v_station_summary (claves reales)."""
    return {
        "temp": payload.get("temp_outdoor"),
        "humidity": payload.get("hum_outdoor"),
        "pressure": payload.get("pressure_abs"),
        "windSpeed": payload.get("wind_speed"),
        "windGust": payload.get("wind_gust"),
        "windDirIndex": payload.get("wind_dir"),
        "timestamp": "2026-10-19T17:30:00+00:00",
    }


def _view_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "station_uuid": uuid4(),
        "code": "IEA-13",
        "name": "IEA 13 -Guarani",
        "locality": None,
        "province": "Misiones",
        "latitude": None,
        "longitude": None,
        "active": True,
        "last_sync_at": datetime(2026, 10, 19, 17, 31, tzinfo=UTC),
        "status": "online",
        "last_values": _view_last_values(REAL_SENSOR_PAYLOAD),
    }
    row.update(overrides)
    return row


class TestLastValuesPayloadReal:
    def test_parsea_el_payload_real(self) -> None:
        lv = parse_last_values(_view_last_values(REAL_SENSOR_PAYLOAD))
        assert lv is not None
        assert lv.temp == Decimal("21.6")
        assert lv.humidity == 46
        assert lv.pressure == Decimal("978.9")
        assert lv.wind_speed == Decimal("0.3")
        assert lv.wind_gust == Decimal("0.7")
        assert lv.wind_direction == Decimal("22.5")

    def test_valores_basura_quedan_en_none(self) -> None:
        payload = {
            **REAL_SENSOR_PAYLOAD,
            "temp_outdoor": "---",
            "hum_outdoor": None,
            "pressure_abs": "",
            "wind_dir": 99,
        }
        lv = parse_last_values(_view_last_values(payload))
        assert lv is not None
        assert (lv.temp, lv.humidity, lv.pressure, lv.wind_direction) == (None, None, None, None)

    def test_sin_payload_es_none(self) -> None:
        assert parse_last_values(None) is None

    def test_schema_publico_con_payload_real(self) -> None:
        body = _to_station_schema(row_to_summary(_view_row())).model_dump(mode="json")
        lv = body["lastValues"]
        assert body["code"] == "IEA-13"
        assert body["coordinates"] is None
        assert lv["temp"] == "21.6"
        assert lv["windDirection"] == "22.5"
        # Campos del contrato que dependen de C13: presentes y en null.
        assert lv["dewpoint"] is None
        assert lv["vpd"] is None
        assert lv["vpdZone"] is None

    def test_schema_publico_acepta_nulls(self) -> None:
        """Ningún campo que pueda llegar NULL con datos reales es obligatorio."""
        row = _view_row(
            code=None,
            locality=None,
            province=None,
            latitude=None,
            longitude=None,
            last_sync_at=None,
            status="pending",
            last_values=None,
        )
        body = _to_station_schema(row_to_summary(row)).model_dump(mode="json")
        assert body["code"] is None
        assert body["province"] == "Misiones"
        assert body["lastValues"] is None
        assert body["lastSyncAt"] is None

    def test_coordenadas_desde_numeric(self) -> None:
        row = _view_row(latitude=Decimal("-27.366"), longitude=Decimal("-55.896"))
        body = _to_station_schema(row_to_summary(row)).model_dump(mode="json")
        assert body["coordinates"] == {"lat": "-27.366", "lng": "-55.896"}
