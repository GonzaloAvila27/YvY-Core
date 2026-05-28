"""Ingest log repository — write-only append to ``bronze.ingest_log``.

Every ingest attempt is logged here, whether it succeeded or failed.
This is the audit trail the operator queries to understand traffic and
debug issues.

Schema notes
------------

- ``station_uuid`` is nullable because some failures (notably AUTH
  pre-identification) happen before we can identify the station.
- ``failure_class`` is NULL for successful attempts (``http_status=200``)
  and non-NULL for failures. The partial index
  ``idx_ingest_log_failures`` (``WHERE failure_class IS NOT NULL``)
  makes admin queries over failures cheap.
- This is append-only; we never UPDATE existing rows.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy.ext.asyncio import AsyncSession

from yvy_server.infrastructure.db import IngestLogORM


class IngestLogRepository:
    """Append-only writes to ``bronze.ingest_log``."""

    async def record_attempt(
        self,
        session: AsyncSession,
        *,
        received_at: datetime,
        station_uuid: PyUUID | None,
        endpoint: str,
        http_status: int,
        failure_class: str | None,
        event_id: PyUUID | None,
        skew_seconds: int | None,
        request_ip: str | None,
        user_agent: str | None,
    ) -> None:
        """Record one ingest attempt (success or failure).

        All args are kw-only because the call site is a handler with
        many similarly-typed fields (multiple UUIDs, multiple strings) —
        positional args would be error-prone.

        Args:
            session: Active ``AsyncSession``.
            received_at: When the handler received the request.
                Timezone-aware UTC.
            station_uuid: Identified station, or ``None`` if AUTH failed
                before identification.
            endpoint: HTTP path (e.g. ``"/api/v1/ingest/observation"``).
            http_status: Final HTTP status returned to the client.
            failure_class: ``"SCHEMA"`` | ``"AUTH"`` | ``"CLOCK"`` |
                ``"SERVER"`` | ``None`` (success). Matches
                ``EnvelopeError.error_class``.
            event_id: From the envelope, if we got that far.
            skew_seconds: Computed skew for CLOCK failures; otherwise
                ``None`` (or could be set to the actual measured skew
                even on success for stats).
            request_ip: ``request.client.host``, or ``None`` if not
                resolvable.
            user_agent: ``User-Agent`` HTTP header, or ``None``.
        """
        session.add(
            IngestLogORM(
                received_at=received_at,
                station_uuid=station_uuid,
                endpoint=endpoint,
                http_status=http_status,
                failure_class=failure_class,
                event_id=event_id,
                skew_seconds=skew_seconds,
                request_ip=request_ip,
                user_agent=user_agent,
            )
        )
