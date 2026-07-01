"""Canonical JSON v1.0 — YVY spec.

Single source of truth for the byte-exact serialization used when
computing HMAC signatures over envelopes. This file in the server is
the authoritative implementation. In Fase 9.2, the edge will adopt it
as a byte-for-byte vendor copy and replicate the golden tests in
``tests/unit/test_canonical_json.py``.

Spec (matches ``YVY_FASE9_Arquitectura_v2.md`` §3.2):

- ``sort_keys=True`` — keys in lexicographic order at every level
- ``separators=(",", ":")`` — no whitespace between tokens
- ``ensure_ascii=False`` — UTF-8 native, no `\\u00xx` escapes
- ``allow_nan=False`` — fail loudly on NaN / Infinity / -Infinity
- output encoded as UTF-8 bytes

Drift control
-------------

If this implementation ever needs to change in a way that produces
different bytes for the same input, the spec version constant
``CANONICAL_JSON_SPEC_VERSION`` MUST be bumped, and:

1. The golden tests in ``tests/unit/test_canonical_json.py`` must be
   regenerated with the new expected bytes.
2. The edge (after Fase 9.2 adopts this file) must be updated in
   lockstep and its golden tests regenerated identically.
3. Track B (Kafka producer) must be notified through the shared
   ``shared/canonical_json.py`` (if/when the shared repo exists).
"""

from __future__ import annotations

import json
from typing import Any

# Bump only when the algorithm changes in a non-byte-equivalent way.
# Edge and server must always share the same value.
CANONICAL_JSON_SPEC_VERSION = "1.0"


def canonicalize(obj: Any) -> bytes:
    """Serialize ``obj`` to canonical JSON bytes per YVY spec v1.0.

    Args:
        obj: A JSON-serializable object. Typically a ``dict`` representing
            an envelope minus the ``signature`` field, but any JSON-
            serializable value is accepted.

    Returns:
        The canonical UTF-8 byte sequence. This is what gets fed to
        HMAC-SHA256.

    Raises:
        ValueError: If ``obj`` contains NaN, Infinity, or -Infinity.
            This is intentional — those values have no canonical JSON
            representation in the YVY spec.
        TypeError: If ``obj`` contains values that ``json`` can't
            serialize (e.g. ``datetime``, ``UUID``). The caller is
            responsible for pre-stringifying those before passing in;
            in YVY's case, envelopes are received already as ``str``
            values for those fields, so this is fine.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
