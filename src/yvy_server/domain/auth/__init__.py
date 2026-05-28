"""Domain · Auth — Canonical JSON, HMAC primitives, auth exceptions.

The Canonical JSON v1 implementation must be byte-for-byte identical to
the one in the edge (``src/yvy/infrastructure/auth/canonical_json.py``).
See ``YVY_FASE9_Arquitectura_v2.md`` §3.2 and §11.10.
"""

from yvy_server.domain.auth.canonical_json import canonicalize
from yvy_server.domain.auth.exceptions import (
    ClockSkewError,
    CredentialNotFoundError,
    EnvelopeError,
    EnvelopeShapeError,
    HmacMismatchError,
    PayloadSchemaError,
    StationNotFoundError,
)

__all__ = [
    "ClockSkewError",
    "CredentialNotFoundError",
    "EnvelopeError",
    "EnvelopeShapeError",
    "HmacMismatchError",
    "PayloadSchemaError",
    "StationNotFoundError",
    "canonicalize",
]
