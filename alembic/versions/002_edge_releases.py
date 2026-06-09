"""edge_releases + sync_state — Fase 9.2 (Canal Core→Edge)

Adds:
- ``public.edge_releases``: catalog of published software releases that
  edge nodes can download.
- ``public.sync_state``: singleton row tracking the global monotonic
  ``sync_version`` counter. Incremented atomically every time a new
  release is published or any other distributable change is committed.

Why a separate ``sync_state`` table instead of ``MAX(id)`` on
``edge_releases``?
- Future categories (feed items, alerts, dashboard config) will also
  bump the counter. Decoupling sync_version from a single table makes
  the counter genuinely global.
- ``UPDATE … RETURNING`` on a single row is the simplest atomic
  increment without a Postgres sequence (avoids gap issues).

Revision ID: 0002_edge_releases
Revises: 0001_init
Create Date: 2026-06-08
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_edge_releases"
down_revision: str | None = "0001_init"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ─── public.sync_state (singleton row) ──────────────────────────────
    op.create_table(
        "sync_state",
        sa.Column("id", sa.Integer(), primary_key=True, default=1),
        sa.Column("sync_version", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.execute("INSERT INTO sync_state (id, sync_version) VALUES (1, 0)")

    # ─── public.edge_releases ────────────────────────────────────────────
    op.create_table(
        "edge_releases",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        # Semver string, e.g. "0.9.3"
        sa.Column("edge_version", sa.Text(), nullable=False),
        # Just the filename stored in releases_dir, e.g. "0.9.3.tar.gz"
        sa.Column("package_filename", sa.Text(), nullable=False),
        # Hex SHA-256 of the .tar.gz (64 chars)
        sa.Column("package_sha256", sa.Text(), nullable=False),
        sa.Column("package_size_bytes", sa.BigInteger(), nullable=False),
        # Minimum edge version that can safely apply this update directly
        sa.Column("min_edge_version", sa.Text(), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "released_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Allows "unpublishing" a release without deleting the row
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        # sync_version assigned when this release was published
        sa.Column("sync_version", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "ix_edge_releases_edge_version",
        "edge_releases",
        ["edge_version"],
        unique=True,
    )
    op.create_index(
        "ix_edge_releases_active_released",
        "edge_releases",
        ["is_active", sa.text("released_at DESC")],
        postgresql_where=sa.text("is_active = true"),
    )


def downgrade() -> None:
    op.drop_table("edge_releases")
    op.drop_table("sync_state")
