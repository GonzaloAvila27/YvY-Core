"""SQLAlchemy ORM models.

Tracks what's actually in the DB post-M0:
  - public.station, station_credentials, sync_state, edge_releases
  - bronze.raw_record (particionada — M0)
  - bronze.ingest_log, bronze.ingest_failures (audit log y DLQ, intocadas)

Los tres *_raw anteriores (sensor_raw, empirical_observation_raw,
empirical_event_raw) fueron dropeados en la migración M0. Sus ORMs
fueron removidos de este módulo.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.sqltypes import TIMESTAMP

# Same naming_convention as alembic/env.py — keep in sync.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base shared by all ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# ─── public.station ──────────────────────────────────────────────────────


class StationORM(Base):
    """Maps ``public.station``. Materializes to domain ``Station`` in repos."""

    __tablename__ = "station"

    station_uuid: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_lat: Mapped[float | None] = mapped_column(REAL, nullable=True)
    location_lon: Mapped[float | None] = mapped_column(REAL, nullable=True)
    location_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    # C10 — public layer fields (added by migration 003)
    code: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    locality: Mapped[str | None] = mapped_column(Text, nullable=True)
    province: Mapped[str | None] = mapped_column(Text, nullable=True, server_default="Misiones")
    latitude: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


# ─── public.station_credentials ──────────────────────────────────────────


class StationCredentialORM(Base):
    """Maps ``public.station_credentials``. Materializes to ``StationCredential``."""

    __tablename__ = "station_credentials"
    __table_args__ = (
        UniqueConstraint(
            "station_uuid",
            "key_version",
            name="uq_station_credentials_station_uuid_key_version",
        ),
    )

    credential_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    station_uuid: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("station.station_uuid"),
        nullable=False,
    )
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    key_hmac_hex: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


# ─── bronze.raw_record (M0) ──────────────────────────────────────────────


class RawRecordORM(Base):
    """Maps ``bronze.raw_record`` — tabla genérica post-M0.

    Particionada por ``received_at``. SQLAlchemy soporta INSERT en tablas
    particionadas sin configuración especial.

    ``record_id`` es el ``event_id`` original del envelope del Edge,
    preservando idempotencia (ON CONFLICT DO NOTHING sobre record_id).
    """

    __tablename__ = "raw_record"
    __table_args__ = (
        Index("idx_raw_source_time", "source_id", text("received_at DESC")),
        Index("idx_raw_kind", "record_kind", text("received_at DESC")),
        {"schema": "bronze"},
    )

    record_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    record_kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)  # type: ignore[type-arg]
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    ingestion_meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # type: ignore[type-arg]


# ─── bronze.ingest_log ──────────────────────────────────────────────────


class IngestLogORM(Base):
    """Maps ``bronze.ingest_log``. Every ingest attempt logged, success or fail."""

    __tablename__ = "ingest_log"
    __table_args__ = (
        Index("idx_ingest_log_recv", text("received_at DESC")),
        # Partial index over failed attempts only — what admin dashboards query.
        Index(
            "idx_ingest_log_failures",
            "failure_class",
            text("received_at DESC"),
            postgresql_where=text("failure_class IS NOT NULL"),
        ),
        {"schema": "bronze"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    received_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    # Nullable because AUTH failures happen before we know the station.
    station_uuid: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_class: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_id: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    skew_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)


# ─── bronze.ingest_failures ─────────────────────────────────────────────


class IngestFailureORM(Base):
    """Maps ``bronze.ingest_failures``. DLQ for post-HMAC failures."""

    __tablename__ = "ingest_failures"
    __table_args__ = (
        CheckConstraint(
            "resolved_action IS NULL OR resolved_action IN "
            "('reprocessed', 'discarded', 'fixed_at_source')",
            name="ck_ingest_failures_resolved_action",
        ),
        # Partial index over open DLQ entries — what admins query daily.
        Index(
            "idx_failures_unresolved",
            "failure_class",
            text("received_at DESC"),
            postgresql_where=text("resolved_at IS NULL"),
        ),
        {"schema": "bronze"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    station_uuid: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    entity_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    failure_class: Mapped[str] = mapped_column(Text, nullable=False)
    failure_reason: Mapped[str] = mapped_column(Text, nullable=False)
    raw_envelope: Mapped[dict] = mapped_column(JSONB, nullable=False)  # type: ignore[type-arg]
    received_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    received_from_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    resolved_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)


# ─── public.sync_state ──────────────────────────────────────────────────


class SyncStateORM(Base):
    """Singleton row tracking the global monotonic ``sync_version`` counter.

    Always exactly one row (id=1). Incremented atomically via
    ``UPDATE sync_state SET sync_version = sync_version + 1 RETURNING sync_version``
    inside a transaction every time a distributable change is published
    (new release, future: feed item, alert, etc.).
    """

    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    sync_version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")


# ─── public.edge_releases ───────────────────────────────────────────────


class EdgeReleaseORM(Base):
    """Maps ``public.edge_releases``.

    Each row is a published YVY Edge software release that edge nodes can
    discover via ``GET /api/v1/edge/sync`` and download via
    ``GET /api/v1/edge/updates/{filename}``.
    """

    __tablename__ = "edge_releases"
    __table_args__ = (
        Index("ix_edge_releases_edge_version", "edge_version", unique=True),
        # Partial index for "latest active release" queries
        Index(
            "ix_edge_releases_active_released",
            "is_active",
            text("released_at DESC"),
            postgresql_where=text("is_active = true"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    edge_version: Mapped[str] = mapped_column(Text, nullable=False)
    package_filename: Mapped[str] = mapped_column(Text, nullable=False)
    package_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    package_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    min_edge_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    released_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    sync_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
