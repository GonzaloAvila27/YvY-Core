"""Infrastructure · Publisher — ``PostgresDirectPublisher`` (Track A).

The ``KafkaPublisher`` and ``DualPublisher`` for Track B will land here
too when the migration plan kicks in. Composition root in ``main.py``
selects which one to wire into the use cases.
"""

from yvy_server.infrastructure.publisher.postgres_direct import PostgresDirectPublisher

__all__ = ["PostgresDirectPublisher"]
