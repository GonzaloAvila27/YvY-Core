"""Application · Use cases — One module per endpoint.

In 9.1 only ``IngestObservationUseCase`` is implemented. Step 9.2 adds
``IngestSensorUseCase`` and ``IngestEventUseCase`` with the same shape.
``IngestCommentUseCase`` lands later (the endpoint stays 501 in 9.1).
"""
from yvy_server.application.use_cases.ingest_observation import (
    IngestFailure,
    IngestObservationUseCase,
    IngestOutcome,
    IngestSuccess,
)

__all__ = [
    "IngestFailure",
    "IngestObservationUseCase",
    "IngestOutcome",
    "IngestSuccess",
]
