"""Edge Canal Core→Edge endpoints.

Two endpoints live here:

``GET /api/v1/edge/sync``
    HMAC-authenticated pull: returns the current ``sync_version`` and,
    when a newer release is available for this edge, the full update
    metadata (version, package URL, SHA-256, size, notes).

``GET /api/v1/edge/updates/{filename}``
    HMAC-authenticated download of a published ``.tar.gz`` package.
    Streams the file from ``settings.releases_dir`` with the SHA-256
    in a response header for integrity verification.

Authentication
--------------

Both endpoints use HMAC-SHA256 over the *headers* (not a request body):
the canonical JSON of ``key_version``, ``nonce``, ``sent_at_utc`` and
``station_uuid`` (alphabetical order). The signature travels in
``Authorization: HMAC <hex_signature>``.

This is distinct from ingest auth (body-based HMAC) and admin auth
(Bearer token). See ``YVY_Core_Spec_Canal_Pull.md`` §3 for rationale.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yvy_server.domain.auth import canonicalize
from yvy_server.domain.station import Station, StationCredential
from yvy_server.infrastructure.db import EdgeReleaseORM
from yvy_server.infrastructure.persistence import (
    CredentialRepository,
    EdgeReleaseRepository,
    StationRepository,
)
from yvy_server.presentation.api.dependencies import get_session_dep

_log = logging.getLogger("yvy.edge")

router = APIRouter(prefix="/api/v1/edge", tags=["edge"])


# ─── HMAC header verification ────────────────────────────────────────────


async def _verify_edge_hmac(
    request: Request,
    session: AsyncSession,
) -> tuple[Station, StationCredential]:
    """Verify the HMAC-over-headers authentication on edge requests.

    Raises ``HTTPException(401)`` on any auth failure (missing headers,
    bad HMAC, unknown station, deactivated credential).
    Raises ``HTTPException(422)`` on clock skew exceeding the configured
    maximum.
    """
    # Extract required headers
    authorization = request.headers.get("authorization", "")
    station_uuid_str = request.headers.get("x-station-uuid")
    key_version_str = request.headers.get("x-key-version")
    nonce = request.headers.get("x-nonce")
    sent_at_utc_str = request.headers.get("x-sent-at-utc")

    required = {
        "Authorization": authorization,
        "X-Station-UUID": station_uuid_str,
        "X-Key-Version": key_version_str,
        "X-Nonce": nonce,
        "X-Sent-At-UTC": sent_at_utc_str,
    }
    missing = [name for name, val in required.items() if not val]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": f"Missing required headers: {missing}", "error_class": "AUTH"},
        )

    # Parse Authorization: HMAC <hex>
    scheme, _, signature = authorization.partition(" ")
    if scheme.lower() != "hmac" or not signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Authorization must be 'HMAC <hex>'", "error_class": "AUTH"},
        )

    # Parse station_uuid and key_version
    try:
        station_uuid = UUID(station_uuid_str)  # type: ignore[arg-type]
        key_version = int(key_version_str)  # type: ignore[arg-type]
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid X-Station-UUID or X-Key-Version", "error_class": "AUTH"},
        )

    # Resolve station + credential
    station_repo: StationRepository = request.app.state.station_repo
    credential_repo: CredentialRepository = request.app.state.credential_repo

    station = await station_repo.get_by_uuid(session, station_uuid)
    if station is None:
        _log.warning("edge auth: unknown station", extra={"station_uuid": str(station_uuid)})
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": f"station_uuid={station_uuid} not registered", "error_class": "AUTH"},
        )

    credential = await credential_repo.get_active_by_station_and_version(
        session, station_uuid, key_version
    )
    if credential is None:
        _log.warning(
            "edge auth: no active credential",
            extra={"station_uuid": str(station_uuid), "key_version": key_version},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "No active credential for this station+key_version", "error_class": "AUTH"},
        )

    # Build canonical JSON and verify HMAC
    to_sign = {
        "key_version": key_version,
        "nonce": nonce,
        "sent_at_utc": sent_at_utc_str,
        "station_uuid": station_uuid_str,
    }
    canonical_bytes = canonicalize(to_sign)
    computed = hmac.new(credential.key_bytes, canonical_bytes, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed, signature):
        _log.warning(
            "edge auth: HMAC mismatch",
            extra={"station_uuid": str(station_uuid)},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "HMAC signature mismatch", "error_class": "AUTH"},
        )

    # Clock skew check
    try:
        if sent_at_utc_str.endswith("Z"):  # type: ignore[union-attr]
            sent_at_str = sent_at_utc_str[:-1] + "+00:00"
        else:
            sent_at_str = sent_at_utc_str  # type: ignore[assignment]
        sent_at = datetime.fromisoformat(sent_at_str)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid X-Sent-At-UTC format", "error_class": "AUTH"},
        )

    max_skew: int = request.app.state.settings.clock_skew_max_seconds
    skew = int(abs((datetime.now(UTC) - sent_at).total_seconds()))
    if skew > max_skew:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": f"Clock skew {skew}s exceeds max {max_skew}s", "error_class": "CLOCK"},
        )

    return station, credential


# ─── Version comparison helper ────────────────────────────────────────────


def _version_gt(a: str, b: str) -> bool:
    """True if semver ``a`` is strictly greater than ``b``.

    Strips pre-release / build suffixes (``-``, ``+``) before comparing
    so ``"1.0.0-dev"`` sorts equal to ``"1.0.0"`` (not greater).
    """
    def _parts(v: str) -> tuple[int, ...]:
        base = v.split("-")[0].split("+")[0]
        try:
            return tuple(int(x) for x in base.split("."))
        except ValueError:
            return (0,)

    return _parts(a) > _parts(b)


# ─── GET /api/v1/edge/sync ───────────────────────────────────────────────


@router.get("/sync")
async def edge_sync(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session_dep)],
) -> JSONResponse:
    """Return current ``sync_version`` and optional update metadata.

    The caller's ``X-Edge-Version`` is compared against the latest active
    release. If a newer release exists, ``updates.available=true`` and
    the full metadata is returned. Otherwise ``updates.available=false``.

    See ``YVY_Core_Spec_Canal_Pull.md`` §4 for the full response contract.
    """
    station, _ = await _verify_edge_hmac(request, session)

    x_edge_version = request.headers.get("x-edge-version", "0.0.0")
    x_last_sync = request.headers.get("x-last-sync-version", "0")

    edge_release_repo: EdgeReleaseRepository = request.app.state.edge_release_repo
    release = await edge_release_repo.get_latest_active(session)
    sync_version = await edge_release_repo.get_current_sync_version(session)

    if release is not None and _version_gt(release.edge_version, x_edge_version):
        updates: dict = {
            "available": True,
            "edge_version": release.edge_version,
            "package_url": f"/api/v1/edge/updates/{release.package_filename}",
            "package_sha256": release.package_sha256,
            "package_size_bytes": release.package_size_bytes,
            "released_at": release.released_at.isoformat(),
            "min_edge_version": release.min_edge_version,
        }
        if release.notes:
            updates["notes"] = release.notes

        _log.info(
            "pull.response.update_available",
            extra={
                "station_uuid": str(station.station_uuid),
                "update_version_offered": release.edge_version,
                "sync_version_returned": sync_version,
                "edge_current_version": x_edge_version,
                "last_sync_version": x_last_sync,
            },
        )
    else:
        updates = {"available": False}
        _log.info(
            "pull.response.no_changes",
            extra={
                "station_uuid": str(station.station_uuid),
                "sync_version_returned": sync_version,
                "edge_current_version": x_edge_version,
            },
        )

    return JSONResponse(content={"sync_version": sync_version, "updates": updates})


# ─── GET /api/v1/edge/updates/{filename} ────────────────────────────────


@router.get("/updates/{filename}")
async def edge_download(
    filename: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session_dep)],
) -> FileResponse:
    """Stream a published ``.tar.gz`` package to the edge.

    ``filename`` must match the ``package_filename`` of a registered
    active release. No path traversal is permitted.

    Returns ``Content-Length`` and ``X-Package-SHA256`` so the edge can
    verify integrity without reading the whole file first.
    """
    station, _ = await _verify_edge_hmac(request, session)

    # Guard against path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Invalid filename", "error_class": "SCHEMA"},
        )

    # Resolve release by filename
    result = await session.execute(
        select(EdgeReleaseORM)
        .where(
            EdgeReleaseORM.package_filename == filename,
            EdgeReleaseORM.is_active.is_(True),
        )
        .limit(1)
    )
    release = result.scalar_one_or_none()
    if release is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": f"No active release for filename={filename!r}", "error_class": "SCHEMA"},
        )

    releases_dir = Path(request.app.state.settings.releases_dir)
    file_path = releases_dir / filename
    if not file_path.is_file():
        _log.error(
            "update.download.failed: file missing on disk",
            extra={
                "station_uuid": str(station.station_uuid),
                "version": release.edge_version,
                "package_filename": filename,
                "path": str(file_path),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": f"Package file not found on disk: {filename}", "error_class": "SERVER"},
        )

    _log.info(
        "update.downloaded",
        extra={
            "station_uuid": str(station.station_uuid),
            "version": release.edge_version,
            "package_filename": filename,
            "bytes": release.package_size_bytes,
        },
    )

    return FileResponse(
        path=str(file_path),
        media_type="application/octet-stream",
        filename=filename,
        headers={
            "X-Package-SHA256": release.package_sha256,
            "Cache-Control": "public, immutable, max-age=31536000",
        },
    )
