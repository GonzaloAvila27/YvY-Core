"""Canonical ingest event model — agreed shape across Tracks A and B.

``IngestEvent`` is the unified value object that flows through the
ingest pipeline regardless of transport. The same instance is consumed
by ``PostgresDirectPublisher`` (Track A, this pilot) and, when Track B
is integrated, by ``KafkaPublisher`` and ``DualPublisher``.

The shape mirrors ``YVY_FASE9_Arquitectura_v2.md`` §11.2, with the
correction agreed on 14-May-2026: ``entity_type`` is passed explicitly
by the handler instead of being read from ``payload["entity_type"]``
(which doesn't exist — the v2 §11.2 code sample had a bug, see resumen
decision 6).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class EntityType(StrEnum):
    """The four ingest entity types known to the server.

    Each value matches the URL slug of its endpoint
    (``/api/v1/ingest/<slug>``) and the Kafka topic suffix in Track B.

    Note: the edge has five ``entity_type`` values in its ``sync_queue``
    because of legacy (``empirical_record`` pre-Fase 8.5). The server
    only knows these four. An envelope arriving with any other value
    means it hit the wrong endpoint — caller bug.
    """

    SENSOR_READING = "sensor_reading"
    EMPIRICAL_OBSERVATION = "empirical_observation"
    EMPIRICAL_EVENT = "empirical_event"
    COMMENT = "comment"


# Topic names for Track B Kafka. Track A doesn't use them at runtime
# (``PostgresDirectPublisher`` writes directly to bronze tables) but
# ``PublishResult.topic`` carries the logical name regardless of
# transport, so downstream consumers see consistent values when
# DualPublisher arrives.
#
# Format: ``yvy.<entity>.raw``. ``raw`` denotes the Bronze layer
# (unprocessed). Future Silver/Gold tracks would be ``yvy.<entity>.silver``.
TOPIC_NAMES: dict[EntityType, str] = {
    EntityType.SENSOR_READING: "yvy.sensor.raw",
    EntityType.EMPIRICAL_OBSERVATION: "yvy.empirical.observation.raw",
    EntityType.EMPIRICAL_EVENT: "yvy.empirical.event.raw",
    EntityType.COMMENT: "yvy.comment.raw",
}


@dataclass(frozen=True, slots=True)
class IngestEvent:
    """The unit of data the server processes and publishes.

    Constructed by ingest handlers via :meth:`from_envelope` once the
    envelope has passed HMAC verification and clock-skew validation.
    Then passed to ``IngestEventPublisher.publish`` for persistence.

    All datetimes must be timezone-aware UTC. The domain rejects naive
    datetimes implicitly (the use case asserts ``tzinfo is not None``).

    ``payload`` is opaque (``dict[str, Any]``) on purpose — the domain
    layer doesn't tie itself to Pydantic. Application-layer use cases
    that need typed payloads do so by validating with Pydantic before
    constructing the ``IngestEvent`` from a now-validated dict.
    """

    event_id: UUID
    station_uuid: UUID
    entity_type: EntityType
    schema_version: str
    payload: dict[str, Any]
    sent_at_utc: datetime
    received_at_utc: datetime
    received_from_ip: str | None
    key_version_used: int
    producer_version: str

    @classmethod
    def from_envelope(
        cls,
        envelope: dict[str, Any],
        *,
        entity_type: EntityType,
        received_at_utc: datetime,
        received_from_ip: str | None,
        producer_version: str,
    ) -> IngestEvent:
        """Build an ``IngestEvent`` from a verified envelope dict.

        The envelope is the canonical request body (already parsed from
        JSON). HMAC verification and clock-skew checks MUST have been
        performed before calling this. This method assumes the envelope
        is structurally sound; any KeyError or ValueError raised here is
        an internal bug, not a client error.

        Args:
            envelope: The verified envelope dict. Must contain
                ``event_id``, ``station_uuid``, ``schema_version``,
                ``payload``, ``sent_at_utc``, ``key_version`` (as
                strings/primitives — they get coerced here).
            entity_type: Set by the caller (the handler that received
                the envelope). The envelope itself doesn't carry this.
            received_at_utc: When the handler received the request.
                Must be timezone-aware UTC. Computed once in the handler
                per decision 8 (no ``DEFAULT now()`` race).
            received_from_ip: Source IP from the request, or ``None``
                if it can't be determined.
            producer_version: The server's own version string, fed from
                ``yvy_server.__version__``. This is what travels in the
                event for tracking across publisher implementations.
        """
        return cls(
            event_id=UUID(envelope["event_id"]),
            station_uuid=UUID(envelope["station_uuid"]),
            entity_type=entity_type,
            schema_version=envelope["schema_version"],
            payload=envelope["payload"],
            sent_at_utc=_parse_iso_utc(envelope["sent_at_utc"]),
            received_at_utc=received_at_utc,
            received_from_ip=received_from_ip,
            key_version_used=int(envelope["key_version"]),
            producer_version=producer_version,
        )

    @property
    def topic(self) -> str:
        """The logical topic name for this event (Track B mapping)."""
        return TOPIC_NAMES[self.entity_type]


def _parse_iso_utc(value: str) -> datetime:
    """Parse an ISO 8601 UTC string ending in ``Z`` or ``+00:00``.

    Returns a timezone-aware datetime. Raises ValueError on bad input.
    Implementation note: ``datetime.fromisoformat`` accepts ``+00:00``
    natively from py3.11+. We normalize the ``Z`` suffix to ``+00:00``
    first because the envelope spec uses ``Z``.
    """
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)
