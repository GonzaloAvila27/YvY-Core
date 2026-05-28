"""Bronze repository — idempotent UPSERT to the three ``bronze.*_raw`` tables.

One method (``upsert``) dispatches to the correct ORM class by
``event.entity_type``. The three target tables share schema (mixin
``_BronzeRawMixin``) so the operation is identical; only the table
changes.

Idempotency
-----------

``INSERT ... ON CONFLICT (event_id) DO NOTHING RETURNING event_id``.

- If the row is new: ``RETURNING`` yields the event_id → ``upsert``
  returns ``True``.
- If ``event_id`` already exists (duplicate retry from a flaky edge):
  ``ON CONFLICT DO NOTHING`` skips the insert, ``RETURNING`` yields
  zero rows → ``upsert`` returns ``False``.

The caller (the ``PostgresDirectPublisher``) reads this bool to set
``PublishResult.was_duplicate``, which the HTTP handler uses to
return ``ingest_result: "duplicate"`` vs ``"inserted"`` per
``YVY_FASE9_Arquitectura_v2.md`` §2.4.

EntityType coverage
-------------------

3 of the 4 entity types map to a bronze table here:

- ``SENSOR_READING`` → ``bronze.sensor_raw``
- ``EMPIRICAL_OBSERVATION`` → ``bronze.empirical_observation_raw``
- ``EMPIRICAL_EVENT`` → ``bronze.empirical_event_raw``

``COMMENT`` does NOT have a bronze table in 9.1 (the endpoint
``/ingest/comment`` is a 501 stub). If an ``IngestEvent`` with
``entity_type=COMMENT`` ever reaches this repo it's an upstream
routing bug — we raise ``NotImplementedError`` to fail loud.
"""
from __future__ import annotations

from typing import ClassVar

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from yvy_server.domain.ingest import EntityType, IngestEvent
from yvy_server.infrastructure.db import (
    EmpiricalEventRawORM,
    EmpiricalObservationRawORM,
    SensorRawORM,
)
from yvy_server.infrastructure.db.models import _BronzeRawMixin


class BronzeRepository:
    """Idempotent UPSERT to ``bronze.*_raw`` tables."""

    # Map EntityType → ORM model. Three entries; ``COMMENT`` is absent
    # on purpose (see module docstring).
    _MODEL_BY_ENTITY: ClassVar[dict[EntityType, type[_BronzeRawMixin]]] = {
        EntityType.SENSOR_READING: SensorRawORM,
        EntityType.EMPIRICAL_OBSERVATION: EmpiricalObservationRawORM,
        EntityType.EMPIRICAL_EVENT: EmpiricalEventRawORM,
    }

    async def upsert(self, session: AsyncSession, event: IngestEvent) -> bool:
        """Insert the event idempotently. Return ``True`` if new, ``False`` if duplicate.

        Args:
            session: An active ``AsyncSession``. The caller manages the
                transaction (``async with session.begin():``).
            event: A validated, HMAC-verified ``IngestEvent``.

        Returns:
            ``True``  — row was inserted (new ``event_id``).
            ``False`` — ``event_id`` already existed; this was a duplicate.

        Raises:
            NotImplementedError: If ``event.entity_type`` is ``COMMENT``
                or any other value without a corresponding bronze table.
        """
        model = self._MODEL_BY_ENTITY.get(event.entity_type)
        if model is None:
            raise NotImplementedError(
                f"No bronze table for entity_type={event.entity_type!r}. "
                f"COMMENT is currently stubbed (501) and shouldn't reach "
                f"this layer. Available: {sorted(e.value for e in self._MODEL_BY_ENTITY)}"
            )

        stmt = (
            pg_insert(model)
            .values(
                event_id=event.event_id,
                station_uuid=event.station_uuid,
                schema_version=event.schema_version,
                payload=event.payload,
                # Explicit received_at (not relying on DEFAULT now()) per
                # decision 8: coherence between log, in-memory event, and
                # the row.
                received_at=event.received_at_utc,
                sent_at_utc=event.sent_at_utc,
                key_version_used=event.key_version_used,
                received_from_ip=event.received_from_ip,
                # kafka_* columns stay NULL during Track A.
            )
            .on_conflict_do_nothing(index_elements=["event_id"])
            .returning(model.event_id)
        )
        result = await session.execute(stmt)
        # RETURNING yields one row if inserted, zero if skipped by ON CONFLICT.
        return result.scalar_one_or_none() is not None
