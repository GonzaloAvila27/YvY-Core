"""``PostgresDirectPublisher`` — Track A implementation of ``IngestEventPublisher``.

The pilot publisher: writes ``IngestEvent`` directly to the
corresponding ``bronze.*_raw`` table via ``BronzeRepository.upsert``.

Track A vs Track B
------------------

This is Track A. Track B (Kafka) will land in parallel work from the
profesor especialista. When Track B is ready, the composition root in
``main.py`` swaps this for ``DualPublisher`` (writes to both Postgres
and Kafka during cutover) or ``KafkaPublisher`` (after cutover). The
``IngestEventPublisher`` Protocol is the seam — no other code changes.

Transaction model
-----------------

This publisher opens its OWN transaction per ``publish`` call, separate
from any transaction the upstream handler may have. This is deliberate:

- The handler typically has a transaction for writing to ``ingest_log``
  (and possibly ``ingest_failures``). That transaction commits when the
  handler returns, regardless of whether ``publish`` succeeded.
- If ``publish`` raises, the handler catches it and logs a SERVER
  failure to ``ingest_log`` → 5xx response.
- If ``publish`` commits but ``ingest_log`` write subsequently fails,
  the bronze row stays (durable) but the log entry is lost. Acceptable
  trade-off: data over audit. The reverse (lose data, keep log) is
  worse for the project.

If we ever need full atomicity, we'd pass the handler's session in and
flush both in one transaction. Out of scope for 9.1.
"""
from __future__ import annotations

from typing import ClassVar

from yvy_server.domain.ingest import IngestEvent, PublishResult
from yvy_server.infrastructure.db import SessionFactory
from yvy_server.infrastructure.persistence import BronzeRepository


class PostgresDirectPublisher:
    """Track A: write ``IngestEvent`` to ``bronze.*_raw`` synchronously.

    Implements ``IngestEventPublisher`` Protocol by structural typing
    (no inheritance — Protocols are duck-typed).
    """

    TRANSPORT_NAME: ClassVar[str] = "postgres-direct"

    def __init__(
        self,
        session_factory: SessionFactory,
        bronze_repo: BronzeRepository,
    ) -> None:
        """Initialize with the resources needed at publish time.

        Args:
            session_factory: Async session factory from
                ``create_engine_and_session_factory``. The publisher
                opens its own session per ``publish`` call.
            bronze_repo: Repository instance. Stateless, can be shared.
        """
        self._session_factory = session_factory
        self._bronze_repo = bronze_repo

    async def publish(self, event: IngestEvent) -> PublishResult:
        """Persist the event to the bronze table for its ``entity_type``.

        Opens a fresh transaction. On normal return, the row is durably
        committed.

        Args:
            event: Validated ``IngestEvent``. The publisher does NOT
                re-validate — that's the caller's job.

        Returns:
            ``PublishResult`` with:
            - ``persisted=True`` (commit succeeded)
            - ``transport="postgres-direct"``
            - ``topic=event.topic``  (from ``TOPIC_NAMES``)
            - ``was_duplicate=True`` if the ``event_id`` already
              existed (UPSERT skipped), ``False`` if newly inserted.

        Raises:
            NotImplementedError: If ``event.entity_type`` has no bronze
                table (i.e. ``COMMENT``). Propagated from
                ``BronzeRepository.upsert``.
            Various SQLAlchemy/asyncpg errors: connection failures,
                FK violations (station not in ``station`` table), etc.
                The caller should treat these as 5xx SERVER errors.
        """
        async with self._session_factory() as session, session.begin():
            inserted = await self._bronze_repo.upsert(session, event)

        return PublishResult(
            persisted=True,
            transport=self.TRANSPORT_NAME,
            topic=event.topic,
            was_duplicate=not inserted,
        )
