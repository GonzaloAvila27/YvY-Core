"""Domain · Ingest — Publisher port (Protocol).

Defines the abstract contract that any event publisher must fulfil.
Implementations live in ``infrastructure/publisher/``. The Protocol
keeps the application layer decoupled from transport details.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from yvy_server.domain.ingest.events import IngestEvent


@dataclass(frozen=True, slots=True)
class PublishResult:
    """Outcome of an ``IngestEventPublisher.publish`` call."""

    persisted: bool
    transport: str
    topic: str
    was_duplicate: bool = False


class IngestEventPublisher(Protocol):
    """Port that the application layer calls to persist an event.

    Implementations live in ``infrastructure/publisher/``.
    """

    async def publish(self, event: IngestEvent) -> PublishResult:
        """Persist or transmit the event."""
        ...
