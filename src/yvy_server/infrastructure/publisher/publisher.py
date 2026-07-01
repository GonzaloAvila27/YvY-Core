"""Publisher port — abstraction over the persistence/streaming transport.

The server doesn't care whether the event ends up in a Postgres table,
a Kafka topic, or both. It just calls ``publish`` and trusts the
adapter behind the Protocol.

Track A (this pilot) implements this with ``PostgresDirectPublisher``
(see ``infrastructure/publisher/``). Track B will add ``KafkaPublisher``
and ``DualPublisher`` in parallel; the swap is a one-line change in the
composition root (``main.py``) once Track B is ready for cutover.

Protocol shape agreed with the Track B developer (profesor especialista).
If they propose ajustes, we accept and update both sides.
"""

from __future__ import annotations

# Re-export domain types so infrastructure code can import from one place.
from yvy_server.domain.ingest.publisher import IngestEventPublisher, PublishResult

__all__ = ["IngestEventPublisher", "PublishResult"]
