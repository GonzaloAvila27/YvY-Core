"""Public read API — /api/v1/public/*

No authentication. Read-only. Served from api.yvyedge.com.
Cache headers per DATA-CONTRACT.md (C4): public, max-age=30, swr=60.

Three endpoints:
  GET /api/v1/public/network          — network KPIs (v_network_status)
  GET /api/v1/public/stations         — all station summaries
  GET /api/v1/public/stations/{id}    — single station by UUID or code
"""
from __future__ import annotations

import logging
from datetime import UTC
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from yvy_server.domain.public.models import LastValues, NetworkStatus, StationSummary
from yvy_server.infrastructure.persistence import PublicReadRepository
from yvy_server.presentation.api.dependencies import get_session_dep
from yvy_server.presentation.api.schemas_public import (
    CoordinatesSchema,
    LastValuesSchema,
    NetworkStatusResponse,
    StationSchema,
    StationsResponse,
)

_log = logging.getLogger("yvy.public")

router = APIRouter(prefix="/api/v1/public", tags=["public"])

_CACHE_SHORT = "public, max-age=30, stale-while-revalidate=60"

SessionDep = Annotated[AsyncSession, Depends(get_session_dep)]


# ─── Helpers ────────────────────────────────────────────────────────────


def _to_station_schema(s: StationSummary) -> StationSchema:
    coords = (
        CoordinatesSchema(lat=s.latitude, lng=s.longitude)
        if s.latitude is not None and s.longitude is not None
        else None
    )
    lv: LastValuesSchema | None = None
    if s.last_values is not None:
        v: LastValues = s.last_values
        lv = LastValuesSchema(
            temp=v.temp,
            humidity=v.humidity,
            pressure=v.pressure,
            windSpeed=v.wind_speed,
            windGust=v.wind_gust,
            windDirection=v.wind_direction,
            timestamp=v.timestamp,
        )
    return StationSchema(
        id=s.station_uuid,
        code=s.code,
        name=s.name,
        locality=s.locality,
        province=s.province,
        coordinates=coords,
        status=s.status,
        lastSyncAt=s.last_sync_at,
        lastValues=lv,
    )


def _network_response(ns: NetworkStatus) -> JSONResponse:
    body = NetworkStatusResponse(
        totalNodes=ns.total_nodes,
        activeNodes=ns.active_nodes,
        offlineNodes=ns.offline_nodes,
        pendingNodes=ns.pending_nodes,
        totalRecords=ns.total_records,
        localitiesCount=ns.localities_count,
        lastSyncSeconds=ns.last_sync_seconds,
        uptime30d=ns.uptime_30d,
        generatedAt=ns.generated_at,
    )
    return JSONResponse(
        content=body.model_dump(mode="json"),
        headers={"Cache-Control": _CACHE_SHORT, "X-Schema-Version": "1.0"},
    )


# ─── Endpoints ──────────────────────────────────────────────────────────


@router.get("/network")
async def get_network(request: Request, session: SessionDep) -> JSONResponse:
    """Network-wide KPIs from v_network_status."""
    repo: PublicReadRepository = request.app.state.public_read_repo
    ns = await repo.get_network_status(session)
    _log.info(
        "public.network",
        extra={"active_nodes": ns.active_nodes, "total_records": ns.total_records},
    )
    return _network_response(ns)


@router.get("/stations")
async def get_stations(request: Request, session: SessionDep) -> JSONResponse:
    """All station summaries from v_station_summary."""
    repo: PublicReadRepository = request.app.state.public_read_repo
    summaries = await repo.get_station_summaries(session)
    from datetime import datetime
    body = StationsResponse(
        stations=[_to_station_schema(s) for s in summaries],
        generatedAt=datetime.now(UTC),
    )
    return JSONResponse(
        content=body.model_dump(mode="json"),
        headers={"Cache-Control": _CACHE_SHORT, "X-Schema-Version": "1.0"},
    )


@router.get("/stations/{station_id}")
async def get_station(
    station_id: str, request: Request, session: SessionDep
) -> JSONResponse:
    """Single station by UUID or code (e.g. 'IEA-07')."""
    repo: PublicReadRepository = request.app.state.public_read_repo

    summary: StationSummary | None = None
    try:
        uuid = UUID(station_id)
        summary = await repo.get_station_by_uuid(session, uuid)
    except ValueError:
        # Not a UUID — try as code
        summary = await repo.get_station_by_code(session, station_id)

    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": f"Station {station_id!r} not found", "error_class": "NOT_FOUND"},
        )

    from datetime import datetime
    body = StationsResponse(
        stations=[_to_station_schema(summary)],
        generatedAt=datetime.now(UTC),
    )
    return JSONResponse(
        content=body.model_dump(mode="json"),
        headers={"Cache-Control": _CACHE_SHORT, "X-Schema-Version": "1.0"},
    )
