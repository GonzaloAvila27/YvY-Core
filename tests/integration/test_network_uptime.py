"""uptime_30d de v_network_status no depende del minuto de la consulta (0004).

Contra Postgres real migrado a head. Se skipea si la base no está disponible o
no está en ``0004_fix_network_uptime``.

La vista calcula el uptime con ``public.yvy_network_uptime_30d(now())``. Acá se
llama a la función con instantes de referencia fijos (año 2020, lejos de los
datos que crean los otros tests) sobre una estación activa con lecturas
conocidas.

Antes de la 0004 el resultado era 0.0 salvo cuando el minuto de la consulta
era múltiplo de 5 (producción: 18:49:49 → 0.0; 18:50:03 → 0.3907).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from yvy_server.config import get_settings
from yvy_server.infrastructure.db import create_engine_and_session_factory, dispose_engine

pytestmark = pytest.mark.integration


def _run_sql(query: str, params: dict[str, Any] | None = None, *, write: bool = False) -> list[Any]:
    async def _inner() -> list[Any]:
        engine, factory = create_engine_and_session_factory(get_settings())
        try:
            async with factory() as session:
                if write:
                    async with session.begin():
                        await session.execute(text(query), params or {})
                    return []
                result = await session.execute(text(query), params or {})
                return [dict(r) for r in result.mappings().all()]
        finally:
            await dispose_engine(engine)

    return asyncio.run(_inner())


def _db_ready() -> tuple[bool, str]:
    try:
        rows = _run_sql("SELECT version_num FROM alembic_version")
    except Exception as exc:
        return False, f"Postgres no disponible: {exc.__class__.__name__}"
    if not rows or rows[0]["version_num"] != "0004_fix_network_uptime":
        return False, "DB no migrada a 0004; correr alembic upgrade head"
    return True, ""


_READY, _SKIP_REASON = _db_ready()
if not _READY:
    pytestmark = [pytest.mark.integration, pytest.mark.skip(reason=_SKIP_REASON)]

# 24 lecturas, una cada 5 minutos, entre 10:42 y 12:37 del 2020-06-15: 24
# buckets de 5 minutos distintos, todos anteriores a las referencias del test.
_FIRST = datetime(2020, 6, 15, 10, 42, tzinfo=UTC)
_N_BUCKETS = 24
# Serie de 30 días en pasos de 5 minutos: 8640 intervalos → 8641 buckets.
_EXPECTED = (Decimal(_N_BUCKETS) / Decimal(8641)).quantize(Decimal("0.0001"))


@pytest.fixture(scope="module")
def station_with_readings() -> str:
    """Estación activa + canonical.source + lecturas en instantes conocidos."""
    station_uuid = uuid.uuid4()
    source_id = uuid.uuid4()
    code = f"TEST-UP-{uuid.uuid4().hex[:6]}"
    _run_sql(
        "INSERT INTO station (station_uuid, name, code, active) VALUES (:u, :n, :c, true)",
        {"u": station_uuid, "n": f"Uptime {code}", "c": code},
        write=True,
    )
    _run_sql(
        "INSERT INTO canonical.source "
        "(source_id, source_type_key, external_ref, name, metadata, active) "
        "VALUES (:s, 'weather_station', :ref, :n, CAST(:m AS jsonb), true)",
        {"s": source_id, "ref": str(station_uuid), "n": code, "m": f'{{"code": "{code}"}}'},
        write=True,
    )
    for i in range(_N_BUCKETS):
        at = _FIRST + timedelta(minutes=5 * i)
        # Dos lecturas en el mismo bucket: el bucket cuenta una sola vez.
        for offset in (0, 1):
            _run_sql(
                "INSERT INTO bronze.raw_record "
                "(record_id, source_id, source_type, record_kind, payload, schema_version, "
                " observed_at, received_at) "
                "VALUES (:r, :s, 'weather_station', 'sensor_reading', CAST('{}' AS jsonb), "
                " '1.0', :t, :t)",
                {"r": uuid.uuid4(), "s": source_id, "t": at + timedelta(seconds=30 * offset)},
                write=True,
            )
    return code


def _uptime_at(ref: datetime) -> Decimal:
    rows = _run_sql("SELECT public.yvy_network_uptime_30d(:ref) AS v", {"ref": ref})
    return Decimal(rows[0]["v"])


class TestNetworkUptime:
    @pytest.mark.parametrize(
        "ref",
        [
            datetime(2020, 6, 15, 12, 47, 0, tzinfo=UTC),
            datetime(2020, 6, 15, 12, 49, 49, tzinfo=UTC),
            datetime(2020, 6, 15, 12, 50, 0, tzinfo=UTC),
            datetime(2020, 6, 15, 12, 50, 3, tzinfo=UTC),
        ],
    )
    def test_uptime_no_depende_del_minuto(self, station_with_readings: str, ref: datetime) -> None:
        assert _uptime_at(ref) == _EXPECTED

    def test_uptime_igual_en_47_y_50_y_mayor_a_cero(self, station_with_readings: str) -> None:
        at_47 = _uptime_at(datetime(2020, 6, 15, 12, 47, tzinfo=UTC))
        at_50 = _uptime_at(datetime(2020, 6, 15, 12, 50, tzinfo=UTC))
        assert at_47 == at_50
        assert at_47 > 0

    def test_lecturas_posteriores_a_ref_no_cuentan(self, station_with_readings: str) -> None:
        # A las 11:00 solo existen las lecturas de 10:42, 10:47, 10:52 y 10:57.
        got = _uptime_at(datetime(2020, 6, 15, 11, 0, tzinfo=UTC))
        assert got == (Decimal(4) / Decimal(8641)).quantize(Decimal("0.0001"))

    def test_estacion_inactiva_no_cuenta(self, station_with_readings: str) -> None:
        code = station_with_readings
        ref = datetime(2020, 6, 15, 12, 47, tzinfo=UTC)
        _run_sql(
            "UPDATE canonical.source SET active = false WHERE metadata->>'code' = :c",
            {"c": code},
            write=True,
        )
        try:
            assert _uptime_at(ref) == Decimal(0)
        finally:
            _run_sql(
                "UPDATE canonical.source SET active = true WHERE metadata->>'code' = :c",
                {"c": code},
                write=True,
            )

    def test_la_vista_usa_la_funcion(self) -> None:
        rows = _run_sql("SELECT uptime_30d FROM v_network_status")
        assert len(rows) == 1
        assert isinstance(rows[0]["uptime_30d"], Decimal)
