"""Alta de estaciones post-M0 con ``yvy-server create-station`` (C-1b).

Contra Postgres real migrado a head, igual que ``test_ingest_e2e.py``. Se
skipea si la base no está disponible o no está en ``0003_m0_canonical_model``.

Cubre:
- alta OK: una sola transacción crea ``station`` (con ``code``),
  ``station_credentials`` y ``canonical.source``;
- ``--code`` duplicado: exit code ``EXIT_DUPLICATE_CODE``, mensaje claro, sin
  traceback y sin ninguna fila nueva en las tres tablas.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from yvy_server.cli.cli import EXIT_DUPLICATE_CODE
from yvy_server.cli.cli import app as cli_app
from yvy_server.config import get_settings
from yvy_server.infrastructure.db import create_engine_and_session_factory, dispose_engine

pytestmark = pytest.mark.integration


def _run_sql(query: str, params: dict[str, Any] | None = None) -> list[Any]:
    async def _inner() -> list[Any]:
        engine, factory = create_engine_and_session_factory(get_settings())
        try:
            async with factory() as session:
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
    if not rows or rows[0]["version_num"] != "0003_m0_canonical_model":
        return False, "DB no migrada a 0003; correr alembic upgrade head"
    return True, ""


_READY, _SKIP_REASON = _db_ready()
if not _READY:
    pytestmark = [pytest.mark.integration, pytest.mark.skip(reason=_SKIP_REASON)]


def _create_station(name: str, code: str, out_file: Path | None = None) -> Any:
    args = [
        "create-station",
        "--name",
        name,
        "--code",
        code,
        "--locality",
        "Localidad Test",
        "--location-lat",
        "-27.5",
        "--location-lon",
        "-55.8",
    ]
    if out_file is not None:
        args += ["--output-file", str(out_file)]
    return CliRunner().invoke(cli_app, args)


def _counts() -> dict[str, int]:
    rows = _run_sql(
        "SELECT (SELECT COUNT(*) FROM station) AS station, "
        "(SELECT COUNT(*) FROM station_credentials) AS credentials, "
        "(SELECT COUNT(*) FROM canonical.source) AS source"
    )
    return {k: int(v) for k, v in rows[0].items()}


class TestCreateStation:
    def test_alta_crea_station_credencial_y_source(self, tmp_path: Path) -> None:
        code = f"TEST-{uuid.uuid4().hex[:8]}"
        out_file = tmp_path / "creds.json"

        result = _create_station(f"Alta {code}", code, out_file)
        assert result.exit_code == 0, result.output

        creds = json.loads(out_file.read_text())
        assert creds["code"] == code
        station_uuid = creds["station_uuid"]

        station = _run_sql(
            "SELECT code, locality, province, active, latitude, longitude "
            "FROM station WHERE station_uuid = :u",
            {"u": station_uuid},
        )
        assert len(station) == 1
        assert station[0]["code"] == code
        assert station[0]["locality"] == "Localidad Test"
        assert station[0]["province"] == "Misiones"
        assert station[0]["active"] is True
        assert float(station[0]["latitude"]) == -27.5

        cred = _run_sql(
            "SELECT key_version FROM station_credentials WHERE station_uuid = :u",
            {"u": station_uuid},
        )
        assert [r["key_version"] for r in cred] == [1]

        source = _run_sql(
            "SELECT metadata->>'code' AS code, metadata->>'locality' AS locality "
            "FROM canonical.source "
            "WHERE source_type_key = 'weather_station' AND external_ref = :u",
            {"u": station_uuid},
        )
        assert source == [{"code": code, "locality": "Localidad Test"}]

    def test_code_duplicado_falla_sin_filas_a_medias(self) -> None:
        code = f"TEST-{uuid.uuid4().hex[:8]}"
        first = _create_station(f"Primera {code}", code)
        assert first.exit_code == 0, first.output

        before = _counts()
        second = _create_station(f"Segunda {code}", code)

        assert second.exit_code == EXIT_DUPLICATE_CODE
        assert "ya existe una estación" in second.output
        assert "Traceback" not in second.output
        assert _counts() == before
        dup = _run_sql(
            "SELECT COUNT(*) AS n FROM station WHERE name = :n", {"n": f"Segunda {code}"}
        )
        assert dup[0]["n"] == 0
