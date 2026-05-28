"""init schema — Fase 9.1

Initial schema for the YVY central server. Creates ``public`` tables for
station identity + credentials, and the ``bronze`` schema with raw
ingest tables and audit/DLQ tables.

Mirrors ``YVY_FASE9_Arquitectura_v2.md`` §6.3 exactly. The ``kafka_*``
columns on the three ``*_raw`` tables stay NULL during the pilot
(Track A only); Track B's DualPublisher will populate them after cutover.

Revision ID: 0001_init
Revises:
Create Date: 2026-05-16

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# ─── Alembic identifiers ─────────────────────────────────────────────────
revision: str = "0001_init"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ─── Helpers ─────────────────────────────────────────────────────────────
#
# Every ``*_raw`` bronze table shares the same shape (event_id PK,
# station_uuid FK, schema_version, payload JSONB, received_at, sent_at_utc,
# key_version_used, received_from_ip, kafka_* placeholders). Defining the
# columns once via this helper avoids 3x copy-paste and the drift that
# inevitably follows.

def _bronze_raw_columns() -> list[sa.Column]:
    """Return the shared column list for ``bronze.<entity>_raw`` tables."""
    return [
        # Identity
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "station_uuid",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("station.station_uuid"),
            nullable=False,
        ),
        # Metadata
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "received_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("sent_at_utc", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("key_version_used", sa.Integer(), nullable=False),
        sa.Column("received_from_ip", postgresql.INET(), nullable=True),
        # Reserved for Track B Kafka — NULL during pilot
        sa.Column("kafka_topic", sa.Text(), nullable=True),
        sa.Column("kafka_partition", sa.Integer(), nullable=True),
        sa.Column("kafka_offset", sa.BigInteger(), nullable=True),
        sa.Column("kafka_published_at", sa.TIMESTAMP(timezone=True), nullable=True),
    ]


# =========================================================================
# UPGRADE
# =========================================================================
def upgrade() -> None:
    # ─── public.station ──────────────────────────────────────────────────
    op.create_table(
        "station",
        sa.Column("station_uuid", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("location_lat", sa.REAL(), nullable=True),
        sa.Column("location_lon", sa.REAL(), nullable=True),
        sa.Column("location_name", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deactivated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # ─── public.station_credentials ──────────────────────────────────────
    op.create_table(
        "station_credentials",
        sa.Column("credential_id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column(
            "station_uuid",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("station.station_uuid"),
            nullable=False,
        ),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("key_hmac_hex", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deactivated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "station_uuid",
            "key_version",
            name="uq_station_credentials_station_uuid_key_version",
        ),
    )

    # ─── CREATE SCHEMA bronze ────────────────────────────────────────────
    # SQLAlchemy/Alembic don't have a clean API for schema creation,
    # so we drop to raw SQL here.
    op.execute("CREATE SCHEMA bronze")

    # ─── bronze.sensor_raw ───────────────────────────────────────────────
    op.create_table(
        "sensor_raw",
        *_bronze_raw_columns(),
        schema="bronze",
    )
    op.create_index(
        "idx_sensor_raw_station_recv",
        "sensor_raw",
        ["station_uuid", sa.text("received_at DESC")],
        schema="bronze",
    )
    # Expression index on payload->>'observed_at_utc' for time-window queries
    # on sensor readings. Raw SQL because SQLAlchemy can't express this
    # idiomatically with op.create_index.
    op.execute(
        "CREATE INDEX idx_sensor_raw_payload_obs "
        "ON bronze.sensor_raw ((payload->>'observed_at_utc'))"
    )

    # ─── bronze.empirical_observation_raw ────────────────────────────────
    op.create_table(
        "empirical_observation_raw",
        *_bronze_raw_columns(),
        schema="bronze",
    )

    # ─── bronze.empirical_event_raw ──────────────────────────────────────
    op.create_table(
        "empirical_event_raw",
        *_bronze_raw_columns(),
        schema="bronze",
    )

    # ─── bronze.ingest_log ───────────────────────────────────────────────
    # Log of EVERY ingest attempt (including failed). station_uuid is
    # nullable because some failures (AUTH) happen before we can
    # identify the station.
    op.create_table(
        "ingest_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column(
            "received_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("station_uuid", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("failure_class", sa.Text(), nullable=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("skew_seconds", sa.Integer(), nullable=True),
        sa.Column("request_ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        schema="bronze",
    )
    op.create_index(
        "idx_ingest_log_recv",
        "ingest_log",
        [sa.text("received_at DESC")],
        schema="bronze",
    )
    # Partial index on failures only — smaller, faster for admin queries.
    op.execute(
        "CREATE INDEX idx_ingest_log_failures "
        "ON bronze.ingest_log (failure_class, received_at DESC) "
        "WHERE failure_class IS NOT NULL"
    )

    # ─── bronze.ingest_failures ──────────────────────────────────────────
    # DLQ for messages that passed HMAC but failed downstream
    # (e.g. payload shape mismatch vs declared schema_version).
    # Maps conceptually to the Kafka topic ``yvy.dlq`` in Track B.
    op.create_table(
        "ingest_failures",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("station_uuid", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("entity_type", sa.Text(), nullable=True),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("failure_class", sa.Text(), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=False),
        sa.Column("raw_envelope", postgresql.JSONB(), nullable=False),
        sa.Column(
            "received_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("received_from_ip", postgresql.INET(), nullable=True),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resolved_action", sa.Text(), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "resolved_action IS NULL OR resolved_action IN "
            "('reprocessed', 'discarded', 'fixed_at_source')",
            name="ck_ingest_failures_resolved_action",
        ),
        schema="bronze",
    )
    # Partial index on unresolved failures only — what admins query daily.
    op.execute(
        "CREATE INDEX idx_failures_unresolved "
        "ON bronze.ingest_failures (failure_class, received_at DESC) "
        "WHERE resolved_at IS NULL"
    )


# =========================================================================
# DOWNGRADE
# =========================================================================
def downgrade() -> None:
    # Drop in reverse dependency order. Indexes go with their tables.
    op.drop_table("ingest_failures", schema="bronze")
    op.drop_table("ingest_log", schema="bronze")
    op.drop_table("empirical_event_raw", schema="bronze")
    op.drop_table("empirical_observation_raw", schema="bronze")
    op.drop_table("sensor_raw", schema="bronze")
    op.execute("DROP SCHEMA bronze CASCADE")
    op.drop_table("station_credentials")
    op.drop_table("station")
