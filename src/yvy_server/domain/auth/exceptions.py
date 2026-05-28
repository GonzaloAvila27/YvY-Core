"""Envelope and authentication exceptions.

All exceptions raised during envelope/payload validation inherit from
``EnvelopeError``. Each subclass declares two class-level constants that
the presentation layer uses to construct the HTTP response:

- ``error_class``: matches the codes in ``YVY_FASE9_Arquitectura_v2.md``
  §2.5 (``"SCHEMA"``, ``"AUTH"``, ``"CLOCK"``). The edge agent uses this
  to decide retry / DLQ / latch behavior.
- ``http_status``: the HTTP status code to return.

All exceptions carry three optional attributes (``station_uuid``,
``event_id``, ``skew_seconds``) so the use case can record them to
``bronze.ingest_log`` when handling the exception, without having to
re-parse the envelope. Each raiser sets what's known at the point of
failure (e.g. pre-station-resolution errors leave ``station_uuid=None``).

Two distinct ``error_class="SCHEMA"`` cases coexist:

- ``EnvelopeShapeError`` — envelope is structurally invalid (missing
  fields, bad JSON, unsupported ``schema_version``). Raised **before**
  HMAC verification. Logged to ``bronze.ingest_log``. Edge agent sends
  to DLQ immediately.
- ``PayloadSchemaError`` — envelope passed HMAC but the payload doesn't
  match the declared ``schema_version``. Persisted to
  ``bronze.ingest_failures`` (the server-side DLQ from §6.3 / §11.5).
  Edge agent treats as fatal too.
"""
from __future__ import annotations

from typing import ClassVar
from uuid import UUID


class EnvelopeError(Exception):
    """Base for all envelope/payload validation errors.

    Concrete subclasses set ``error_class`` and ``http_status`` as
    class-level constants. The presentation layer reads these to build
    consistent error responses.

    The optional attributes ``station_uuid``, ``event_id`` and
    ``skew_seconds`` carry whatever was known at the moment of failure.
    Use case handlers extract them to populate ``bronze.ingest_log``.
    """

    error_class: ClassVar[str]
    http_status: ClassVar[int]

    def __init__(
        self,
        message: str,
        *,
        station_uuid: UUID | None = None,
        event_id: UUID | None = None,
        skew_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.station_uuid = station_uuid
        self.event_id = event_id
        self.skew_seconds = skew_seconds


# ─── Pre-HMAC: structural envelope issues ────────────────────────────────


class EnvelopeShapeError(EnvelopeError):
    """The envelope is structurally invalid.

    Raised when:
    - Required fields are missing.
    - ``schema_version`` is not in ``SUPPORTED_SCHEMAS``.
    - The body is not valid JSON.
    - Field types are unparseable (e.g. ``station_uuid`` is not a UUID).

    All these are detected before HMAC verification, so typically
    ``station_uuid`` is ``None`` (we never got to identify the station).
    """

    error_class: ClassVar[str] = "SCHEMA"
    http_status: ClassVar[int] = 400


# ─── HMAC verification failures ──────────────────────────────────────────


class HmacMismatchError(EnvelopeError):
    """The signature in the envelope does not match the computed HMAC.

    Raised after station and credential are resolved, so ``station_uuid``
    and ``event_id`` are set.
    """

    error_class: ClassVar[str] = "AUTH"
    http_status: ClassVar[int] = 401


class StationNotFoundError(EnvelopeError):
    """The declared ``station_uuid`` has no row in the ``station`` table.

    ``station_uuid`` IS set (we tried to look it up). ``event_id`` may
    or may not be set depending on whether shape validation extracted it.
    """

    error_class: ClassVar[str] = "AUTH"
    http_status: ClassVar[int] = 401


class CredentialNotFoundError(EnvelopeError):
    """The declared ``key_version`` has no active row in ``station_credentials``.

    Either the version doesn't exist at all, or the row was deactivated
    (``deactivated_at IS NOT NULL``).
    """

    error_class: ClassVar[str] = "AUTH"
    http_status: ClassVar[int] = 401


# ─── Clock skew (post-HMAC) ──────────────────────────────────────────────


class ClockSkewError(EnvelopeError):
    """The envelope's ``sent_at_utc`` is too far from server time.

    Raised after HMAC verification (we trust the timestamp once the
    signature checks out) when the absolute difference exceeds
    ``YVY_CLOCK_SKEW_MAX_SECONDS``.

    The edge agent uses the ``X-Server-Time`` response header (set by
    the handler) to recalibrate and retry. The measured ``skew_seconds``
    attribute is populated.
    """

    error_class: ClassVar[str] = "CLOCK"
    http_status: ClassVar[int] = 422


# ─── Post-HMAC: payload typing fails ─────────────────────────────────────


class PayloadSchemaError(EnvelopeError):
    """The payload shape doesn't match the declared ``schema_version``.

    Raised after HMAC verification when the typed Pydantic model fails
    to parse the payload. Goes to ``bronze.ingest_failures`` (the server
    DLQ) with the full raw envelope for later inspection.
    """

    error_class: ClassVar[str] = "SCHEMA"
    http_status: ClassVar[int] = 400
