"""YVY server CLI — operational, minimal.

Single non-trivial command: ``create-station``. Generates a station
identity (UUID4 + 32-byte HMAC-SHA256 key) and inserts the rows in
``station`` and ``station_credentials`` (key_version=1).

The credentials JSON printed to stdout (and optionally to a file) is
what the operator carries to the IEA edge: the edge needs the
``station_uuid`` to build envelopes and the ``key_hmac_hex`` to sign
them.

Design notes
------------

- **Sync Typer**, async DB internal via ``asyncio.run``. The CLI runs
  once per onboarding; cooperative concurrency adds no value here, and
  sync handlers are simpler.
- **No write to ``bronze.ingest_log``.** That table's semantics are
  "HTTP ingest attempt". Administrative actions (create-station, future
  rotate-key, etc) audit via structured JSON logs (``logger.info``)
  that systemd captures. If persisted admin audit becomes a hard
  requirement, a separate ``admin_audit_log`` table will be added —
  not by extending ``ingest_log``.
- **Single subcommand.** ``list-stations``, ``rotate-key``,
  ``deactivate-station`` are out of scope for 9.1; they come if/when
  operational practice demands them.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import typer

from yvy_server import __version__
from yvy_server.config import get_settings
from yvy_server.domain.station import Station, StationCredential
from yvy_server.infrastructure.db import (
    create_engine_and_session_factory,
    dispose_engine,
)
from yvy_server.infrastructure.persistence import (
    CredentialRepository,
    EdgeReleaseRepository,
    StationRepository,
)
from yvy_server.logging_setup import setup_logging

app = typer.Typer(
    name="yvy-server",
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    help="YVY server operational CLI. Minimal by design.",
)


# ─── create-station ─────────────────────────────────────────────────────


@app.command("create-station")
def create_station(
    name: str = typer.Option(
        ...,
        "--name",
        "-n",
        help="Display name (e.g. 'IEA 7 - Cerro Azul').",
    ),
    description: str | None = typer.Option(None, "--description", help="Free-form description."),
    location_lat: float | None = typer.Option(
        None,
        "--location-lat",
        help="Latitude in decimal degrees (south is negative).",
    ),
    location_lon: float | None = typer.Option(
        None,
        "--location-lon",
        help="Longitude in decimal degrees (west is negative).",
    ),
    location_name: str | None = typer.Option(
        None,
        "--location-name",
        help="Human-readable location (e.g. 'Cerro Azul, Misiones').",
    ),
    output_file: Path | None = typer.Option(
        None,
        "--output-file",
        "-o",
        help="Write the credentials JSON to this file (in addition to stdout).",
    ),
) -> None:
    """Register a new edge station.

    Generates a UUID4 and a 32-byte (256-bit) HMAC-SHA256 key. Inserts
    the rows in ``station`` and ``station_credentials`` (key_version=1).
    Prints the credentials JSON to stdout for the operator to deliver
    to the IEA.

    The JSON contains: ``station_uuid``, ``key_version``, ``key_hmac_hex``,
    ``name``, ``created_at_utc``. The edge needs at minimum
    ``station_uuid`` and ``key_hmac_hex`` to build signed envelopes.

    Exits with code 0 on success, 1 on any error (DB unreachable,
    constraint violation, etc).
    """
    settings = get_settings()
    # CLI logs go to stderr so stdout stays pipe-friendly (the JSON
    # credentials are the only thing on stdout).
    setup_logging(settings.log_level, stream=sys.stderr)
    log = logging.getLogger("yvy.cli")

    # 1. Generate identity in-memory
    station_uuid = uuid4()
    key_bytes = secrets.token_bytes(32)
    key_hmac_hex = key_bytes.hex()
    now = datetime.now(UTC)

    # 2. Persist (async repos + asyncio.run wrapper)
    try:
        asyncio.run(
            _persist_station(
                settings=settings,
                station_uuid=station_uuid,
                name=name,
                description=description,
                location_lat=location_lat,
                location_lon=location_lon,
                location_name=location_name,
                key_hmac_hex=key_hmac_hex,
                created_at=now,
            )
        )
    except Exception as err:
        log.exception(
            "create_station failed",
            extra={"display_name": name, "station_uuid": str(station_uuid)},
        )
        typer.secho(f"ERROR: {err}", fg="red", err=True)
        raise typer.Exit(code=1) from err

    # 3. Output FIRST: this is the operator's only way to get the key.
    #    Even if downstream steps fail (audit log, etc), the credentials
    #    must reach stdout so the IEA onboarding doesn't end with a
    #    station registered in DB but inaccessible to the edge.
    credentials = {
        "station_uuid": str(station_uuid),
        "key_version": 1,
        "key_hmac_hex": key_hmac_hex,
        "name": name,
        "created_at_utc": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }

    # Pretty JSON to stdout. ``json.dumps`` ordering follows the dict
    # literal above — stable across runs.
    typer.echo(json.dumps(credentials, indent=2))

    if output_file is not None:
        output_file.write_text(json.dumps(credentials, indent=2) + "\n")
        # File-written notice goes to stderr so stdout stays pipe-able.
        typer.secho(
            f"Credentials written to {output_file}",
            fg="green",
            err=True,
        )

    # 4. Structured audit log (NOT to bronze.ingest_log — that's HTTP-only).
    #    Note: ``name`` is a reserved key in ``LogRecord`` (it's the
    #    logger name). Use ``display_name`` to carry the station's
    #    user-facing label.
    log.info(
        "station_created",
        extra={
            "station_uuid": str(station_uuid),
            "display_name": name,
            "key_version": 1,
            "key_length_bytes": len(key_bytes),
        },
    )


async def _persist_station(
    *,
    settings: Any,  # Settings (avoid import cycle in type hint)
    station_uuid: UUID,
    name: str,
    description: str | None,
    location_lat: float | None,
    location_lon: float | None,
    location_name: str | None,
    key_hmac_hex: str,
    created_at: datetime,
) -> None:
    """Open an engine, insert station + credential atomically, dispose.

    Single transaction with ``session.flush()`` between adds so the
    FK constraint on ``station_credentials.station_uuid`` is satisfied.
    """
    engine, factory = create_engine_and_session_factory(settings)
    try:
        async with factory() as session, session.begin():
            await StationRepository().insert(
                session,
                Station(
                    station_uuid=station_uuid,
                    name=name,
                    description=description,
                    location_lat=location_lat,
                    location_lon=location_lon,
                    location_name=location_name,
                    created_at=created_at,
                    deactivated_at=None,
                ),
            )
            await session.flush()
            await CredentialRepository().insert(
                session,
                StationCredential(
                    credential_id=0,  # ignored on insert (BIGSERIAL)
                    station_uuid=station_uuid,
                    key_version=1,
                    key_hmac_hex=key_hmac_hex,
                    created_at=created_at,
                    deactivated_at=None,
                    reason=None,
                ),
            )
    finally:
        await dispose_engine(engine)


# ─── publish-update ─────────────────────────────────────────────────────


@app.command("publish-update")
def publish_update(
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to the .tar.gz package to publish.",
    ),
    edge_version: str = typer.Option(
        ...,
        "--version",
        "-v",
        help="Semver version string for this release (e.g. '0.9.3').",
    ),
    min_edge_version: str = typer.Option(
        "0.0.0",
        "--min-edge-version",
        help="Minimum Edge version from which this update can be safely applied.",
    ),
    notes: str | None = typer.Option(
        None,
        "--notes",
        "-n",
        help="Human-readable release notes (shown in the Edge UI).",
    ),
    releases_dir: Path | None = typer.Option(
        None,
        "--releases-dir",
        help=(
            "Override the releases directory. Defaults to settings.releases_dir "
            "(YVY_RELEASES_DIR env var or 'data/releases')."
        ),
    ),
) -> None:
    """Publish a new Edge software release.

    1. Computes SHA-256 of ``--file``.
    2. Copies the file to the releases directory.
    3. Inserts a row in ``public.edge_releases``.
    4. Atomically increments ``public.sync_state.sync_version``.

    The server's running instance caches ``sync_version`` at startup;
    restart the server after publishing so ingest piggyback headers
    reflect the new value immediately.

    Exits with code 0 on success, 1 on any error.
    """
    settings = get_settings()
    setup_logging(settings.log_level, stream=sys.stderr)
    log = logging.getLogger("yvy.cli")

    dest_dir = Path(releases_dir) if releases_dir else Path(settings.releases_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 1. Compute SHA-256
    sha256 = _sha256_file(file)
    size_bytes = file.stat().st_size

    # 2. Determine destination filename
    dest_filename = file.name
    dest_path = dest_dir / dest_filename

    if dest_path.exists():
        typer.secho(
            f"WARNING: {dest_path} already exists. Overwriting.",
            fg="yellow",
            err=True,
        )

    # 3. Copy file to releases dir (no-op if source == dest)
    if file.resolve() != dest_path.resolve():
        shutil.copy2(str(file), str(dest_path))
        typer.secho(f"Copied {file} → {dest_path}", fg="green", err=True)
    else:
        typer.secho(f"File already at destination: {dest_path}", fg="green", err=True)

    # 4. Persist release in DB
    now = datetime.now(UTC)
    try:
        asyncio.run(
            _persist_release(
                settings=settings,
                edge_version=edge_version,
                package_filename=dest_filename,
                package_sha256=sha256,
                package_size_bytes=size_bytes,
                min_edge_version=min_edge_version,
                notes=notes,
                released_at=now,
            )
        )
    except Exception as err:
        log.exception(
            "publish_update failed",
            extra={"edge_version": edge_version, "filename": dest_filename},
        )
        typer.secho(f"ERROR: {err}", fg="red", err=True)
        raise typer.Exit(code=1) from err

    result = {
        "edge_version": edge_version,
        "package_filename": dest_filename,
        "package_sha256": sha256,
        "package_size_bytes": size_bytes,
        "min_edge_version": min_edge_version,
        "released_at_utc": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "notes": notes,
    }
    typer.echo(json.dumps(result, indent=2))

    log.info(
        "update.published",
        extra={
            "version": edge_version,
            "released_at": now.isoformat(),
            "sha256": sha256,
            "size_bytes": size_bytes,
            "min_edge_version": min_edge_version,
        },
    )


def _sha256_file(path: Path) -> str:
    """Return the hex SHA-256 of a file, reading in 1 MiB chunks."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


async def _persist_release(
    *,
    settings: Any,  # Settings — avoid import cycle in type hint
    edge_version: str,
    package_filename: str,
    package_sha256: str,
    package_size_bytes: int,
    min_edge_version: str,
    notes: str | None,
    released_at: datetime,
) -> None:
    """Open an engine, insert the release + bump sync_version atomically, dispose."""
    engine, factory = create_engine_and_session_factory(settings)
    try:
        async with factory() as session, session.begin():
            _, new_sync = await EdgeReleaseRepository().publish_release(
                session,
                edge_version=edge_version,
                package_filename=package_filename,
                package_sha256=package_sha256,
                package_size_bytes=package_size_bytes,
                min_edge_version=min_edge_version,
                notes=notes,
                released_at=released_at,
            )
        typer.secho(
            f"Release registered. New sync_version={new_sync}",
            fg="green",
            err=True,
        )
    finally:
        await dispose_engine(engine)


# ─── version ─────────────────────────────────────────────────────────────


@app.command("version")
def version() -> None:
    """Print the server version string and exit.

    Useful for verifying which build is installed without needing a
    running server (the same value is in ``GET /api/v1/serverinfo``).
    """
    typer.echo(__version__)


# ─── Entry point ─────────────────────────────────────────────────────────


def main() -> None:
    """Console-script entry point.

    Defined separately from ``app`` for callers that import the function
    directly (e.g. tests). ``pyproject.toml`` points to ``app`` because
    Typer apps are themselves callable.
    """
    app()


if __name__ == "__main__":
    main()
