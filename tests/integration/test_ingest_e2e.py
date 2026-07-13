"""E2E de ingesta contra Postgres real (post-M0).

Cubre los contratos que los unit tests no pueden ver y que causaron los
hallazgos críticos de la auditoría del 07-jul-2026:

- QW1: el UPSERT a ``bronze.raw_record`` funciona y es idempotente por
  ``event_id`` (``inserted`` → ``duplicate``, una sola fila).
- QW2: ``create-station`` deja la estación ingest-able (crea el
  ``canonical.source`` en la misma transacción).
- QW3: ``raw_record.observed_at`` sale del payload, no del envelope.
- Pipeline completo: envelope firmado HMAC real → 200; firma adulterada
  → 401; payload inválido post-HMAC → 400 + fila en DLQ.

Requisitos: Postgres accesible vía ``YVY_DB_DSN`` (default:
``postgresql+asyncpg://yvy:yvy@localhost:5432/yvy``) y migrado a head
(``alembic upgrade head`` — el CI lo corre antes de pytest). Si la DB no
está disponible, la suite se SKIPEA (no falla) para no romper corridas
locales sin docker.

Los datos de prueba quedan en la DB (Bronze es append-only por diseño);
cada corrida usa UUIDs nuevos, no colisiona.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from typer.testing import CliRunner

from yvy_server.cli.cli import app as cli_app
from yvy_server.config import get_settings
from yvy_server.domain.auth import canonicalize
from yvy_server.infrastructure.db import (
    create_engine_and_session_factory,
    dispose_engine,
)
from yvy_server.main import create_app

pytestmark = pytest.mark.integration


# ─── Infra helpers ────────────────────────────────────────────────────────


def _run_sql(query: str, params: dict[str, Any] | None = None) -> list[Any]:
    """Ejecuta una query contra la DB de settings y devuelve las filas.

    Abre y cierra su propio engine — simple y suficiente para asserts.
    """

    async def _inner() -> list[Any]:
        engine, factory = create_engine_and_session_factory(get_settings())
        try:
            async with factory() as session:
                result = await session.execute(text(query), params or {})
                return list(result.mappings().all())
        finally:
            await dispose_engine(engine)

    return asyncio.run(_inner())


def _db_ready() -> tuple[bool, str]:
    """(disponible, motivo). Skip limpio si no hay Postgres migrado."""
    try:
        rows = _run_sql("SELECT version_num FROM alembic_version")
    except Exception as exc:  # conexión rechazada, DB inexistente, etc.
        return False, f"Postgres no disponible: {exc.__class__.__name__}"
    if not rows:
        return False, "DB sin migrar (alembic_version vacía)"
    version = rows[0]["version_num"]
    if version != "0003_m0_canonical_model":
        return False, f"DB en revisión {version!r}; correr alembic upgrade head"
    return True, ""


_READY, _SKIP_REASON = _db_ready()
if not _READY:
    pytestmark = [pytest.mark.integration, pytest.mark.skip(reason=_SKIP_REASON)]


# ─── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def station(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Estación nueva creada vía el CLI real (cubre QW2 de punta a punta).

    Las credenciales se leen del ``--output-file`` (stdout del runner
    puede venir mezclado con los logs de stderr según la versión de click).
    """
    out_file = tmp_path_factory.mktemp("creds") / "station.json"
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "create-station",
            "--name",
            f"TEST integración {uuid.uuid4().hex[:8]}",
            "--location-name",
            "Test, Misiones",
            "--location-lat",
            "-27.5",
            "--location-lon",
            "-55.8",
            "--output-file",
            str(out_file),
        ],
    )
    assert result.exit_code == 0, f"create-station falló: {result.output}"
    credentials: dict[str, Any] = json.loads(out_file.read_text())
    return credentials


@pytest.fixture(scope="module")
def client() -> Any:
    """TestClient con lifespan real (startup carga source_cache, etc.)."""
    with TestClient(create_app()) as c:
        yield c


# ─── Envelope helpers ─────────────────────────────────────────────────────


def _make_sensor_envelope(
    station: dict[str, Any],
    *,
    event_id: str | None = None,
    observed_at: str = "2026-07-07T12:00:00Z",
) -> dict[str, Any]:
    now_iso = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    envelope: dict[str, Any] = {
        "schema_version": "1.0",
        "event_id": event_id or str(uuid.uuid4()),
        "station_uuid": station["station_uuid"],
        "key_version": station["key_version"],
        "sent_at_utc": now_iso,
        "nonce": uuid.uuid4().hex,
        "payload": {
            "observed_at": observed_at,
            "event_id": event_id or str(uuid.uuid4()),
            "temp_outdoor": "21.5",
            "hum_outdoor": 87,
            "pressure_rel": "1013.2",
        },
    }
    key = bytes.fromhex(station["key_hmac_hex"])
    signature = hmac.new(key, canonicalize(envelope), hashlib.sha256).hexdigest()
    return {**envelope, "signature": signature}


# ─── Tests ────────────────────────────────────────────────────────────────


class TestIngestE2E:
    def test_signed_ingest_inserts_raw_record(
        self, client: TestClient, station: dict[str, Any]
    ) -> None:
        """Happy path: envelope firmado → 200 inserted → fila en raw_record."""
        event_id = str(uuid.uuid4())
        envelope = _make_sensor_envelope(station, event_id=event_id)

        resp = client.post("/api/v1/ingest/sensor", json=envelope)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ingest_result"] == "inserted"
        assert body["event_id"] == event_id

        rows = _run_sql(
            "SELECT record_id, record_kind, source_type, observed_at "
            "FROM bronze.raw_record WHERE record_id = :rid",
            {"rid": event_id},
        )
        assert len(rows) == 1
        assert rows[0]["record_kind"] == "sensor_reading"
        assert rows[0]["source_type"] == "weather_station"

    def test_duplicate_event_id_is_idempotent(
        self, client: TestClient, station: dict[str, Any]
    ) -> None:
        """QW1: mismo event_id dos veces → duplicate, UNA sola fila."""
        event_id = str(uuid.uuid4())
        envelope = _make_sensor_envelope(station, event_id=event_id)

        first = client.post("/api/v1/ingest/sensor", json=envelope)
        assert first.status_code == 200
        assert first.json()["ingest_result"] == "inserted"

        # Re-envío literal (mismo envelope, misma firma) — caso retry del Edge
        second = client.post("/api/v1/ingest/sensor", json=envelope)
        assert second.status_code == 200, second.text
        assert second.json()["ingest_result"] == "duplicate"

        rows = _run_sql(
            "SELECT COUNT(*) AS n FROM bronze.raw_record WHERE record_id = :rid",
            {"rid": event_id},
        )
        assert rows[0]["n"] == 1

    def test_observed_at_comes_from_payload(
        self, client: TestClient, station: dict[str, Any]
    ) -> None:
        """QW3: observed_at = payload.observed_at, NO sent_at_utc del envelope."""
        event_id = str(uuid.uuid4())
        observed = "2026-07-01T03:30:00Z"  # deliberadamente lejos del sent_at (ahora)
        envelope = _make_sensor_envelope(station, event_id=event_id, observed_at=observed)

        resp = client.post("/api/v1/ingest/sensor", json=envelope)
        assert resp.status_code == 200, resp.text

        rows = _run_sql(
            "SELECT observed_at FROM bronze.raw_record WHERE record_id = :rid",
            {"rid": event_id},
        )
        assert rows[0]["observed_at"] == datetime(2026, 7, 1, 3, 30, tzinfo=UTC)

    def test_new_station_can_ingest_without_restart(
        self, client: TestClient, station: dict[str, Any]
    ) -> None:
        """QW2: la estación creada por CLI (fixture) ingesta sin reiniciar el server.

        El source_cache del startup NO la contiene (la fixture corre después
        del lifespan del TestClient en algunos órdenes) — el miss debe
        resolverse contra canonical.source y calentar el cache.
        """
        rows = _run_sql(
            "SELECT source_id FROM canonical.source "
            "WHERE source_type_key = 'weather_station' AND external_ref = :ref",
            {"ref": station["station_uuid"]},
        )
        assert len(rows) == 1, "create-station no creó el canonical.source (QW2)"

        envelope = _make_sensor_envelope(station)
        resp = client.post("/api/v1/ingest/sensor", json=envelope)
        assert resp.status_code == 200, resp.text

    def test_tampered_signature_is_401_auth(
        self, client: TestClient, station: dict[str, Any]
    ) -> None:
        envelope = _make_sensor_envelope(station)
        envelope["signature"] = "0" * 64

        resp = client.post("/api/v1/ingest/sensor", json=envelope)
        assert resp.status_code == 401
        assert resp.json()["error_class"] == "AUTH"

    def test_malformed_station_uuid_is_400_schema_not_500(
        self, client: TestClient, station: dict[str, Any]
    ) -> None:
        """QW4: campo presente pero malformado → 400 SCHEMA (nunca 500)."""
        envelope = _make_sensor_envelope(station)
        envelope["station_uuid"] = "banana"  # rompe la firma también, pero
        # la coerción falla ANTES del lookup/HMAC → SCHEMA

        resp = client.post("/api/v1/ingest/sensor", json=envelope)
        assert resp.status_code == 400, resp.text
        assert resp.json()["error_class"] == "SCHEMA"

    def test_invalid_payload_post_hmac_goes_to_dlq(
        self, client: TestClient, station: dict[str, Any]
    ) -> None:
        """Payload firmado pero inválido (falta observed_at) → 400 + DLQ."""
        event_id = str(uuid.uuid4())
        now_iso = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        envelope: dict[str, Any] = {
            "schema_version": "1.0",
            "event_id": event_id,
            "station_uuid": station["station_uuid"],
            "key_version": station["key_version"],
            "sent_at_utc": now_iso,
            "nonce": uuid.uuid4().hex,
            "payload": {"temp_outdoor": "21.5"},  # sin observed_at ni event_id
        }
        key = bytes.fromhex(station["key_hmac_hex"])
        envelope["signature"] = hmac.new(
            key,
            canonicalize({k: v for k, v in envelope.items() if k != "signature"}),
            hashlib.sha256,
        ).hexdigest()

        resp = client.post("/api/v1/ingest/sensor", json=envelope)
        assert resp.status_code == 400, resp.text
        assert resp.json()["error_class"] == "SCHEMA"

        dlq = _run_sql(
            "SELECT failure_class FROM bronze.ingest_failures WHERE event_id = :eid",
            {"eid": event_id},
        )
        assert len(dlq) == 1
        assert dlq[0]["failure_class"] == "SCHEMA"

        # Y NO llegó a bronze
        raw = _run_sql(
            "SELECT COUNT(*) AS n FROM bronze.raw_record WHERE record_id = :rid",
            {"rid": event_id},
        )
        assert raw[0]["n"] == 0
