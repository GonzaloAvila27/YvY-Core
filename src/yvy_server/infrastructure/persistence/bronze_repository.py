"""Bronze repository — idempotent UPSERT to ``bronze.raw_record`` (post-M0).

Records are stored in the single generic table ``bronze.raw_record``
partitioned by ``received_at``.  The three typed tables that existed
before M0 (sensor_raw, empirical_observation_raw, empirical_event_raw)
were dropped in migration 003_m0_canonical_model.

Station → source_id resolution
--------------------------------
The ingest envelope carries ``station_uuid`` (Edge identity).  The
canonical model stores each source in ``canonical.source`` with
``external_ref = station_uuid::text``.

``load_source_cache`` MUST be called once at startup (main.py lifespan)
to populate the in-memory dict.  Cache misses trigger a DB lookup and
warm the cache on the fly, so adding a new station mid-session works
without a restart.

Idempotency
-----------
``INSERT … ON CONFLICT (record_id) DO NOTHING RETURNING record_id``.
``record_id`` maps 1-to-1 from the old ``event_id`` (same UUID from the
Edge envelope), preserving deduplication semantics byte-for-byte.
"""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID as PyUUID

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from yvy_server.domain.ingest import EntityType, IngestEvent
from yvy_server.infrastructure.db import RawRecordORM


class BronzeRepository:
    """Idempotent UPSERT to ``bronze.raw_record``."""

    # entity_type.value already matches record_kind string — explicit for clarity.
    _RECORD_KIND: ClassVar[dict[EntityType, str]] = {
        EntityType.SENSOR_READING: "sensor_reading",
        EntityType.EMPIRICAL_OBSERVATION: "empirical_observation",
        EntityType.EMPIRICAL_EVENT: "empirical_event",
    }

    _SOURCE_TYPE_BY_KIND: ClassVar[dict[str, str]] = {
        "sensor_reading": "weather_station",
        "empirical_observation": "human_observation",
        "empirical_event": "human_observation",
    }

    def __init__(self) -> None:
        # station_uuid (from edge envelope) → source_id (in canonical.source)
        self._station_source_cache: dict[PyUUID, PyUUID] = {}

    async def load_source_cache(self, session: AsyncSession) -> None:
        """Populate station→source_id cache from canonical.source.

        Call once in the lifespan startup after engine creation.  Safe to
        call again to refresh (e.g. after inserting a new station row).
        """
        result = await session.execute(
            text(
                "SELECT external_ref, source_id "
                "FROM canonical.source "
                "WHERE source_type_key = 'weather_station'"
            )
        )
        self._station_source_cache = {
            PyUUID(row.external_ref): row.source_id for row in result.mappings()
        }

    async def _resolve_source_id(
        self, session: AsyncSession, station_uuid: PyUUID
    ) -> PyUUID | None:
        """Return source_id for station_uuid — cache hit first, DB fallback."""
        if station_uuid in self._station_source_cache:
            return self._station_source_cache[station_uuid]

        result = await session.execute(
            text(
                "SELECT source_id FROM canonical.source "
                "WHERE external_ref = :ref AND source_type_key = 'weather_station'"
            ),
            {"ref": str(station_uuid)},
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None

        source_id: PyUUID = row["source_id"]
        self._station_source_cache[station_uuid] = source_id
        return source_id

    async def upsert(self, session: AsyncSession, event: IngestEvent) -> bool:
        """Insert the event idempotently. Return ``True`` if new, ``False`` if duplicate.

        Args:
            session: An active ``AsyncSession``. The caller manages the
                transaction (``async with session.begin():``).
            event: A validated, HMAC-verified ``IngestEvent``.

        Returns:
            ``True``  — row was inserted (new ``record_id``).
            ``False`` — ``record_id`` already existed; this was a duplicate.

        Raises:
            NotImplementedError: If ``event.entity_type`` is ``COMMENT``
                or any other value without a corresponding record_kind.
            LookupError: If ``event.station_uuid`` has no entry in
                ``canonical.source``.  Station must be backfilled (M0) or
                registered before ingesting.
        """
        record_kind = self._RECORD_KIND.get(event.entity_type)
        if record_kind is None:
            raise NotImplementedError(
                f"No record_kind for entity_type={event.entity_type!r}. "
                "COMMENT is currently stubbed (501) and should not reach this layer."
            )

        source_id = await self._resolve_source_id(session, event.station_uuid)
        if source_id is None:
            raise LookupError(
                f"station_uuid={event.station_uuid} has no entry in canonical.source. "
                "Station may not have been backfilled in M0."
            )

        stmt = (
            pg_insert(RawRecordORM)
            .values(
                record_id=event.event_id,
                source_id=source_id,
                source_type=self._SOURCE_TYPE_BY_KIND[record_kind],
                record_kind=record_kind,
                payload=event.payload,
                schema_version=event.schema_version,
                observed_at=event.sent_at_utc,
                received_at=event.received_at_utc,
                ingestion_meta={
                    "key_version": event.key_version_used,
                    "received_from_ip": event.received_from_ip,
                    "producer_version": event.producer_version,
                },
            )
            .on_conflict_do_nothing(index_elements=["record_id"])
            .returning(RawRecordORM.record_id)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None
