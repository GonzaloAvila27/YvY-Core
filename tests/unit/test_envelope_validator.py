"""Unit tests for ``application/auth/envelope_validator.py``.

Two layers covered:

1. **Pure module-level functions** (``validate_shape``, ``verify_hmac``,
   ``check_clock_skew``). No DB, no async — direct calls with crafted
   inputs.

2. **`EnvelopeValidator` class** with fake repositories. Verifies the
   orchestration: order of validation steps, short-circuiting on early
   failure, exception attributes populated correctly.

Why fakes instead of ``unittest.mock``?

Fakes are tiny dict-backed classes that mirror the real repo's
public surface. They're more readable than ``MagicMock`` setups and
let us assert call ordering by recording the args.

Async testing strategy
----------------------

The validator's ``validate`` method is async. To avoid adding
``pytest-asyncio`` (or ``anyio``) as a dev dependency, each async test
wraps its body in ``asyncio.run(...)``. This works because each test
is a single self-contained event loop run.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from yvy_server.application.auth.envelope_validator import (
    EnvelopeValidator,
    ValidatedEnvelope,
    check_clock_skew,
    validate_shape,
    verify_hmac,
)
from yvy_server.domain.auth import (
    ClockSkewError,
    CredentialNotFoundError,
    EnvelopeShapeError,
    HmacMismatchError,
    StationNotFoundError,
    canonicalize,
)
from yvy_server.domain.station import Station, StationCredential

# ─── Helpers ─────────────────────────────────────────────────────────────


SUPPORTED_SCHEMAS = frozenset({"1.0"})
MAX_SKEW_SECONDS = 1800

# A canonical valid envelope for reuse. Tests that need a specific
# malformed shape build from this with one field changed/removed.
_STATION_UUID = UUID("00000000-0000-0000-0000-000000000001")
_EVENT_ID = UUID("11111111-1111-1111-1111-111111111111")
_KEY_BYTES = bytes.fromhex("a" * 64)


def _valid_envelope_no_sig(
    *,
    sent_at_utc: str = "2026-05-21T18:00:00.000Z",
    station_uuid: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    """Build a valid envelope dict WITHOUT the signature field.

    Tests that need a signed envelope call :func:`_sign` on the result.
    """
    return {
        "schema_version": "1.0",
        "event_id": event_id or str(_EVENT_ID),
        "station_uuid": station_uuid or str(_STATION_UUID),
        "key_version": 1,
        "sent_at_utc": sent_at_utc,
        "nonce": "abcd" * 12,
        "payload": {"observed_at_utc": sent_at_utc, "observer_id": "u"},
    }


def _sign(envelope_no_sig: dict[str, Any], key: bytes = _KEY_BYTES) -> dict[str, Any]:
    """Add a valid HMAC signature to an envelope-without-signature."""
    canonical = canonicalize(envelope_no_sig)
    sig = hmac.new(key, canonical, hashlib.sha256).hexdigest()
    return {**envelope_no_sig, "signature": sig}


# ─── 1. validate_shape ───────────────────────────────────────────────────


class TestValidateShape:
    """Pre-HMAC structural check."""

    def test_complete_envelope_passes(self) -> None:
        envelope = _sign(_valid_envelope_no_sig())
        # No exception means pass
        validate_shape(envelope, SUPPORTED_SCHEMAS)

    def test_missing_schema_version_raises(self) -> None:
        envelope = _sign(_valid_envelope_no_sig())
        del envelope["schema_version"]
        with pytest.raises(EnvelopeShapeError, match="schema_version"):
            validate_shape(envelope, SUPPORTED_SCHEMAS)

    def test_missing_signature_raises(self) -> None:
        envelope = _valid_envelope_no_sig()  # no signature
        with pytest.raises(EnvelopeShapeError, match="signature"):
            validate_shape(envelope, SUPPORTED_SCHEMAS)

    def test_missing_event_id_raises(self) -> None:
        envelope = _sign(_valid_envelope_no_sig())
        del envelope["event_id"]
        with pytest.raises(EnvelopeShapeError, match="event_id"):
            validate_shape(envelope, SUPPORTED_SCHEMAS)

    def test_missing_multiple_fields_lists_all(self) -> None:
        envelope = {"schema_version": "1.0"}  # everything else missing
        with pytest.raises(EnvelopeShapeError) as excinfo:
            validate_shape(envelope, SUPPORTED_SCHEMAS)
        msg = str(excinfo.value)
        # At least the most relevant fields should appear in the message
        for field_name in ["event_id", "station_uuid", "signature", "nonce", "payload"]:
            assert field_name in msg, f"{field_name!r} should be in error message"

    def test_unsupported_schema_version_raises(self) -> None:
        envelope = _sign(_valid_envelope_no_sig())
        envelope["schema_version"] = "9.9"
        with pytest.raises(EnvelopeShapeError, match=r"9\.9"):
            validate_shape(envelope, SUPPORTED_SCHEMAS)

    def test_unsupported_schema_version_message_lists_supported(self) -> None:
        envelope = _sign(_valid_envelope_no_sig())
        envelope["schema_version"] = "2.0"
        with pytest.raises(EnvelopeShapeError) as excinfo:
            validate_shape(envelope, SUPPORTED_SCHEMAS)
        assert "1.0" in str(excinfo.value)

    def test_extra_fields_are_tolerated(self) -> None:
        envelope = _sign(_valid_envelope_no_sig())
        envelope["future_field"] = "added in 2027"
        # Forward compatibility: shape check only requires the known fields.
        validate_shape(envelope, SUPPORTED_SCHEMAS)

    def test_empty_envelope_raises(self) -> None:
        with pytest.raises(EnvelopeShapeError):
            validate_shape({}, SUPPORTED_SCHEMAS)

    def test_multiple_supported_schemas_accepts_any(self) -> None:
        envelope = _sign(_valid_envelope_no_sig())
        envelope["schema_version"] = "1.1"
        # Validator configured with multiple versions
        validate_shape(envelope, frozenset({"1.0", "1.1", "2.0"}))


# ─── 2. verify_hmac ──────────────────────────────────────────────────────


class TestVerifyHmac:
    """HMAC-SHA256 signature verification."""

    def test_valid_signature_passes(self) -> None:
        envelope = _sign(_valid_envelope_no_sig())
        # No exception
        verify_hmac(envelope, _KEY_BYTES)

    def test_invalid_signature_raises(self) -> None:
        envelope = _sign(_valid_envelope_no_sig())
        envelope["signature"] = "0" * 64  # valid hex format, wrong value
        with pytest.raises(HmacMismatchError):
            verify_hmac(envelope, _KEY_BYTES)

    def test_wrong_key_raises(self) -> None:
        envelope = _sign(_valid_envelope_no_sig(), key=_KEY_BYTES)
        other_key = bytes.fromhex("b" * 64)
        with pytest.raises(HmacMismatchError):
            verify_hmac(envelope, other_key)

    def test_tampered_payload_raises(self) -> None:
        envelope = _sign(_valid_envelope_no_sig())
        # Tamper after signing
        envelope["payload"] = {"observed_at_utc": "tampered", "observer_id": "x"}
        with pytest.raises(HmacMismatchError):
            verify_hmac(envelope, _KEY_BYTES)

    def test_tampered_event_id_raises(self) -> None:
        envelope = _sign(_valid_envelope_no_sig())
        envelope["event_id"] = str(uuid4())
        with pytest.raises(HmacMismatchError):
            verify_hmac(envelope, _KEY_BYTES)

    def test_signature_field_excluded_from_canonical(self) -> None:
        """Sanity check: changing the signature value should NOT affect
        the canonical bytes (signature is excluded from canonicalize).

        We verify this indirectly: a valid envelope passes, and the same
        envelope with a different (still hex) signature fails — meaning
        the canonical bytes were computed without the signature field.
        """
        envelope = _sign(_valid_envelope_no_sig())
        verify_hmac(envelope, _KEY_BYTES)  # passes
        envelope["signature"] = "f" * 64
        with pytest.raises(HmacMismatchError):
            verify_hmac(envelope, _KEY_BYTES)

    def test_exception_carries_station_uuid_and_event_id(self) -> None:
        envelope = _sign(_valid_envelope_no_sig())
        envelope["signature"] = "0" * 64
        sta = uuid4()
        evt = uuid4()
        with pytest.raises(HmacMismatchError) as excinfo:
            verify_hmac(envelope, _KEY_BYTES, station_uuid=sta, event_id=evt)
        assert excinfo.value.station_uuid == sta
        assert excinfo.value.event_id == evt

    def test_exception_attributes_default_to_none(self) -> None:
        envelope = _sign(_valid_envelope_no_sig())
        envelope["signature"] = "0" * 64
        with pytest.raises(HmacMismatchError) as excinfo:
            verify_hmac(envelope, _KEY_BYTES)
        assert excinfo.value.station_uuid is None
        assert excinfo.value.event_id is None
        assert excinfo.value.skew_seconds is None


# ─── 3. check_clock_skew ─────────────────────────────────────────────────


class TestCheckClockSkew:
    """Clock skew check between sent_at_utc and received_at_utc."""

    def _envelope_with_sent_at(self, sent_at: datetime) -> dict[str, Any]:
        iso = sent_at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        return _valid_envelope_no_sig(sent_at_utc=iso)

    def test_zero_skew_returns_zero(self) -> None:
        now = datetime(2026, 5, 21, 18, 0, 0, tzinfo=UTC)
        envelope = self._envelope_with_sent_at(now)
        result = check_clock_skew(envelope, now, MAX_SKEW_SECONDS)
        assert result == 0

    def test_small_skew_within_limit_returns_value(self) -> None:
        sent = datetime(2026, 5, 21, 18, 0, 0, tzinfo=UTC)
        received = sent + timedelta(seconds=10)
        envelope = self._envelope_with_sent_at(sent)
        result = check_clock_skew(envelope, received, MAX_SKEW_SECONDS)
        assert result == 10

    def test_skew_exactly_at_limit_passes(self) -> None:
        """Boundary: skew == max_seconds is allowed (strict ``>`` in code)."""
        sent = datetime(2026, 5, 21, 18, 0, 0, tzinfo=UTC)
        received = sent + timedelta(seconds=MAX_SKEW_SECONDS)
        envelope = self._envelope_with_sent_at(sent)
        result = check_clock_skew(envelope, received, MAX_SKEW_SECONDS)
        assert result == MAX_SKEW_SECONDS

    def test_skew_one_above_limit_raises(self) -> None:
        sent = datetime(2026, 5, 21, 18, 0, 0, tzinfo=UTC)
        received = sent + timedelta(seconds=MAX_SKEW_SECONDS + 1)
        envelope = self._envelope_with_sent_at(sent)
        with pytest.raises(ClockSkewError) as excinfo:
            check_clock_skew(envelope, received, MAX_SKEW_SECONDS)
        assert excinfo.value.skew_seconds == MAX_SKEW_SECONDS + 1

    def test_skew_sent_in_the_future_uses_abs(self) -> None:
        """sent_at in the future of received_at: still measured as positive skew."""
        received = datetime(2026, 5, 21, 18, 0, 0, tzinfo=UTC)
        sent = received + timedelta(seconds=3700)
        envelope = self._envelope_with_sent_at(sent)
        with pytest.raises(ClockSkewError) as excinfo:
            check_clock_skew(envelope, received, MAX_SKEW_SECONDS)
        assert excinfo.value.skew_seconds == 3700

    def test_skew_sent_in_the_past_large(self) -> None:
        sent = datetime(2026, 5, 21, 18, 0, 0, tzinfo=UTC)
        received = sent + timedelta(hours=2)  # 7200s, way over limit
        envelope = self._envelope_with_sent_at(sent)
        with pytest.raises(ClockSkewError) as excinfo:
            check_clock_skew(envelope, received, MAX_SKEW_SECONDS)
        assert excinfo.value.skew_seconds == 7200

    def test_exception_carries_station_uuid_and_event_id(self) -> None:
        sent = datetime(2026, 5, 21, 18, 0, 0, tzinfo=UTC)
        received = sent + timedelta(seconds=3700)
        envelope = self._envelope_with_sent_at(sent)
        sta = uuid4()
        evt = uuid4()
        with pytest.raises(ClockSkewError) as excinfo:
            check_clock_skew(
                envelope,
                received,
                MAX_SKEW_SECONDS,
                station_uuid=sta,
                event_id=evt,
            )
        assert excinfo.value.station_uuid == sta
        assert excinfo.value.event_id == evt
        assert excinfo.value.skew_seconds == 3700

    def test_accepts_iso_with_plus_zero_offset(self) -> None:
        """Server might receive sent_at_utc as ``+00:00`` instead of ``Z``."""
        envelope = _valid_envelope_no_sig(sent_at_utc="2026-05-21T18:00:00+00:00")
        received = datetime(2026, 5, 21, 18, 0, 0, tzinfo=UTC)
        result = check_clock_skew(envelope, received, MAX_SKEW_SECONDS)
        assert result == 0


# ─── 4. EnvelopeValidator class with fake repos ──────────────────────────


@dataclass
class _FakeStationRepo:
    """In-memory station repo. Records every call for assertion."""

    stations: dict[UUID, Station] = field(default_factory=dict)
    get_calls: list[UUID] = field(default_factory=list)

    def add(self, station: Station) -> None:
        self.stations[station.station_uuid] = station

    async def get_by_uuid(self, session: Any, station_uuid: UUID) -> Station | None:
        self.get_calls.append(station_uuid)
        return self.stations.get(station_uuid)


@dataclass
class _FakeCredentialRepo:
    """In-memory credential repo. Records every call for assertion."""

    creds: dict[tuple[UUID, int], StationCredential] = field(default_factory=dict)
    get_calls: list[tuple[UUID, int]] = field(default_factory=list)

    def add(self, cred: StationCredential) -> None:
        self.creds[(cred.station_uuid, cred.key_version)] = cred

    async def get_active_by_station_and_version(
        self,
        session: Any,
        station_uuid: UUID,
        key_version: int,
    ) -> StationCredential | None:
        self.get_calls.append((station_uuid, key_version))
        cred = self.creds.get((station_uuid, key_version))
        # Mirror the real repo's filter on deactivated_at
        if cred is None or not cred.is_active:
            return None
        return cred


def _make_station(uuid: UUID = _STATION_UUID) -> Station:
    return Station(
        station_uuid=uuid,
        name="test",
        description=None,
        location_lat=None,
        location_lon=None,
        location_name=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        deactivated_at=None,
    )


def _make_credential(
    station_uuid: UUID = _STATION_UUID,
    key_version: int = 1,
    key_hex: str = "a" * 64,
    deactivated: bool = False,
) -> StationCredential:
    return StationCredential(
        credential_id=1,
        station_uuid=station_uuid,
        key_version=key_version,
        key_hmac_hex=key_hex,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        deactivated_at=datetime(2026, 1, 2, tzinfo=UTC) if deactivated else None,
        reason=None,
    )


def _make_validator(
    station_repo: _FakeStationRepo,
    cred_repo: _FakeCredentialRepo,
) -> EnvelopeValidator:
    return EnvelopeValidator(
        station_repo=station_repo,  # type: ignore[arg-type]  # fakes match protocol
        credential_repo=cred_repo,  # type: ignore[arg-type]
        supported_schemas=SUPPORTED_SCHEMAS,
        max_skew_seconds=MAX_SKEW_SECONDS,
    )


# A sentinel session — fakes ignore it
_SESSION: Any = object()


class TestEnvelopeValidator:
    """Integration of the pure functions with the repos."""

    def test_happy_path_returns_validated_envelope(self) -> None:
        station_repo = _FakeStationRepo()
        station_repo.add(_make_station())
        cred_repo = _FakeCredentialRepo()
        cred_repo.add(_make_credential())
        validator = _make_validator(station_repo, cred_repo)

        now = datetime.now(UTC)
        envelope = _sign(
            _valid_envelope_no_sig(
                sent_at_utc=now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            )
        )
        result = asyncio.run(validator.validate(_SESSION, envelope, now))

        assert isinstance(result, ValidatedEnvelope)
        assert result.station.station_uuid == _STATION_UUID
        assert result.credential.key_version == 1
        assert result.skew_seconds == 0
        assert result.received_at_utc == now

    def test_shape_failure_short_circuits_before_db_calls(self) -> None:
        """If shape fails, no repo call is made."""
        station_repo = _FakeStationRepo()
        cred_repo = _FakeCredentialRepo()
        validator = _make_validator(station_repo, cred_repo)

        envelope = _valid_envelope_no_sig()
        del envelope["nonce"]  # malformed

        with pytest.raises(EnvelopeShapeError):
            asyncio.run(validator.validate(_SESSION, envelope, datetime.now(UTC)))

        assert station_repo.get_calls == []
        assert cred_repo.get_calls == []

    def test_unknown_station_raises_without_querying_credential(self) -> None:
        station_repo = _FakeStationRepo()  # empty — station not registered
        cred_repo = _FakeCredentialRepo()
        validator = _make_validator(station_repo, cred_repo)

        envelope = _sign(_valid_envelope_no_sig())
        with pytest.raises(StationNotFoundError) as excinfo:
            asyncio.run(validator.validate(_SESSION, envelope, datetime.now(UTC)))

        # Station was queried once
        assert station_repo.get_calls == [_STATION_UUID]
        # Credential was NOT queried (short-circuit)
        assert cred_repo.get_calls == []
        # Exception carries identifiers
        assert excinfo.value.station_uuid == _STATION_UUID
        assert excinfo.value.event_id == _EVENT_ID

    def test_unknown_credential_raises_credential_not_found(self) -> None:
        station_repo = _FakeStationRepo()
        station_repo.add(_make_station())
        cred_repo = _FakeCredentialRepo()  # empty
        validator = _make_validator(station_repo, cred_repo)

        envelope = _sign(_valid_envelope_no_sig())
        with pytest.raises(CredentialNotFoundError) as excinfo:
            asyncio.run(validator.validate(_SESSION, envelope, datetime.now(UTC)))

        assert station_repo.get_calls == [_STATION_UUID]
        assert cred_repo.get_calls == [(_STATION_UUID, 1)]
        assert excinfo.value.station_uuid == _STATION_UUID

    def test_deactivated_credential_raises_credential_not_found(self) -> None:
        """A row exists but ``deactivated_at IS NOT NULL`` → treated as missing."""
        station_repo = _FakeStationRepo()
        station_repo.add(_make_station())
        cred_repo = _FakeCredentialRepo()
        cred_repo.add(_make_credential(deactivated=True))
        validator = _make_validator(station_repo, cred_repo)

        envelope = _sign(_valid_envelope_no_sig())
        with pytest.raises(CredentialNotFoundError):
            asyncio.run(validator.validate(_SESSION, envelope, datetime.now(UTC)))

    def test_bad_hmac_raises_after_credential_lookup(self) -> None:
        station_repo = _FakeStationRepo()
        station_repo.add(_make_station())
        cred_repo = _FakeCredentialRepo()
        cred_repo.add(_make_credential())
        validator = _make_validator(station_repo, cred_repo)

        envelope = _sign(_valid_envelope_no_sig())
        envelope["signature"] = "0" * 64  # tamper after signing

        with pytest.raises(HmacMismatchError) as excinfo:
            asyncio.run(validator.validate(_SESSION, envelope, datetime.now(UTC)))

        # All lookups happened before HMAC check
        assert station_repo.get_calls == [_STATION_UUID]
        assert cred_repo.get_calls == [(_STATION_UUID, 1)]
        assert excinfo.value.station_uuid == _STATION_UUID
        assert excinfo.value.event_id == _EVENT_ID

    def test_clock_skew_raises_last_with_skew_value(self) -> None:
        station_repo = _FakeStationRepo()
        station_repo.add(_make_station())
        cred_repo = _FakeCredentialRepo()
        cred_repo.add(_make_credential())
        validator = _make_validator(station_repo, cred_repo)

        sent = datetime(2026, 5, 21, 18, 0, 0, tzinfo=UTC)
        envelope = _sign(
            _valid_envelope_no_sig(
                sent_at_utc=sent.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            )
        )
        received = sent + timedelta(seconds=3700)

        with pytest.raises(ClockSkewError) as excinfo:
            asyncio.run(validator.validate(_SESSION, envelope, received))

        assert excinfo.value.skew_seconds == 3700
        assert excinfo.value.station_uuid == _STATION_UUID
        assert excinfo.value.event_id == _EVENT_ID

    def test_wrong_key_version_raises_credential_not_found(self) -> None:
        """``key_version=99`` in envelope, only version 1 in DB."""
        station_repo = _FakeStationRepo()
        station_repo.add(_make_station())
        cred_repo = _FakeCredentialRepo()
        cred_repo.add(_make_credential(key_version=1))
        validator = _make_validator(station_repo, cred_repo)

        env_no_sig = _valid_envelope_no_sig()
        env_no_sig["key_version"] = 99
        envelope = _sign(env_no_sig)

        with pytest.raises(CredentialNotFoundError):
            asyncio.run(validator.validate(_SESSION, envelope, datetime.now(UTC)))

        # Credential repo WAS called (we look up to discover absence)
        assert cred_repo.get_calls == [(_STATION_UUID, 99)]

    def test_validated_envelope_carries_received_at(self) -> None:
        """The ValidatedEnvelope contains the exact received_at_utc passed in."""
        station_repo = _FakeStationRepo()
        station_repo.add(_make_station())
        cred_repo = _FakeCredentialRepo()
        cred_repo.add(_make_credential())
        validator = _make_validator(station_repo, cred_repo)

        envelope = _sign(_valid_envelope_no_sig())
        specific_received_at = datetime(2026, 5, 21, 18, 0, 5, 123456, tzinfo=UTC)
        result = asyncio.run(validator.validate(_SESSION, envelope, specific_received_at))

        assert result.received_at_utc == specific_received_at


# ─── 5. Valores presentes pero malformados → SCHEMA, nunca 500 (QW4) ─────


class TestMalformedFieldValues:
    """Campos presentes pero con valores no coercionables.

    Antes del fix QW4 (jul-2026) estos casos levantaban ``ValueError``
    crudo, que escapaba del use case como 500 SERVER → el Edge lo
    reintentaba para siempre (mensaje veneno). El contrato correcto es
    400 SCHEMA (``EnvelopeShapeError``) → DLQ inmediato en el Edge.
    """

    def _validator_with_data(self) -> EnvelopeValidator:
        station_repo = _FakeStationRepo()
        station_repo.add(_make_station())
        cred_repo = _FakeCredentialRepo()
        cred_repo.add(_make_credential())
        return _make_validator(station_repo, cred_repo)

    def test_station_uuid_not_a_uuid_raises_shape_error(self) -> None:
        validator = self._validator_with_data()
        envelope = _sign(_valid_envelope_no_sig())
        envelope["station_uuid"] = "banana"

        with pytest.raises(EnvelopeShapeError):
            asyncio.run(validator.validate(_SESSION, envelope, datetime.now(UTC)))

    def test_event_id_not_a_uuid_raises_shape_error(self) -> None:
        validator = self._validator_with_data()
        envelope = _sign(_valid_envelope_no_sig())
        envelope["event_id"] = "no-soy-un-uuid"

        with pytest.raises(EnvelopeShapeError):
            asyncio.run(validator.validate(_SESSION, envelope, datetime.now(UTC)))

    def test_key_version_not_an_int_raises_shape_error(self) -> None:
        validator = self._validator_with_data()
        envelope = _sign(_valid_envelope_no_sig())
        envelope["key_version"] = "x"

        with pytest.raises(EnvelopeShapeError):
            asyncio.run(validator.validate(_SESSION, envelope, datetime.now(UTC)))

    def test_key_version_none_raises_shape_error(self) -> None:
        """None dispara TypeError en int() — también debe ser SCHEMA."""
        validator = self._validator_with_data()
        envelope = _sign(_valid_envelope_no_sig())
        envelope["key_version"] = None

        with pytest.raises(EnvelopeShapeError):
            asyncio.run(validator.validate(_SESSION, envelope, datetime.now(UTC)))

    def test_malformed_field_short_circuits_before_db(self) -> None:
        """La coerción falla ANTES de tocar los repos."""
        station_repo = _FakeStationRepo()
        cred_repo = _FakeCredentialRepo()
        validator = _make_validator(station_repo, cred_repo)

        envelope = _sign(_valid_envelope_no_sig())
        envelope["station_uuid"] = "banana"

        with pytest.raises(EnvelopeShapeError):
            asyncio.run(validator.validate(_SESSION, envelope, datetime.now(UTC)))

        assert station_repo.get_calls == []
        assert cred_repo.get_calls == []

    def test_malformed_sent_at_utc_raises_shape_error_post_hmac(self) -> None:
        """sent_at_utc firmado pero no parseable como ISO → SCHEMA (no CLOCK ni 500).

        La firma HMAC cubre el string tal cual, así que un envelope
        firmado con ``sent_at_utc="ayer"`` pasa HMAC y recién falla al
        parsear el timestamp.
        """
        validator = self._validator_with_data()
        envelope = _sign(_valid_envelope_no_sig(sent_at_utc="ayer"))

        with pytest.raises(EnvelopeShapeError) as excinfo:
            asyncio.run(validator.validate(_SESSION, envelope, datetime.now(UTC)))

        # Post-HMAC: la excepción ya conoce la estación
        assert excinfo.value.station_uuid == _STATION_UUID
        assert excinfo.value.event_id == _EVENT_ID

    def test_check_clock_skew_malformed_timestamp_direct(self) -> None:
        """La función pura también traduce ValueError → EnvelopeShapeError."""
        envelope = _valid_envelope_no_sig(sent_at_utc="2026-13-45T99:99:99Z")

        with pytest.raises(EnvelopeShapeError):
            check_clock_skew(envelope, datetime.now(UTC), MAX_SKEW_SECONDS)
