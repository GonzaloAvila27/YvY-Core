"""Admin endpoints with bearer token auth.

In 9.1 only ``/admin/healthz`` is implemented. The richer endpoints
``/admin/stats`` and ``/admin/failures`` (including DLQ resolution
actions) land in 9.2.

All routes in this router require ``verify_admin_token`` via the
router-level ``dependencies=[...]`` argument. No per-endpoint repetition.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yvy_server.infrastructure.db import (
    IngestFailureORM,
    IngestLogORM,
    RawRecordORM,
    StationORM,
)
from yvy_server.infrastructure.persistence import (
    IngestFailuresRepository,
    IngestLogRepository,
)
from yvy_server.presentation.api.dependencies import (
    get_session_dep,
    verify_admin_token,
)
from yvy_server.presentation.api.schemas import (
    AdminFailureItem,
    AdminFailuresResponse,
    AdminHealthzResponse,
    AdminResolveRequest,
    AdminResolveResponse,
    AdminStatsResponse,
)

_log = logging.getLogger("yvy.admin")

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
    dependencies=[Depends(verify_admin_token)],
)


# Mapping (label → ORM model) for the row-count query. Ordered for stable
# output across calls and across server instances.
_TABLES_TO_COUNT: list[tuple[str, type]] = [
    ("station", StationORM),
    ("bronze.raw_record", RawRecordORM),
    ("bronze.ingest_log", IngestLogORM),
    ("bronze.ingest_failures", IngestFailureORM),
]


@router.get("/healthz", response_model=AdminHealthzResponse)
async def admin_healthz(
    session: Annotated[AsyncSession, Depends(get_session_dep)],
) -> AdminHealthzResponse:
    """DB-aware health check.

    Counts rows in the main tables. Used to validate end-to-end
    connectivity (auth → handler → DB query → response). If the DB is
    unresponsive, ``db_responsive=False`` and ``counts={}``.
    """
    counts: dict[str, int] = {}
    db_responsive = True

    try:
        for label, orm in _TABLES_TO_COUNT:
            stmt = select(func.count()).select_from(orm)
            result = await session.execute(stmt)
            counts[label] = result.scalar_one()
    except Exception:
        _log.exception("admin_healthz: DB query failed")
        db_responsive = False
        counts = {}

    return AdminHealthzResponse(
        status="ok",
        db_responsive=db_responsive,
        counts=counts,
    )


# ─── GET /api/v1/admin/stats ─────────────────────────────────────────────


@router.get("/stats", response_model=AdminStatsResponse)
async def admin_stats(
    session: Annotated[AsyncSession, Depends(get_session_dep)],
) -> AdminStatsResponse:
    """Ingest statistics snapshot (all-time counts from ``bronze.ingest_log``)."""
    stats = await IngestLogRepository().get_stats(session)
    return AdminStatsResponse(
        total_attempts=stats.total_attempts,
        total_successes=stats.total_successes,
        total_failures=stats.total_failures,
        failures_by_class=stats.failures_by_class,
        attempts_by_endpoint=stats.attempts_by_endpoint,
    )


# ─── GET /api/v1/admin/failures ──────────────────────────────────────────


@router.get("/failures", response_model=AdminFailuresResponse)
async def admin_failures(
    session: Annotated[AsyncSession, Depends(get_session_dep)],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> AdminFailuresResponse:
    """Paginated list of unresolved DLQ entries from ``bronze.ingest_failures``."""
    repo = IngestFailuresRepository()
    rows = await repo.list_unresolved(session, limit=limit, offset=offset)

    # Count total unresolved for the pagination envelope.
    total_result = await session.execute(
        select(func.count()).select_from(IngestFailureORM).where(
            IngestFailureORM.resolved_at.is_(None)
        )
    )
    total = int(total_result.scalar_one() or 0)

    items = [
        AdminFailureItem(
            id=row.id,
            event_id=row.event_id,
            station_uuid=row.station_uuid,
            entity_type=row.entity_type,
            endpoint=row.endpoint,
            failure_class=row.failure_class,
            failure_reason=row.failure_reason,
            received_at=row.received_at,
            received_from_ip=row.received_from_ip,
            raw_envelope=row.raw_envelope,
        )
        for row in rows
    ]
    return AdminFailuresResponse(total=total, items=items)


# ─── POST /api/v1/admin/failures/{failure_id}/resolve ───────────────────


@router.post("/failures/{failure_id}/resolve", response_model=AdminResolveResponse)
async def resolve_failure(
    failure_id: int,
    body: AdminResolveRequest,
    session: Annotated[AsyncSession, Depends(get_session_dep)],
) -> AdminResolveResponse:
    """Mark a DLQ entry as resolved with an action and optional notes."""
    async with session.begin():
        resolved = await IngestFailuresRepository().resolve(
            session,
            failure_id,
            resolved_action=body.action,
            resolution_notes=body.notes,
            resolved_at=_dt.datetime.now(_dt.UTC),
        )

    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": f"failure_id={failure_id} not found or already resolved",
                "error_class": "SCHEMA",
            },
        )

    return AdminResolveResponse(resolved=True, failure_id=failure_id)
