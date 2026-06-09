"""Use cases for all ingest endpoints (observation, sensor, event).

Common pipeline for all three:

1. Validate envelope (shape + HMAC + clock) via ``EnvelopeValidator``.
2. Validate the payload typing via an injected callable (the handler
   passes a function that runs the Pydantic model for this endpoint).
3. Build ``IngestEvent`` and publish via ``IngestEventPublisher``.
4. Log the attempt to ``bronze.ingest_log``.
5. On post-HMAC payload failure, also record to ``bronze.ingest_failures``
   (the server DLQ).
6. Return an ``IngestOutcome`` that the handler maps to an HTTP response.

The base class ``_BaseIngestUseCase`` implements the full pipeline.
Each endpoint subclass overrides only ``ENDPOINT`` and ``ENTITY_TYPE``.

The use case catches all expected errors internally so the handler stays
thin. Only truly unexpected exceptions (e.g. Postgres down mid-publish)
propagate up as 5xx ``IngestFailure``.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from yvy_server.application.auth.envelope_validator import EnvelopeValidator
from yvy_server.domain.auth import EnvelopeError
from yvy_server.domain.ingest import EntityType, IngestEvent, IngestEventPublisher
from yvy_server.infrastructure.db import SessionFactory
from yvy_server.infrastructure.persistence import (
    IngestFailuresRepository,
    IngestLogRepository,
)

_log = logging.getLogger("yvy.ingest")


# ─── Outcome value objects (discriminated union) ────────────────────────


@dataclass(frozen=True, slots=True)
class IngestSuccess:
    """Successful ingest outcome — maps to HTTP 200."""

    event_id: UUID
    was_duplicate: bool
    received_at_utc: datetime


@dataclass(frozen=True, slots=True)
class IngestFailure:
    """Failed ingest outcome — maps to HTTP 4xx/5xx.

    The presentation layer reads ``http_status`` and ``error_class``
    (matches the constants in ``EnvelopeError`` subclasses) to build
    the response body.
    """

    http_status: int
    error_class: str  # "SCHEMA" | "AUTH" | "CLOCK" | "SERVER"
    error_message: str


IngestOutcome = IngestSuccess | IngestFailure


# ─── Base use case ────────────────────────────────────────────────────────


class _BaseIngestUseCase:
    """Full ingest pipeline. Subclasses set ``ENDPOINT`` and ``ENTITY_TYPE``."""

    ENDPOINT: ClassVar[str]
    ENTITY_TYPE: ClassVar[EntityType]

    def __init__(
        self,
        *,
        validator: EnvelopeValidator,
        publisher: IngestEventPublisher,
        log_repo: IngestLogRepository,
        failures_repo: IngestFailuresRepository,
        session_factory: SessionFactory,
        producer_version: str,
    ) -> None:
        self._validator = validator
        self._publisher = publisher
        self._log_repo = log_repo
        self._failures_repo = failures_repo
        self._session_factory = session_factory
        self._producer_version = producer_version

    async def execute(
        self,
        *,
        envelope: dict[str, Any],
        received_at_utc: datetime,
        received_from_ip: str | None,
        user_agent: str | None,
        payload_validator: Callable[[dict[str, Any]], None] | None = None,
    ) -> IngestOutcome:
        """Run the ingest pipeline.

        Args:
            envelope: Parsed JSON body of the request. Must be a dict.
            received_at_utc: When the handler received the request
                (timezone-aware UTC). Decision 8 — computed once,
                used everywhere.
            received_from_ip: Source IP, ``None`` if not resolvable.
            user_agent: ``User-Agent`` HTTP header, ``None`` if absent.
            payload_validator: Optional callable to validate the
                envelope's ``payload`` field after HMAC. If it raises
                any exception, the failure is recorded in
                ``bronze.ingest_failures`` and the outcome is
                ``IngestFailure(http_status=400, error_class="SCHEMA")``.
                The callable receives ``envelope["payload"]``.

        Returns:
            ``IngestSuccess`` if persisted, ``IngestFailure`` otherwise.
            Never raises (all expected exceptions are translated;
            unexpected ones become ``IngestFailure(500, "SERVER", ...)``).
        """
        # ─── 1. Envelope validation (shape + HMAC + clock) ──────────────
        try:
            async with self._session_factory() as session:
                validated = await self._validator.validate(
                    session, envelope, received_at_utc
                )
        except EnvelopeError as err:
            await self._log_attempt(
                received_at_utc=received_at_utc,
                station_uuid=err.station_uuid,
                http_status=type(err).http_status,
                failure_class=type(err).error_class,
                event_id=err.event_id,
                skew_seconds=err.skew_seconds,
                request_ip=received_from_ip,
                user_agent=user_agent,
            )
            _log.warning(
                "envelope validation failed",
                extra={
                    "error_class": type(err).error_class,
                    "station_uuid": (
                        str(err.station_uuid) if err.station_uuid else None
                    ),
                    "event_id": str(err.event_id) if err.event_id else None,
                    "skew_seconds": err.skew_seconds,
                    "endpoint": self.ENDPOINT,
                },
            )
            return IngestFailure(
                http_status=type(err).http_status,
                error_class=type(err).error_class,
                error_message=str(err),
            )

        # ─── 2. Payload typing (post-HMAC) ──────────────────────────────
        if payload_validator is not None:
            try:
                payload_validator(envelope["payload"])
            except Exception as err:  # noqa: BLE001 — we capture anything Pydantic raises
                # Record in DLQ + log
                async with self._session_factory() as session, session.begin():
                    await self._failures_repo.record_failure(
                        session,
                        event_id=UUID(envelope["event_id"]),
                        station_uuid=validated.station.station_uuid,
                        entity_type=self.ENTITY_TYPE.value,
                        endpoint=self.ENDPOINT,
                        failure_class="SCHEMA",
                        failure_reason=str(err),
                        raw_envelope=envelope,
                        received_at=received_at_utc,
                        received_from_ip=received_from_ip,
                    )
                await self._log_attempt(
                    received_at_utc=received_at_utc,
                    station_uuid=validated.station.station_uuid,
                    http_status=400,
                    failure_class="SCHEMA",
                    event_id=UUID(envelope["event_id"]),
                    skew_seconds=validated.skew_seconds,
                    request_ip=received_from_ip,
                    user_agent=user_agent,
                )
                _log.warning(
                    "payload validation failed (post-HMAC, recorded to DLQ)",
                    extra={
                        "error_class": "SCHEMA",
                        "station_uuid": str(validated.station.station_uuid),
                        "event_id": envelope["event_id"],
                        "endpoint": self.ENDPOINT,
                    },
                )
                return IngestFailure(
                    http_status=400,
                    error_class="SCHEMA",
                    error_message=f"Payload validation failed: {err}",
                )

        # ─── 3. Build IngestEvent + publish ─────────────────────────────
        try:
            event = IngestEvent.from_envelope(
                envelope=envelope,
                entity_type=self.ENTITY_TYPE,
                received_at_utc=received_at_utc,
                received_from_ip=received_from_ip,
                producer_version=self._producer_version,
            )
            publish_result = await self._publisher.publish(event)
        except Exception:  # noqa: BLE001 — any publish failure is SERVER
            _log.exception(
                "publish failed",
                extra={
                    "station_uuid": str(validated.station.station_uuid),
                    "event_id": envelope["event_id"],
                    "endpoint": self.ENDPOINT,
                },
            )
            await self._log_attempt(
                received_at_utc=received_at_utc,
                station_uuid=validated.station.station_uuid,
                http_status=500,
                failure_class="SERVER",
                event_id=UUID(envelope["event_id"]),
                skew_seconds=validated.skew_seconds,
                request_ip=received_from_ip,
                user_agent=user_agent,
            )
            return IngestFailure(
                http_status=500,
                error_class="SERVER",
                error_message="Internal server error during publish",
            )

        # ─── 4. Log success ─────────────────────────────────────────────
        await self._log_attempt(
            received_at_utc=received_at_utc,
            station_uuid=validated.station.station_uuid,
            http_status=200,
            failure_class=None,
            event_id=event.event_id,
            skew_seconds=validated.skew_seconds,
            request_ip=received_from_ip,
            user_agent=user_agent,
        )
        _log.info(
            "envelope persisted",
            extra={
                "station_uuid": str(event.station_uuid),
                "event_id": str(event.event_id),
                "entity_type": self.ENTITY_TYPE.value,
                "was_duplicate": publish_result.was_duplicate,
                "topic": publish_result.topic,
                "transport": publish_result.transport,
                "skew_seconds": validated.skew_seconds,
            },
        )

        return IngestSuccess(
            event_id=event.event_id,
            was_duplicate=publish_result.was_duplicate,
            received_at_utc=received_at_utc,
        )

    async def _log_attempt(
        self,
        *,
        received_at_utc: datetime,
        station_uuid: UUID | None,
        http_status: int,
        failure_class: str | None,
        event_id: UUID | None,
        skew_seconds: int | None,
        request_ip: str | None,
        user_agent: str | None,
    ) -> None:
        """Internal helper: open a session, write to ingest_log."""
        async with self._session_factory() as session, session.begin():
            await self._log_repo.record_attempt(
                session,
                received_at=received_at_utc,
                station_uuid=station_uuid,
                endpoint=self.ENDPOINT,
                http_status=http_status,
                failure_class=failure_class,
                event_id=event_id,
                skew_seconds=skew_seconds,
                request_ip=request_ip,
                user_agent=user_agent,
            )


# ─── Concrete use cases (one per ingest endpoint) ────────────────────────


class IngestObservationUseCase(_BaseIngestUseCase):
    """Ingest use case for ``POST /api/v1/ingest/observation``."""

    ENDPOINT = "/api/v1/ingest/observation"
    ENTITY_TYPE = EntityType.EMPIRICAL_OBSERVATION


class IngestSensorUseCase(_BaseIngestUseCase):
    """Ingest use case for ``POST /api/v1/ingest/sensor``."""

    ENDPOINT = "/api/v1/ingest/sensor"
    ENTITY_TYPE = EntityType.SENSOR_READING


class IngestEventUseCase(_BaseIngestUseCase):
    """Ingest use case for ``POST /api/v1/ingest/event``."""

    ENDPOINT = "/api/v1/ingest/event"
    ENTITY_TYPE = EntityType.EMPIRICAL_EVENT
