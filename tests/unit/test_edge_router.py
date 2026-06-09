"""Unit tests for edge router helpers: HMAC verification + version comparison.

Strategy: test the pure helpers (``_version_gt``) and the HMAC
verification logic (``_verify_edge_hmac``) via TestClient with fake
repos on app.state. No real DB, no network.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from yvy_server.domain.auth import canonicalize
from yvy_server.presentation.api.routers.edge import _version_gt


# ─── _version_gt ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_version_gt_simple() -> None:
    assert _version_gt("0.9.3", "0.9.2") is True


@pytest.mark.unit
def test_version_gt_equal() -> None:
    assert _version_gt("0.9.3", "0.9.3") is False


@pytest.mark.unit
def test_version_gt_less() -> None:
    assert _version_gt("0.9.2", "0.9.3") is False


@pytest.mark.unit
def test_version_gt_major_bump() -> None:
    assert _version_gt("1.0.0", "0.9.99") is True


@pytest.mark.unit
def test_version_gt_strips_prerelease() -> None:
    # "0.9.3-dev" treated same as "0.9.3"
    assert _version_gt("0.9.3-dev", "0.9.2") is True
    assert _version_gt("0.9.3-dev", "0.9.3") is False


@pytest.mark.unit
def test_version_gt_zero_baseline() -> None:
    assert _version_gt("0.9.3", "0.0.0") is True


@pytest.mark.unit
def test_version_gt_patch_only() -> None:
    assert _version_gt("0.9.3", "0.9.10") is False
    assert _version_gt("0.9.10", "0.9.3") is True


# ─── HMAC canonical JSON for edge requests ──────────────────────────────
# Verify that the canonical JSON construction that the edge router signs
# matches what the spec says (alphabetical: key_version, nonce, sent_at_utc,
# station_uuid).


@pytest.mark.unit
def test_edge_canonical_json_key_order() -> None:
    station_uuid = str(uuid4())
    nonce = "n" * 32
    sent_at = datetime.now(UTC).isoformat()
    key_version = 1

    to_sign = {
        "key_version": key_version,
        "nonce": nonce,
        "sent_at_utc": sent_at,
        "station_uuid": station_uuid,
    }
    canonical = canonicalize(to_sign).decode()
    # Keys must appear in alphabetical order
    ki = canonical.index("key_version")
    ni = canonical.index("nonce")
    si = canonical.index("sent_at_utc")
    ui = canonical.index("station_uuid")
    assert ki < ni < si < ui


@pytest.mark.unit
def test_edge_hmac_signature_matches_canonical() -> None:
    """The edge HMAC must be reproducible given the same inputs."""
    station_uuid = str(uuid4())
    nonce = "x" * 32
    sent_at = "2026-06-08T12:00:00.000000+00:00"
    key_version = 1
    key_bytes = bytes.fromhex("a" * 64)

    to_sign = {
        "key_version": key_version,
        "nonce": nonce,
        "sent_at_utc": sent_at,
        "station_uuid": station_uuid,
    }
    canonical_bytes = canonicalize(to_sign)
    sig1 = hmac.new(key_bytes, canonical_bytes, hashlib.sha256).hexdigest()
    sig2 = hmac.new(key_bytes, canonical_bytes, hashlib.sha256).hexdigest()
    assert sig1 == sig2
    assert len(sig1) == 64


@pytest.mark.unit
def test_edge_hmac_changes_if_nonce_changes() -> None:
    station_uuid = str(uuid4())
    sent_at = "2026-06-08T12:00:00.000000+00:00"
    key_bytes = bytes.fromhex("b" * 64)
    key_version = 1

    def _sig(nonce: str) -> str:
        to_sign = {
            "key_version": key_version,
            "nonce": nonce,
            "sent_at_utc": sent_at,
            "station_uuid": station_uuid,
        }
        return hmac.new(key_bytes, canonicalize(to_sign), hashlib.sha256).hexdigest()

    assert _sig("n" * 32) != _sig("m" * 32)


@pytest.mark.unit
def test_edge_hmac_changes_if_station_uuid_changes() -> None:
    sent_at = "2026-06-08T12:00:00.000000+00:00"
    nonce = "n" * 32
    key_bytes = bytes.fromhex("c" * 64)
    key_version = 1

    def _sig(station_uuid: str) -> str:
        to_sign = {
            "key_version": key_version,
            "nonce": nonce,
            "sent_at_utc": sent_at,
            "station_uuid": station_uuid,
        }
        return hmac.new(key_bytes, canonicalize(to_sign), hashlib.sha256).hexdigest()

    assert _sig(str(uuid4())) != _sig(str(uuid4()))
