"""Ingest failures repository — server-side DLQ for post-HMAC failures.

When an envelope passes HMAC verification but the payload then fails
the typed Pydantic validation (or any other post-HMAC SCHEMA issue),
the full raw envelope is written here for forensic inspection.

This is the server equivalent of the Kafka topic ``yvy.dlq`` in Track B.

Why store the full envelope?
- Lets the admin replay it after fixing the payload bug.
- Lets the admin understand what the edge actually sent (not what the
  server *thought* it sent).
- ``raw_envelope`` is JSONB so we can query into it from psql.

Resolution workflow (admin actions out of scope for 9.1)
--------------------------------------------------------

Once a row is here, an admin can later set ``resolved_at`` and
``resolved_action`` (CHECK constrained to ``'reprocessed' |
'discarded' | 'fixed_at_source'``). The partial index
``idx_failures_unresolved`` makes "show me unresolved failures"
queries cheap.

Repository scope
----------------

For 9.1 this repo only exposes ``record_failure``. UPDATE operations
for resolution come in 9.2 with the ``/admin/failures`` endpoints.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID as PyUUID

from sqlalchemy.ext.asyncio import AsyncSession

from yvy_server.infrastructure.db import IngestFailureORM


class IngestFailuresRepository:
    """Write-only inserts to ``bronze.ingest_failures``."""

    async def record_failure(
        self,
        session: AsyncSession,
        *,
        event_id: PyUUID | None,
        station_uuid: PyUUID | None,
        entity_type: str | None,
        endpoint: str,
        failure_class: str,
        failure_reason: str,
        raw_envelope: dict[str, Any],
        received_at: datetime,
        received_from_ip: str | None,
    ) -> None:
        """Persist a post-HMAC ingest failure for later admin review.

        Args:
            session: Active ``AsyncSession``.
            event_id: From the envelope (HMAC passed, so we trust it).
                May be ``None`` if even the envelope shape was malformed
                enough that we couldn't extract it — though typically
                shape failures happen pre-HMAC and go to ``ingest_log``,
                not here.
            station_uuid: Identified station (HMAC passed → we know).
            entity_type: The endpoint-derived entity type as a string
                (e.g. ``"empirical_observation"``). Stored as TEXT
                because at this point we only know the URL slug, not
                necessarily a valid ``EntityType`` enum value.
            endpoint: HTTP path of the failed request.
            failure_class: ``"SCHEMA"`` typically. Other values possible
                if future failure types are added.
            failure_reason: Human-readable explanation. Pydantic's
                ``ValidationError`` stringified is a reasonable choice.
            raw_envelope: The complete envelope dict as received. JSONB.
            received_at: When the handler received the request.
            received_from_ip: Source IP, or ``None``.
        """
        session.add(
            IngestFailureORM(
                event_id=event_id,
                station_uuid=station_uuid,
                entity_type=entity_type,
                endpoint=endpoint,
                failure_class=failure_class,
                failure_reason=failure_reason,
                raw_envelope=raw_envelope,
                received_at=received_at,
                received_from_ip=received_from_ip,
            )
        )
