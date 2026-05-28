"""Ingest endpoints.

In 9.1 only ``/api/v1/ingest/observation`` is fully implemented.
``/api/v1/ingest/comment`` is a 501 stub kept for endpoint matrix
completeness. ``/api/v1/ingest/sensor`` and ``/api/v1/ingest/event``
land in 9.2 with the same handler shape as observation.

Handler shape
-------------

The handlers stay thin. They:

1. Read raw body bytes (NOT a Pydantic envelope — see schemas.py).
2. Parse as JSON dict.
3. Build a ``payload_validator`` closure that runs the endpoint's
   typed Pydantic model.
4. Call the appropriate use case's ``execute(...)``.
5. Map the ``IngestOutcome`` (``IngestSuccess`` or ``IngestFailure``)
   to an HTTP response with status + body.

All ingest_log / ingest_failures persistence happens inside the use
case. The handler does not touch repos directly.
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from yvy_server.application.use_cases import (
    IngestFailure,
    IngestObservationUseCase,
    IngestSuccess,
)
from yvy_server.presentation.api.dependencies import get_observation_use_case
from yvy_server.presentation.api.schemas import (
    EmpiricalObservationPayload,
    IngestErrorResponse,
    IngestSuccessResponse,
)

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])


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
    """Ingest an empirical observation envelope.

    Flow:
        1. Capture ``received_at_utc`` (once, used everywhere — decision 8).
        2. Read raw body + parse JSON.
        3. Reject if body isn't a JSON object.
        4. Delegate to ``IngestObservationUseCase.execute`` with a
           closure that validates the payload via Pydantic.
        5. Map outcome to HTTP response.
    """
    received_at_utc = _dt.datetime.now(_dt.UTC)

    # 1. Read & parse JSON
    raw_bytes = await request.body()
    try:
        envelope = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        return _error_response(
            status_code=400,
            error_class="SCHEMA",
            error_message=f"Body is not valid JSON: {exc.msg}",
        )

    if not isinstance(envelope, dict):
        return _error_response(
            status_code=400,
            error_class="SCHEMA",
            error_message="Body must be a JSON object",
        )

    # 2. Build the Pydantic payload validator closure.
    #    The use case calls it AFTER HMAC verification. If it raises,
    #    the use case records to ingest_failures and returns
    #    IngestFailure(400, "SCHEMA").
    def _validate_payload(payload: dict) -> None:
        EmpiricalObservationPayload.model_validate(payload)

    # 3. Delegate to use case
    outcome = await use_case.execute(
        envelope=envelope,
        received_at_utc=received_at_utc,
        received_from_ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        payload_validator=_validate_payload,
    )

    # 4. Map outcome to HTTP response
    if isinstance(outcome, IngestSuccess):
        body = IngestSuccessResponse(
            ingest_result="duplicate" if outcome.was_duplicate else "inserted",
            event_id=outcome.event_id,
            received_at_utc=outcome.received_at_utc,
        )
        return JSONResponse(status_code=200, content=body.model_dump(mode="json"))

    # IngestFailure
    return _error_response(
        status_code=outcome.http_status,
        error_class=outcome.error_class,  # type: ignore[arg-type]
        error_message=outcome.error_message,
    )


# ─── POST /api/v1/ingest/comment (stub 501) ─────────────────────────────


@router.post(
    "/comment",
    responses={
        501: {"model": IngestErrorResponse, "description": "Not implemented"},
    },
)
async def ingest_comment() -> JSONResponse:
    """Stub: comments aren't accepted in 9.1.

    The endpoint exists so the endpoint matrix is complete and clients
    receive a consistent ``IngestErrorResponse`` shape. ``error_class``
    is set to ``"SCHEMA"`` so the edge agent treats the failure as
    permanent (DLQ), not retryable.

    Will be replaced in 9.2 when the comment ingest is added.
    """
    return _error_response(
        status_code=501,
        error_class="SCHEMA",
        error_message="Endpoint not implemented in 9.1, scheduled for 9.2",
    )


# ─── Helpers ────────────────────────────────────────────────────────────


def _error_response(
    *,
    status_code: int,
    error_class: str,
    error_message: str,
) -> JSONResponse:
    """Build a typed error response uniformly.

    Using ``IngestErrorResponse.model_dump`` ensures the JSON shape
    matches the OpenAPI spec. We pass ``error_class`` as ``str`` and
    rely on Pydantic to validate it against the ``Literal[...]``.
    """
    body = IngestErrorResponse(
        error=error_message,
        error_class=error_class,  # type: ignore[arg-type]
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))
