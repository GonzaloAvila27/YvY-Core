"""Unit tests for IngestSensorUseCase and IngestEventUseCase.

The use cases share the same base-class pipeline (``_BaseIngestUseCase``);
these tests verify the concrete subclasses wire the right ENDPOINT and
ENTITY_TYPE, and that the shared pipeline handles both success and
failure branches correctly for the new endpoints.

Strategy: fake repos + fake validator/publisher, no DB, no async I/O.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from yvy_server.application.use_cases import (
    IngestEventUseCase,
    IngestFailure,
    IngestSensorUseCase,
    IngestSuccess,
)
from yvy_server.domain.auth import canonicalize
from yvy_server.domain.ingest import EntityType
from yvy_server.domain.station import Station, StationCredential


# ─── Fakes ────────────────────────────────────────────────────────────────


@dataclass
class _FakePublishResult:
    was_duplicate: bool = False
    topic: str = "test.topic"
    transport: str = "postgres_direct"


class _FakePublisher:
    async def publish(self, event: Any) -> _FakePublishResult:
        return _FakePublishResult()


class _FakePublisherDuplicate:
    async def publish(self, event: Any) -> _FakePublishResult:
        return _FakePublishResult(was_duplicate=True)


class _FakePublisherFails:
    async def publish(self, event: Any) -> _FakePublishResult:
        raise RuntimeError("DB down")


@dataclass
class _LoggedAttempt:
    endpoint: str
    http_status: int
    failure_class: str | None


class _FakeLogRepo:
    attempts: list[_LoggedAttempt] = field(default_factory=list)

    def __init__(self) -> None:
        self.attempts = []

    async def record_attempt(self, session: Any, **kwargs: Any) -> None:
        self.attempts.append(
            _LoggedAttempt(
                endpoint=kwargs["endpoint"],
                http_status=kwargs["http_status"],
                failure_class=kwargs["failure_class"],
            )
        )


class _FakeFailuresRepo:
    recorded: list[dict] = field(default_factory=list)

    def __init__(self) -> None:
        self.recorded = []

    async def record_failure(self, session: Any, **kwargs: Any) -> None:
        self.recorded.append(kwargs)


@dataclass
class _FakeValidatedEnvelope:
    station: Any
    credential: Any
    skew_seconds: int = 0


_STATION_UUID = uuid4()
_KEY_BYTES = bytes.fromhex("a" * 64)

_FAKE_STATION = Station(
    station_uuid=_STATION_UUID,
    name="Test IEA",
    description=None,
    location_lat=None,
    location_lon=None,
    location_name=None,
    created_at=datetime.now(UTC),
    deactivated_at=None,
)
_FAKE_CREDENTIAL = StationCredential(
    credential_id=1,
    station_uuid=_STATION_UUID,
    key_version=1,
    key_hmac_hex="a" * 64,
)


class _FakeValidator:
    async def validate(
        self, session: Any, envelope: dict, received_at_utc: datetime
    ) -> _FakeValidatedEnvelope:
        return _FakeValidatedEnvelope(station=_FAKE_STATION, credential=_FAKE_CREDENTIAL)


class _FakeValidatorFails:
    """Raises an AUTH error to simulate envelope validation failure."""

    async def validate(
        self, session: Any, envelope: dict, received_at_utc: datetime
    ) -> _FakeValidatedEnvelope:
        from yvy_server.domain.auth import HmacMismatchError

        raise HmacMismatchError("bad sig")


class _FakeSession:
    """Minimal fake AsyncSession — supports ``session.begin()``."""

    def add(self, obj: Any) -> None:
        pass

    @asynccontextmanager
    async def begin(self):  # type: ignore[misc]
        yield


@asynccontextmanager
async def _fake_session_factory():  # type: ignore[misc]
    yield _FakeSession()


def _make_envelope(payload: dict, event_id: UUID | None = None) -> dict:
    """Build a minimal valid-looking envelope dict."""
    eid = event_id or uuid4()
    env = {
        "schema_version": "1.0",
        "event_id": str(eid),
        "station_uuid": str(_STATION_UUID),
        "key_version": 1,
        "sent_at_utc": datetime.now(UTC).isoformat(),
        "nonce": "aaaa" * 8,
        "payload": payload,
    }
    env["signature"] = hmac.new(
        _KEY_BYTES,
        canonicalize({k: v for k, v in env.items()}),
        hashlib.sha256,
    ).hexdigest()
    return env


def _build_sensor_uc(
    *,
    publisher: Any = None,
    validator: Any = None,
    log_repo: Any = None,
    failures_repo: Any = None,
) -> IngestSensorUseCase:
    return IngestSensorUseCase(
        validator=validator or _FakeValidator(),
        publisher=publisher or _FakePublisher(),
        log_repo=log_repo or _FakeLogRepo(),
        failures_repo=failures_repo or _FakeFailuresRepo(),
        session_factory=_fake_session_factory,
        producer_version="test",
    )


def _build_event_uc(
    *,
    publisher: Any = None,
    validator: Any = None,
    log_repo: Any = None,
    failures_repo: Any = None,
) -> IngestEventUseCase:
    return IngestEventUseCase(
        validator=validator or _FakeValidator(),
        publisher=publisher or _FakePublisher(),
        log_repo=log_repo or _FakeLogRepo(),
        failures_repo=failures_repo or _FakeFailuresRepo(),
        session_factory=_fake_session_factory,
        producer_version="test",
    )


# ─── Tests: ENDPOINT / ENTITY_TYPE constants ─────────────────────────────


@pytest.mark.unit
def test_sensor_use_case_endpoint_and_entity_type() -> None:
    assert IngestSensorUseCase.ENDPOINT == "/api/v1/ingest/sensor"
    assert IngestSensorUseCase.ENTITY_TYPE == EntityType.SENSOR_READING


@pytest.mark.unit
def test_event_use_case_endpoint_and_entity_type() -> None:
    assert IngestEventUseCase.ENDPOINT == "/api/v1/ingest/event"
    assert IngestEventUseCase.ENTITY_TYPE == EntityType.EMPIRICAL_EVENT


# ─── Tests: sensor use case success path ─────────────────────────────────


@pytest.mark.unit
def test_sensor_success_returns_ingest_success() -> None:
    uc = _build_sensor_uc()
    envelope = _make_envelope(
        {"observed_at": "2026-06-01T12:00:00Z", "event_id": str(uuid4())}
    )
    result = asyncio.run(
        uc.execute(
            envelope=envelope,
            received_at_utc=datetime.now(UTC),
            received_from_ip="1.2.3.4",
            user_agent="pytest",
        )
    )
    assert isinstance(result, IngestSuccess)
    assert not result.was_duplicate


@pytest.mark.unit
def test_sensor_duplicate_returns_ingest_success_with_flag() -> None:
    uc = _build_sensor_uc(publisher=_FakePublisherDuplicate())
    envelope = _make_envelope(
        {"observed_at": "2026-06-01T12:00:00Z", "event_id": str(uuid4())}
    )
    result = asyncio.run(
        uc.execute(
            envelope=envelope,
            received_at_utc=datetime.now(UTC),
            received_from_ip=None,
            user_agent=None,
        )
    )
    assert isinstance(result, IngestSuccess)
    assert result.was_duplicate


@pytest.mark.unit
def test_sensor_auth_failure_returns_ingest_failure() -> None:
    uc = _build_sensor_uc(validator=_FakeValidatorFails())
    envelope = _make_envelope({"observed_at": "2026-06-01T12:00:00Z", "event_id": str(uuid4())})
    result = asyncio.run(
        uc.execute(
            envelope=envelope,
            received_at_utc=datetime.now(UTC),
            received_from_ip=None,
            user_agent=None,
        )
    )
    assert isinstance(result, IngestFailure)
    assert result.error_class == "AUTH"
    assert result.http_status == 401


@pytest.mark.unit
def test_sensor_publish_failure_returns_server_error() -> None:
    uc = _build_sensor_uc(publisher=_FakePublisherFails())
    envelope = _make_envelope({"observed_at": "2026-06-01T12:00:00Z", "event_id": str(uuid4())})
    result = asyncio.run(
        uc.execute(
            envelope=envelope,
            received_at_utc=datetime.now(UTC),
            received_from_ip=None,
            user_agent=None,
        )
    )
    assert isinstance(result, IngestFailure)
    assert result.error_class == "SERVER"
    assert result.http_status == 500


@pytest.mark.unit
def test_sensor_payload_validation_failure_goes_to_dlq() -> None:
    failures_repo = _FakeFailuresRepo()
    uc = _build_sensor_uc(failures_repo=failures_repo)

    envelope = _make_envelope({"observed_at": "2026-06-01T12:00:00Z", "event_id": str(uuid4())})

    def _bad_validator(payload: dict) -> None:
        raise ValueError("missing required field")

    result = asyncio.run(
        uc.execute(
            envelope=envelope,
            received_at_utc=datetime.now(UTC),
            received_from_ip="1.2.3.4",
            user_agent=None,
            payload_validator=_bad_validator,
        )
    )
    assert isinstance(result, IngestFailure)
    assert result.error_class == "SCHEMA"
    assert len(failures_repo.recorded) == 1
    assert failures_repo.recorded[0]["failure_class"] == "SCHEMA"


# ─── Tests: event use case success path ──────────────────────────────────


@pytest.mark.unit
def test_event_success_returns_ingest_success() -> None:
    uc = _build_event_uc()
    envelope = _make_envelope(
        {
            "event_record_id": str(uuid4()),
            "observation_date": "2026-06-01",
            "event_type": "frost",
        }
    )
    result = asyncio.run(
        uc.execute(
            envelope=envelope,
            received_at_utc=datetime.now(UTC),
            received_from_ip="10.0.0.1",
            user_agent="yvy-edge/0.9.1",
        )
    )
    assert isinstance(result, IngestSuccess)


@pytest.mark.unit
def test_sensor_logs_success_attempt() -> None:
    log_repo = _FakeLogRepo()
    uc = _build_sensor_uc(log_repo=log_repo)
    envelope = _make_envelope({"observed_at": "2026-06-01T12:00:00Z", "event_id": str(uuid4())})
    asyncio.run(
        uc.execute(
            envelope=envelope,
            received_at_utc=datetime.now(UTC),
            received_from_ip=None,
            user_agent=None,
        )
    )
    assert len(log_repo.attempts) == 1
    assert log_repo.attempts[0].http_status == 200
    assert log_repo.attempts[0].failure_class is None
    assert log_repo.attempts[0].endpoint == "/api/v1/ingest/sensor"


@pytest.mark.unit
def test_event_logs_auth_failure() -> None:
    log_repo = _FakeLogRepo()
    uc = _build_event_uc(validator=_FakeValidatorFails(), log_repo=log_repo)
    envelope = _make_envelope({"event_record_id": str(uuid4()), "observation_date": "2026-06-01", "event_type": "hail"})
    asyncio.run(
        uc.execute(
            envelope=envelope,
            received_at_utc=datetime.now(UTC),
            received_from_ip=None,
            user_agent=None,
        )
    )
    assert len(log_repo.attempts) == 1
    assert log_repo.attempts[0].http_status == 401
    assert log_repo.attempts[0].failure_class == "AUTH"
    assert log_repo.attempts[0].endpoint == "/api/v1/ingest/event"
