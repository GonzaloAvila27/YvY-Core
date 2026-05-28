"""Health and server info endpoints.

Both endpoints are unauthenticated. ``/healthz`` is a pure liveness
probe (no DB access), while ``/serverinfo`` returns server identity
metadata that the edge agent reads for compatibility checks.
"""
from __future__ import annotations

import datetime as _dt
from typing import Annotated

from fastapi import APIRouter, Depends

from yvy_server import __build_date__, __git_commit_short__, __version__
from yvy_server.config import Settings
from yvy_server.presentation.api.dependencies import get_settings_dep
from yvy_server.presentation.api.schemas import (
    HealthzResponse,
    ServerInfoResponse,
)

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/healthz", response_model=HealthzResponse)
async def healthz() -> HealthzResponse:
    """Liveness probe.

    Returns ``{"status": "ok"}``. Does NOT touch the database — that's
    the role of ``/admin/healthz``. This endpoint is what a load
    balancer or systemd healthcheck would hit.
    """
    return HealthzResponse(status="ok")


@router.get("/serverinfo", response_model=ServerInfoResponse)
async def serverinfo(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ServerInfoResponse:
    """Server identity + capabilities.

    Read by the edge agent on first contact and after schema-version
    failures to know which versions the server accepts.
    """
    return ServerInfoResponse(
        version=__version__,
        git_commit_short=__git_commit_short__,
        build_date=__build_date__,
        server_time_utc=_dt.datetime.now(_dt.UTC),
        schema_versions_supported=sorted(settings.supported_schemas),
    )
