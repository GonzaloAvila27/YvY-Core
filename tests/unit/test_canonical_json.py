"""Golden tests for the Canonical JSON v1.0 serializer.

These tests assert the EXACT byte output for a set of representative
inputs. They serve as the contract between the server and the edge:
the same file copied to the edge MUST produce the same bytes for the
same inputs, byte-for-byte.

If a test fails here:
    - Either the serializer changed (bug or intentional — bump the
      ``CANONICAL_JSON_SPEC_VERSION`` and update the goldens), or
    - The expected bytes are wrong (fix the goldens, not the code).

If the EDGE side fails the equivalent tests with the same goldens:
    - Edge and server have drifted. Sync immediately by re-vendoring.

These tests have no dependencies beyond stdlib + pytest + the module
under test, so they run anywhere without Postgres / Docker.
"""
from __future__ import annotations

import pytest

from yvy_server.domain.auth.canonical_json import (
    CANONICAL_JSON_SPEC_VERSION,
    canonicalize,
)

# ─── Sanity: spec version constant ───────────────────────────────────────


def test_spec_version_is_1_0() -> None:
    """If this changes, the edge must change in lockstep."""
    assert CANONICAL_JSON_SPEC_VERSION == "1.0"


# ─── Determinism ─────────────────────────────────────────────────────────


def test_canonicalize_is_deterministic() -> None:
    obj = {"b": 2, "a": 1}
    assert canonicalize(obj) == canonicalize(obj)


def test_canonicalize_returns_bytes() -> None:
    assert isinstance(canonicalize({"k": "v"}), bytes)


# ─── Golden cases: empty / minimal ───────────────────────────────────────


def test_empty_dict() -> None:
    assert canonicalize({}) == b"{}"


def test_empty_list() -> None:
    assert canonicalize([]) == b"[]"


def test_single_string_field() -> None:
    assert canonicalize({"k": "v"}) == b'{"k":"v"}'


def test_single_int_field() -> None:
    assert canonicalize({"n": 42}) == b'{"n":42}'


def test_null_field() -> None:
    assert canonicalize({"x": None}) == b'{"x":null}'


def test_bool_fields() -> None:
    assert canonicalize({"t": True, "f": False}) == b'{"f":false,"t":true}'


# ─── Golden cases: key ordering (alphabetic) ─────────────────────────────


def test_keys_are_sorted_alphabetically() -> None:
    assert canonicalize({"c": 3, "a": 1, "b": 2}) == b'{"a":1,"b":2,"c":3}'


def test_keys_sorted_nested() -> None:
    obj = {"outer": {"b": 2, "a": 1}}
    assert canonicalize(obj) == b'{"outer":{"a":1,"b":2}}'


def test_keys_sorted_deeply() -> None:
    obj = {"z": {"y": {"x": {"b": 2, "a": 1}}}}
    assert canonicalize(obj) == b'{"z":{"y":{"x":{"a":1,"b":2}}}}'


def test_array_order_preserved() -> None:
    """Arrays are ordered, not sorted."""
    assert canonicalize({"arr": [3, 1, 2]}) == b'{"arr":[3,1,2]}'


# ─── Golden cases: no whitespace ─────────────────────────────────────────


def test_no_space_between_kv() -> None:
    """Separator ``:`` has no space around it."""
    out = canonicalize({"k": "v", "n": 1})
    assert b": " not in out
    assert b" :" not in out


def test_no_space_between_items() -> None:
    """Separator ``,`` has no space around it."""
    out = canonicalize({"a": 1, "b": 2})
    assert b", " not in out
    assert b" ," not in out


# ─── Golden cases: UTF-8 native (no ascii-escape) ────────────────────────


def test_utf8_native_for_spanish() -> None:
    """``ensure_ascii=False`` means accented chars are emitted as-is."""
    result = canonicalize({"name": "Loretó"})
    assert result == '{"name":"Loretó"}'.encode()
    # And specifically NOT the escaped form
    assert b"\\u00f3" not in result


def test_utf8_native_for_guarani() -> None:
    """Names with Guaraní accents (relevant for the project)."""
    result = canonicalize({"location": "Ñu Guazú"})
    assert result == '{"location":"Ñu Guazú"}'.encode()
    assert b"\\u00d1" not in result
    assert b"\\u00fa" not in result


def test_utf8_native_for_emoji() -> None:
    """Even emoji stay as raw UTF-8 (multi-byte) sequences."""
    result = canonicalize({"flag": "🇦🇷"})
    # 🇦🇷 = regional indicator A (4 bytes) + regional indicator R (4 bytes)
    assert b'"flag":"\xf0\x9f\x87\xa6\xf0\x9f\x87\xb7"' in result


# ─── Golden cases: numbers ───────────────────────────────────────────────


def test_float_no_trailing_zeros_unless_integral() -> None:
    """Python's ``json`` emits floats compactly."""
    # 23.5 stays as 23.5
    assert canonicalize({"temp": 23.5}) == b'{"temp":23.5}'


def test_float_zero() -> None:
    # Python emits 0.0 as 0.0, not 0.
    assert canonicalize({"v": 0.0}) == b'{"v":0.0}'


def test_negative_number() -> None:
    assert canonicalize({"lat": -27.0333}) == b'{"lat":-27.0333}'


def test_large_int() -> None:
    assert canonicalize({"id": 9007199254740993}) == b'{"id":9007199254740993}'


# ─── Golden cases: NaN / Infinity rejection ──────────────────────────────


def test_nan_raises() -> None:
    with pytest.raises(ValueError, match="(?i)nan|out of range"):
        canonicalize({"v": float("nan")})


def test_positive_infinity_raises() -> None:
    with pytest.raises(ValueError, match="(?i)inf|out of range"):
        canonicalize({"v": float("inf")})


def test_negative_infinity_raises() -> None:
    with pytest.raises(ValueError, match="(?i)inf|out of range"):
        canonicalize({"v": float("-inf")})


# ─── Realistic golden: an envelope-minus-signature ───────────────────────


def test_realistic_envelope_payload_observation() -> None:
    """A full envelope shape (minus ``signature``) as we'd actually sign.

    This is the shape that ``hmac_validator`` will pass to
    ``canonicalize`` before computing HMAC. The expected bytes below
    are the single source of truth — edge must produce the same.
    """
    envelope_without_signature = {
        "schema_version": "1.0",
        "event_id": "550e8400-e29b-41d4-a716-446655440000",
        "station_uuid": "00000000-0000-0000-0000-000000000001",
        "key_version": 1,
        "sent_at_utc": "2026-05-16T15:30:00.000Z",
        "nonce": "a1b2c3d4e5f6789012345678901234567890abcdef12345678",
        "payload": {
            "observed_at_utc": "2026-05-16T15:30:00Z",
            "observer_id": "user_42",
            "rain_mm": 0,
            "temp_c_out": 23.5,
        },
    }

    expected = (
        b'{"event_id":"550e8400-e29b-41d4-a716-446655440000",'
        b'"key_version":1,'
        b'"nonce":"a1b2c3d4e5f6789012345678901234567890abcdef12345678",'
        b'"payload":{"observed_at_utc":"2026-05-16T15:30:00Z",'
        b'"observer_id":"user_42","rain_mm":0,"temp_c_out":23.5},'
        b'"schema_version":"1.0",'
        b'"sent_at_utc":"2026-05-16T15:30:00.000Z",'
        b'"station_uuid":"00000000-0000-0000-0000-000000000001"}'
    )
    assert canonicalize(envelope_without_signature) == expected


def test_realistic_envelope_payload_sensor() -> None:
    """A sensor envelope, smaller / numeric-heavier."""
    envelope_without_signature = {
        "schema_version": "1.0",
        "event_id": "00000000-0000-0000-0000-000000000010",
        "station_uuid": "00000000-0000-0000-0000-000000000001",
        "key_version": 1,
        "sent_at_utc": "2026-05-16T15:00:00.000Z",
        "nonce": "deadbeefcafebabe1234567890abcdef1234567890abcdef12",
        "payload": {
            "observed_at_utc": "2026-05-16T14:59:48Z",
            "temp_c_out": 21.3,
            "hum_pct_out": 78,
            "abs_pressure_hpa": 1013.2,
            "wind_dir_deg": 180,
        },
    }

    result = canonicalize(envelope_without_signature)

    # Check structural invariants we care about most:
    # 1. Keys are sorted at every level
    assert b'"event_id"' in result
    # 2. event_id comes before key_version (alphabetical at envelope level)
    assert result.index(b'"event_id"') < result.index(b'"key_version"')
    # 3. Inside payload, abs_pressure_hpa is first (alphabetical)
    payload_start = result.index(b'"payload":{') + len(b'"payload":{')
    assert result[payload_start:].startswith(b'"abs_pressure_hpa"')
    # 4. No whitespace anywhere
    assert b" " not in result
    # 5. Numbers emit cleanly
    assert b'"temp_c_out":21.3' in result
    assert b'"wind_dir_deg":180' in result


# ─── Robustness ──────────────────────────────────────────────────────────


def test_unicode_keys_sorted_by_codepoint() -> None:
    """Sort order is by Unicode codepoint, which means ASCII before Latin-1."""
    obj = {"ñ": 1, "n": 2, "a": 3}
    result = canonicalize(obj)
    # 'a' (0x61) < 'n' (0x6e) < 'ñ' (0xf1)
    assert result.index(b'"a"') < result.index(b'"n"') < result.index(b'"\xc3\xb1"')


def test_idempotent_through_recanonicalization() -> None:
    """``canonicalize(json.loads(canonicalize(x))) == canonicalize(x)``.

    This is the property that lets the server re-canonicalize a
    received envelope and compare to the bytes that the edge signed.
    """
    import json

    obj = {"b": [3, 2, 1], "a": {"y": "yes", "x": "no"}}
    first = canonicalize(obj)
    roundtripped = json.loads(first)
    second = canonicalize(roundtripped)
    assert first == second
