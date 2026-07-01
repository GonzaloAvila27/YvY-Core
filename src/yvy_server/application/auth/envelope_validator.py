"""Envelope validation service.

Orchestrates the full validation pipeline for incoming ingest requests:

1. **Shape check (pre-HMAC).** Required fields present, ``schema_version``
   in the supported set.
2. **Station resolution.** ``station_uuid`` exists in the database.
3. **Credential resolution.** Active credential exists for
   ``(station_uuid, key_version)``.
4. **HMAC verification.** ``signature`` matches the canonical bytes
   signed with the resolved credential's key.
5. **Clock skew check.** ``sent_at_utc`` is close enough to server time.

Any failure raises an ``EnvelopeError`` subclass with as much context
as was known at the point of failure (``station_uuid``, ``event_id``,
``skew_seconds`` populated where applicable).

The pure module-level functions (``validate_shape``, ``verify_hmac``,
``check_clock_skew``) are exposed for unit testing without setting up
repos. The ``EnvelopeValidator`` class is the integration point used
by the use cases.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from yvy_server.domain.auth import (
    ClockSkewError,
    CredentialNotFoundError,
    EnvelopeShapeError,
    HmacMismatchError,
    StationNotFoundError,
    canonicalize,
)
from yvy_server.domain.station import Station, StationCredential
from yvy_server.infrastructure.persistence import (
    CredentialRepository,
    StationRepository,
)

# Fields the envelope MUST have for the server to even attempt processing.
# Mirrors the contract in ``YVY_FASE9_Arquitectura_v2.md`` §2.3.
_REQUIRED_ENVELOPE_FIELDS: frozenset[str] = frozenset(
    {
        "schema_version",
        "event_id",
        "station_uuid",
        "key_version",
        "sent_at_utc",
        "nonce",
        "payload",
        "signature",
    }
)


@dataclass(frozen=True, slots=True)
class ValidatedEnvelope:
    """Result of a successful envelope validation.

    Carries the raw envelope dict plus the resolved domain objects, so
    the use case can build the ``IngestEvent`` and the log entry
    without re-parsing or re-querying.
    """

    envelope: dict[str, Any]
    station: Station
    credential: StationCredential
    skew_seconds: int
    received_at_utc: datetime


# ─── Pure validation functions ───────────────────────────────────────────


def validate_shape(envelope: dict[str, Any], supported_schemas: frozenset[str]) -> None:
    """Pre-HMAC structural check.

    Verifies:
    - All required fields are present.
    - ``schema_version`` is in the configured supported set.

    Does NOT verify the types of values; that comes later (Pydantic in
    the handler, or implicit via ``IngestEvent.from_envelope`` coercion).

    Raises ``EnvelopeShapeError`` on failure. Station is unknown at this
    point so the exception carries no ``station_uuid``.
    """
    missing = _REQUIRED_ENVELOPE_FIELDS - envelope.keys()
    if missing:
        raise EnvelopeShapeError(f"Envelope missing required fields: {sorted(missing)}")

    schema_version = envelope["schema_version"]
    if schema_version not in supported_schemas:
        raise EnvelopeShapeError(
            f"schema_version={schema_version!r} not supported. "
            f"Supported: {sorted(supported_schemas)}"
        )


def verify_hmac(
    envelope: dict[str, Any],
    key_bytes: bytes,
    *,
    station_uuid: UUID | None = None,
    event_id: UUID | None = None,
) -> None:
    """HMAC-SHA256 verification.

    Canonicalizes the envelope MINUS the ``signature`` field, computes
    HMAC-SHA256 with ``key_bytes``, and compares (constant-time) to the
    ``signature`` declared in the envelope.

    Raises ``HmacMismatchError`` on mismatch. The optional kwargs are
    attached to the exception so the use case can log them.
    """
    declared_signature = envelope["signature"]

    envelope_without_signature = {k: v for k, v in envelope.items() if k != "signature"}
    canonical_bytes = canonicalize(envelope_without_signature)

    computed = hmac.new(key_bytes, canonical_bytes, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed, declared_signature):
        raise HmacMismatchError(
            "HMAC signature mismatch",
            station_uuid=station_uuid,
            event_id=event_id,
        )


def check_clock_skew(
    envelope: dict[str, Any],
    received_at_utc: datetime,
    max_seconds: int,
    *,
    station_uuid: UUID | None = None,
    event_id: UUID | None = None,
) -> int:
    """Clock skew check.

    Parses ``envelope["sent_at_utc"]`` (ISO 8601 with ``Z`` suffix),
    computes the absolute difference with ``received_at_utc``, and
    raises ``ClockSkewError`` if it exceeds ``max_seconds``.

    Returns the measured skew in seconds (rounded to int) on success.
    The use case persists this in ``ingest_log.skew_seconds`` for stats.
    """
    sent_at_utc = _parse_iso_utc(envelope["sent_at_utc"])
    skew = int(abs((received_at_utc - sent_at_utc).total_seconds()))

    if skew > max_seconds:
        raise ClockSkewError(
            f"Clock skew {skew}s exceeds max {max_seconds}s",
            station_uuid=station_uuid,
            event_id=event_id,
            skew_seconds=skew,
        )
    return skew


def _parse_iso_utc(value: str) -> datetime:
    """Parse an ISO 8601 UTC string ending in ``Z`` or ``+00:00``.

    Internal helper. Mirrors the one in ``domain/ingest/events.py``
    intentionally — keeping each layer free of cross-imports for a
    trivial 4-line function.
    """
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


# ─── Validator service ───────────────────────────────────────────────────


class EnvelopeValidator:
    """Orchestrates envelope validation against the database.

    Stateless: no per-request state stored on the instance. The
    ``AsyncSession`` is passed per call.
    """

    def __init__(
        self,
        *,
        station_repo: StationRepository,
        credential_repo: CredentialRepository,
        supported_schemas: frozenset[str],
        max_skew_seconds: int,
    ) -> None:
        self._station_repo = station_repo
        self._credential_repo = credential_repo
        self._supported_schemas = supported_schemas
        self._max_skew_seconds = max_skew_seconds

    async def validate(
        self,
        session: AsyncSession,
        envelope: dict[str, Any],
        received_at_utc: datetime,
    ) -> ValidatedEnvelope:
        """Run the full validation pipeline.

        Args:
            session: Active ``AsyncSession`` for the station/credential
                lookups. The caller manages its lifecycle.
            envelope: The parsed request body (dict from ``json.loads``).
            received_at_utc: When the handler received the request.
                Timezone-aware UTC.

        Returns:
            A ``ValidatedEnvelope`` with the resolved station and
            credential plus the measured skew.

        Raises:
            EnvelopeShapeError: shape or schema_version is wrong.
            StationNotFoundError: station_uuid not in DB.
            CredentialNotFoundError: no active credential for
                (station_uuid, key_version).
            HmacMismatchError: signature doesn't match.
            ClockSkewError: skew exceeds the configured maximum.
        """
        # 1. Pre-HMAC shape
        validate_shape(envelope, self._supported_schemas)

        # 2. Extract identifiers (safe now — shape validated)
        station_uuid = UUID(envelope["station_uuid"])
        event_id = UUID(envelope["event_id"])
        key_version = int(envelope["key_version"])

        # 3. Resolve station
        station = await self._station_repo.get_by_uuid(session, station_uuid)
        if station is None:
            raise StationNotFoundError(
                f"station_uuid={station_uuid} not registered",
                station_uuid=station_uuid,
                event_id=event_id,
            )

        # 4. Resolve credential (active only)
        credential = await self._credential_repo.get_active_by_station_and_version(
            session, station_uuid, key_version
        )
        if credential is None:
            raise CredentialNotFoundError(
                f"no active credential for station={station_uuid} " f"key_version={key_version}",
                station_uuid=station_uuid,
                event_id=event_id,
            )

        # 5. HMAC
        verify_hmac(
            envelope,
            credential.key_bytes,
            station_uuid=station_uuid,
            event_id=event_id,
        )

        # 6. Clock skew (after HMAC — we trust the timestamp now)
        skew_seconds = check_clock_skew(
            envelope,
            received_at_utc,
            self._max_skew_seconds,
            station_uuid=station_uuid,
            event_id=event_id,
        )

        return ValidatedEnvelope(
            envelope=envelope,
            station=station,
            credential=credential,
            skew_seconds=skew_seconds,
            received_at_utc=received_at_utc,
        )
