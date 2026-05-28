"""Pydantic schemas — payload validation + response models.

The envelope itself is NOT validated as a Pydantic model in presentation.
Reason: signature verification requires the canonical bytes of the
envelope-as-received (strings, not coerced types). Validating the
envelope with Pydantic BEFORE HMAC would re-serialize and break the
signature. See decision 5 of step 4.

What IS validated with Pydantic:

- The ``payload`` field of the envelope, AFTER HMAC succeeds. Each
  endpoint passes its specific payload model as a callable to the use
  case (``payload_validator``). If validation fails post-HMAC, the
  failure goes to ``bronze.ingest_failures`` (DLQ) per the use case
  contract.

- The response bodies. These are emitted via FastAPI's serializer or
  ``JSONResponse(content=Model(...).model_dump())``.

Payload models match the schema declared by the edge for
``schema_version="1.0"`` (see ``YVY_FASE9_Arquitectura_v2.md`` §2.4 and
the Fase 8.5a empirical observation form).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ─── Payloads (post-HMAC validation, schema_version=1.0) ────────────────


class EmpiricalObservationPayload(BaseModel):
    """Payload of ``POST /api/v1/ingest/observation``.

    Validated AFTER HMAC verification. Failures here go to
    ``bronze.ingest_failures`` (post-HMAC DLQ).

    Fields mirror the Fase 8.5a empirical observation form. All sensor-
    style measurements are optional (the form allows missing values with
    a "are you sure?" warning in the UI, not hard rejection).

    ``extra="allow"`` is intentional: per v2 §2.6, the server tolerates
    unknown fields for forward compatibility — when the edge adds new
    fields in a future schema_version, old server versions don't reject
    them outright.
    """

    model_config = ConfigDict(extra="allow")

    # Required
    observed_at_utc: datetime
    observer_id: str = Field(min_length=1, max_length=100)

    # Quantitative (all optional — Fase 8.5a decision)
    rain_mm: int | None = Field(default=None, ge=0)
    temp_c_out: float | None = None
    hum_pct_perceived: int | None = Field(default=None, ge=0, le=100)
    wind_beaufort: int | None = Field(default=None, ge=0, le=12)
    wind_dir_8pt: str | None = None

    # Qualitative
    cloud_cover_pct: int | None = Field(default=None, ge=0, le=100)
    cloud_type: str | None = None
    soil_state: str | None = None
    visibility: str | None = None
    precipitation_now: str | None = None
    phenology_notes: str | None = None
    cattle_state: str | None = None
    cattle_notes: str | None = None
    general_notes: str | None = None


# ─── Response models ────────────────────────────────────────────────────


class HealthzResponse(BaseModel):
    """``GET /api/v1/healthz`` — minimal liveness response."""

    status: Literal["ok"] = "ok"


class ServerInfoResponse(BaseModel):
    """``GET /api/v1/serverinfo`` — server identity and supported schemas."""

    version: str
    git_commit_short: str
    build_date: str
    server_time_utc: datetime
    schema_versions_supported: list[str]


class AdminHealthzResponse(BaseModel):
    """``GET /api/v1/admin/healthz`` — DB connectivity + row counts."""

    status: Literal["ok"] = "ok"
    db_responsive: bool
    counts: dict[str, int]


class IngestSuccessResponse(BaseModel):
    """200 response of ``POST /api/v1/ingest/observation``.

    ``ingest_result`` distinguishes new vs duplicate idempotent submissions.
    The edge agent treats both as "sent" — the duplicate path is hit when
    the edge retried after a network blip on a successful submission.
    """

    ingest_result: Literal["inserted", "duplicate"]
    event_id: UUID
    received_at_utc: datetime


class IngestErrorResponse(BaseModel):
    """4xx/5xx response body for ingest failures.

    The ``error_class`` matches the codes in the architecture doc §2.5:
    the edge agent uses it to decide retry behavior (SCHEMA → DLQ
    immediately, AUTH → latch, CLOCK → recalibrate, SERVER → backoff).
    """

    error: str
    error_class: Literal["SCHEMA", "AUTH", "CLOCK", "SERVER"]
