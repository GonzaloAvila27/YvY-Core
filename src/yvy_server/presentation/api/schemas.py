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
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


# ─── Sensor reading payload (POST /api/v1/ingest/sensor) ────────────────


class SensorReadingPayload(BaseModel):
    """Payload of ``POST /api/v1/ingest/sensor``.

    Fields mirror the pywws output from ``capture_live_reading`` in the
    Edge (see ``YVY_IEAProject/Fase_0/yvy/src/yvy/application/use_cases/
    sensor/capture_live_reading.py#_build_sync_payload``).

    Numeric readings arrive as strings from pywws (Decimal → str).
    ``extra="allow"`` for forward compatibility.
    """

    model_config = ConfigDict(extra="allow")

    # Required — observation timestamp from the sensor clock
    observed_at: str  # ISO 8601 string from pywws
    event_id: str  # UUID string (v5 deterministic)

    # Sensor mode (LIVE / CATCHUP)
    pywws_mode: str | None = None

    # Temperature (strings — pywws outputs Decimal)
    temp_indoor: str | None = None
    temp_outdoor: str | None = None

    # Humidity (ints)
    hum_indoor: int | None = None
    hum_outdoor: int | None = None

    # Pressure (strings — Decimal)
    pressure_abs: str | None = None
    pressure_rel: str | None = None

    # Wind (strings for speed/gust, int for direction bearing)
    wind_speed: str | None = None
    wind_gust: str | None = None
    wind_dir: int | None = None

    # Rain accumulations (strings — Decimal)
    rain_1h: str | None = None
    rain_24h: str | None = None
    rain_total: str | None = None

    # UV / solar
    uv_index: int | None = None
    solar_radiation: str | None = None

    # QA flags
    is_valid: bool | None = None
    validation_notes: str | None = None


# ─── Empirical event payload (POST /api/v1/ingest/event) ────────────────


class EmpiricalEventPayload(BaseModel):
    """Payload of ``POST /api/v1/ingest/event``.

    Represents a point-in-time meteorological event (frost, hail, strong
    wind, etc.) recorded by a student observer. Fields mirror
    ``EmpiricalEvent`` in the Edge domain.

    ``extra="allow"`` for forward compatibility.
    """

    model_config = ConfigDict(extra="allow")

    # Required
    event_record_id: str  # UUID v4 string
    observation_date: str  # YYYY-MM-DD
    event_type: str  # matches EventType enum values on Edge

    # Optional context
    observation_id: str | None = None  # UUID of linked observation
    started_at_local: str | None = None  # HH:MM
    ended_at_local: str | None = None  # HH:MM
    hail_size: str | None = None
    description: str | None = None

    # QA flags
    is_valid: bool | None = None
    created_by_user_id: int | None = None


# ─── Admin stats response (GET /api/v1/admin/stats) ─────────────────────


class AdminStatsResponse(BaseModel):
    """``GET /api/v1/admin/stats`` — ingest statistics snapshot."""

    total_attempts: int
    total_successes: int
    total_failures: int
    failures_by_class: dict[str, int]
    attempts_by_endpoint: dict[str, int]


# ─── Admin failures responses (GET /api/v1/admin/failures) ──────────────


class AdminFailureItem(BaseModel):
    """Single DLQ entry as returned in the failures list."""

    id: int
    event_id: UUID | None
    station_uuid: UUID | None
    entity_type: str | None
    endpoint: str
    failure_class: str
    failure_reason: str
    received_at: datetime
    received_from_ip: str | None
    # Expose the raw envelope so the admin can inspect it
    raw_envelope: dict[str, Any]

    @field_validator("received_from_ip", mode="before")
    @classmethod
    def _coerce_ip(cls, v: object) -> str | None:
        # asyncpg returns INET columns as IPv4Address/IPv6Address objects
        return None if v is None else str(v)


class AdminFailuresResponse(BaseModel):
    """``GET /api/v1/admin/failures`` — paginated unresolved DLQ entries."""

    total: int
    items: list[AdminFailureItem]


class AdminResolveRequest(BaseModel):
    """Body of ``POST /api/v1/admin/failures/{id}/resolve``."""

    action: Literal["reprocessed", "discarded", "fixed_at_source"]
    notes: str | None = Field(default=None, max_length=1000)


class AdminResolveResponse(BaseModel):
    """Response of ``POST /api/v1/admin/failures/{id}/resolve``."""

    resolved: bool
    failure_id: int


# ─── Edge sync response (GET /api/v1/edge/sync) ──────────────────────────


class EdgeUpdateMetadata(BaseModel):
    """Metadata block included when an update is available."""

    edge_version: str
    package_url: str
    package_sha256: str
    package_size_bytes: int
    released_at: datetime
    min_edge_version: str
    notes: str | None = None


class EdgeSyncResponse(BaseModel):
    """``GET /api/v1/edge/sync`` — current sync state for this edge node.

    If ``updates.available`` is ``False``, only the ``available`` field
    is present under ``updates``. If ``True``, all fields of
    ``EdgeUpdateMetadata`` are included.
    """

    sync_version: int
    updates: dict[str, Any]
