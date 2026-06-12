"""Ingest endpoints.

All handlers follow the same thin pattern:
1. Capture ``received_at_utc`` once.
2. Read raw body bytes and parse as JSON dict.
3. Build a ``payload_validator`` closure for the endpoint's Pydantic model.
4. Delegate to the appropriate use case's ``execute()``.
5. Map the ``IngestOutcome`` to an HTTP response.

Every successful ingest response includes an ``X-Server-Sync-Version``
header so edge nodes can trigger the update-check daemon when Core has
published a new release since their last known sync_version (piggyback
mechanism — see ``YVY_Core_Spec_Canal_Pull.md`` §5).

All ingest_log / ingest_failures writes happen inside the use case; the
handler does not touch repos directly.
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from yvy_server.application.use_cases import (
    IngestEventUseCase,
    IngestFailure,
    IngestObservationUseCase,
    IngestSensorUseCase,
    IngestSuccess,
)
from yvy_server.presentation.api.dependencies import (
    get_event_use_case,
    get_observation_use_case,
    get_sensor_use_case,
)
from yvy_server.presentation.api.schemas import (
    EmpiricalEventPayload,
    EmpiricalObservationPayload,
    IngestErrorResponse,
    IngestSuccessResponse,
    SensorReadingPayload,
)

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])

_SYNC_VERSION_HEADER = "X-Server-Sync-Version"


def _sync_version_header(request: Request) -> dict[str, str]:
    """Read cached sync_version from app.state (populated at startup)."""
    sv: int = getattr(request.app.state, "sync_version", 0)
    return {_SYNC_VERSION_HEADER: str(sv)}


# ─── Shared handler logic ─────────────────────────────────────────────────


async def _run_ingest(
    request: Request,
    use_case: IngestObservationUseCase | IngestSensorUseCase | IngestEventUseCase,
    payload_validator_factory: type,
) -> JSONResponse:
    """Common ingest handler body for all three endpoints."""
    received_at_utc = _dt.datetime.now(_dt.UTC)
    extra_headers = _sync_version_header(request)

    raw_bytes = await request.body()
    try:
        envelope = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        return _error_response(
            status_code=400,
            error_class="SCHEMA",
            error_message=f"Body is not valid JSON: {exc.msg}",
            headers=extra_headers,
        )

    if not isinstance(envelope, dict):
        return _error_response(
            status_code=400,
            error_class="SCHEMA",
            error_message="Body must be a JSON object",
            headers=extra_headers,
        )

    def _validate_payload(payload: dict) -> None:
        payload_validator_factory.model_validate(payload)

    outcome = await use_case.execute(
        envelope=envelope,
        received_at_utc=received_at_utc,
        received_from_ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        payload_validator=_validate_payload,
    )

    if isinstance(outcome, IngestSuccess):
        body = IngestSuccessResponse(
            ingest_result="duplicate" if outcome.was_duplicate else "inserted",
            event_id=outcome.event_id,
            received_at_utc=outcome.received_at_utc,
        )
        return JSONResponse(
            status_code=200,
            content=body.model_dump(mode="json"),
            headers=extra_headers,
        )

    return _error_response(
        status_code=outcome.http_status,
        error_class=outcome.error_class,  # type: ignore[arg-type]
        error_message=outcome.error_message,
        headers=extra_headers,
        skew_seconds=outcome.skew_seconds,
    )


# ─── POST /api/v1/ingest/observation ────────────────────────────────────


@router.post(
    "/observation",
    responses={
        200: {"model": IngestSuccessResponse},
        400: {"model": IngestErrorResponse, "description": "Schema error"},
        401: {"model": IngestErrorResponse, "description": "Auth error"},
        422: {"model": IngestErrorResponse, "description": "Clock skew"},
        500: {"model": IngestErrorResponse, "description": "Server error"},
    },
)
async def ingest_observation(
    request: Request,
    use_case: Annotated[
        IngestObservationUseCase, Depends(get_observation_use_case)
    ],
) -> JSONResponse:
    """Ingest an empirical observation envelope (HMAC-signed)."""
    return await _run_ingest(request, use_case, EmpiricalObservationPayload)


# ─── POST /api/v1/ingest/sensor ──────────────────────────────────────────


@router.post(
    "/sensor",
    responses={
        200: {"model": IngestSuccessResponse},
        400: {"model": IngestErrorResponse, "description": "Schema error"},
        401: {"model": IngestErrorResponse, "description": "Auth error"},
        422: {"model": IngestErrorResponse, "description": "Clock skew"},
        500: {"model": IngestErrorResponse, "description": "Server error"},
    },
)
async def ingest_sensor(
    request: Request,
    use_case: Annotated[IngestSensorUseCase, Depends(get_sensor_use_case)],
) -> JSONResponse:
    """Ingest a sensor reading envelope (HMAC-signed)."""
    return await _run_ingest(request, use_case, SensorReadingPayload)


# ─── POST /api/v1/ingest/event ───────────────────────────────────────────


@router.post(
    "/event",
    responses={
        200: {"model": IngestSuccessResponse},
        400: {"model": IngestErrorResponse, "description": "Schema error"},
        401: {"model": IngestErrorResponse, "description": "Auth error"},
        422: {"model": IngestErrorResponse, "description": "Clock skew"},
        500: {"model": IngestErrorResponse, "description": "Server error"},
    },
)
async def ingest_event(
    request: Request,
    use_case: Annotated[IngestEventUseCase, Depends(get_event_use_case)],
) -> JSONResponse:
    """Ingest an empirical event envelope (HMAC-signed)."""
    return await _run_ingest(request, use_case, EmpiricalEventPayload)


# ─── POST /api/v1/ingest/comment (stub 501) ─────────────────────────────


@router.post(
    "/comment",
    responses={
        501: {"model": IngestErrorResponse, "description": "Not implemented"},
    },
)
async def ingest_comment() -> JSONResponse:
    """Stub: comments are not yet accepted.

    Returns ``SCHEMA`` error so the edge treats it as permanent DLQ.
    """
    return _error_response(
        status_code=501,
        error_class="SCHEMA",
        error_message="Endpoint not implemented; scheduled for a future release",
    )


# ─── Helpers ────────────────────────────────────────────────────────────


def _error_response(
    *,
    status_code: int,
    error_class: str,
    error_message: str,
    headers: dict[str, str] | None = None,
    skew_seconds: int | None = None,
) -> JSONResponse:
    body = IngestErrorResponse(
        error=error_message,
        error_class=error_class,  # type: ignore[arg-type]
        skew_seconds=skew_seconds,
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers=headers,
    )
