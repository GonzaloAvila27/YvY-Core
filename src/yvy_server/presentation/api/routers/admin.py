"""Admin endpoints with bearer token auth.

In 9.1 only ``/admin/healthz`` is implemented. The richer endpoints
``/admin/stats`` and ``/admin/failures`` (including DLQ resolution
actions) land in 9.2.

All routes in this router require ``verify_admin_token`` via the
router-level ``dependencies=[...]`` argument. No per-endpoint repetition.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yvy_server.infrastructure.db import (
    EmpiricalEventRawORM,
    EmpiricalObservationRawORM,
    IngestFailureORM,
    IngestLogORM,
    SensorRawORM,
    StationORM,
)
from yvy_server.presentation.api.dependencies import (
    get_session_dep,
    verify_admin_token,
)
from yvy_server.presentation.api.schemas import AdminHealthzResponse

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
    ("bronze.sensor_raw", SensorRawORM),
    ("bronze.empirical_observation_raw", EmpiricalObservationRawORM),
    ("bronze.empirical_event_raw", EmpiricalEventRawORM),
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
    except Exception:  # noqa: BLE001 — we want any DB error to set the flag
        _log.exception("admin_healthz: DB query failed")
        db_responsive = False
        counts = {}

    return AdminHealthzResponse(
        status="ok",
        db_responsive=db_responsive,
        counts=counts,
    )
