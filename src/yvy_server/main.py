"""Composition root + FastAPI app factory.

The single place where all dependencies are wired together.

Lifespan
--------

Startup:
    1. Load ``Settings`` (from env + ``.env``).
    2. Configure structured JSON logging at the configured level.
    3. Build the async SQLAlchemy engine + session factory.
    4. Instantiate stateless repositories (one of each).
    5. Build the ``EnvelopeValidator`` from settings + repos.
    6. Build the Track A publisher (``PostgresDirectPublisher``).
    7. Build the three ingest use cases (observation, sensor, event).
    8. Build the ``EdgeReleaseRepository`` and read the initial
       ``sync_version`` from the database.
    9. Store all the above on ``app.state`` for dependencies to read.

Shutdown:
    - ``dispose_engine`` closes the connection pool gracefully.

Track A is the only publisher active in 9.1. When Track B (Kafka)
lands, the change is a one-liner here (swap ``PostgresDirectPublisher``
for ``DualPublisher`` or ``KafkaPublisher``) — the use case doesn't
change because it depends on the ``IngestEventPublisher`` Protocol.

Running
-------

    uvicorn yvy_server.main:app --host 0.0.0.0 --port 8000

The module-level ``app`` is what uvicorn loads. ``create_app()`` is
exposed for tests that want to construct a fresh instance with
overrides.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from yvy_server import __version__
from yvy_server.application.auth import EnvelopeValidator
from yvy_server.application.use_cases import (
    IngestEventUseCase,
    IngestObservationUseCase,
    IngestSensorUseCase,
)
from yvy_server.config import get_settings
from yvy_server.infrastructure.db import (
    create_engine_and_session_factory,
    dispose_engine,
)
from yvy_server.infrastructure.persistence import (
    BronzeRepository,
    CredentialRepository,
    EdgeReleaseRepository,
    IngestFailuresRepository,
    IngestLogRepository,
    PublicReadRepository,
    StationRepository,
)
from yvy_server.infrastructure.publisher import PostgresDirectPublisher
from yvy_server.logging_setup import setup_logging
from yvy_server.presentation.api import ServerHeadersMiddleware
from yvy_server.presentation.api.routers import admin, edge, health, ingest, public


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup + shutdown of long-lived dependencies."""
    settings = get_settings()
    setup_logging(settings.log_level)
    log = logging.getLogger("yvy.main")

    log.info(
        "starting up",
        extra={
            "version": __version__,
            "env": settings.env,
            "log_level": settings.log_level,
            "supported_schemas": sorted(settings.supported_schemas),
            "clock_skew_max_seconds": settings.clock_skew_max_seconds,
        },
    )

    # ─── DB infrastructure ──────────────────────────────────────────────
    engine, session_factory = create_engine_and_session_factory(settings)

    # ─── Repositories (stateless, one instance per process) ─────────────
    station_repo = StationRepository()
    credential_repo = CredentialRepository()
    bronze_repo = BronzeRepository()
    log_repo = IngestLogRepository()
    failures_repo = IngestFailuresRepository()
    edge_release_repo = EdgeReleaseRepository()
    public_read_repo = PublicReadRepository()

    # ─── Application services ───────────────────────────────────────────
    envelope_validator = EnvelopeValidator(
        station_repo=station_repo,
        credential_repo=credential_repo,
        supported_schemas=settings.supported_schemas,
        max_skew_seconds=settings.clock_skew_max_seconds,
    )

    # ─── Publisher (Track A) ────────────────────────────────────────────
    publisher = PostgresDirectPublisher(session_factory, bronze_repo)

    # ─── Use cases ──────────────────────────────────────────────────────
    _uc_kwargs = dict(
        validator=envelope_validator,
        publisher=publisher,
        log_repo=log_repo,
        failures_repo=failures_repo,
        session_factory=session_factory,
        producer_version=__version__,
    )
    ingest_observation_use_case = IngestObservationUseCase(**_uc_kwargs)
    ingest_sensor_use_case = IngestSensorUseCase(**_uc_kwargs)
    ingest_event_use_case = IngestEventUseCase(**_uc_kwargs)

    # ─── Seed sync_version cache ─────────────────────────────────────────
    # Cached at startup so ingest piggyback headers have a real value
    # immediately. Stale after publish-update until server restart —
    # acceptable for the pilot (operator restarts after publishing).
    async with session_factory() as _session:
        initial_sync_version = await edge_release_repo.get_current_sync_version(_session)

    # ─── Wire to app.state ──────────────────────────────────────────────
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.station_repo = station_repo
    app.state.credential_repo = credential_repo
    app.state.edge_release_repo = edge_release_repo
    app.state.public_read_repo = public_read_repo
    app.state.ingest_observation_use_case = ingest_observation_use_case
    app.state.ingest_sensor_use_case = ingest_sensor_use_case
    app.state.ingest_event_use_case = ingest_event_use_case
    app.state.sync_version = initial_sync_version

    log.info(
        "startup complete; ready to serve requests",
        extra={"initial_sync_version": initial_sync_version},
    )

    try:
        yield
    finally:
        log.info("shutting down")
        await dispose_engine(engine)
        log.info("shutdown complete")


def create_app() -> FastAPI:
    """Build the FastAPI application.

    Lifespan loads ``Settings`` lazily, so import-time side effects are
    minimal: only the FastAPI() construction + middleware + router
    registration. Errors in Settings (e.g. bad DSN) surface at startup,
    not at import.
    """
    app = FastAPI(
        title="YVY Server",
        description=(
            "Bronze ingest layer for the YVY distributed weather system. "
            "Receives signed (HMAC-SHA256) payloads from edge nodes in "
            "agricultural schools (IEAs) in Misiones, Argentina."
        ),
        version=__version__,
        lifespan=lifespan,
        redoc_url=None,
    )

    app.add_middleware(ServerHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().public_cors_origins,
        allow_origin_regex=r"https://.*\.vercel\.app",
        allow_methods=["GET"],
        allow_headers=["*"],
        max_age=600,
    )

    app.include_router(health.router)
    app.include_router(admin.router)
    app.include_router(ingest.router)
    app.include_router(edge.router)
    app.include_router(public.router)

    return app


# Module-level instance for ``uvicorn yvy_server.main:app``
app = create_app()
