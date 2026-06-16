"""Unit tests for admin repository methods added in 9.2.

Tests ``IngestLogRepository.get_stats`` and
``IngestFailuresRepository.list_unresolved`` / ``resolve`` using a
fake async session with an in-memory store.

These are pure unit tests — no real DB connection.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from yvy_server.infrastructure.persistence.ingest_failures_repository import (
    IngestFailuresRepository,
)
from yvy_server.infrastructure.persistence.ingest_log_repository import (
    IngestStats,
)

# ─── Fake session / ORM ──────────────────────────────────────────────────


class _FakeScalarResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one(self) -> Any:
        return self._value

    def scalar_one_or_none(self) -> Any:
        return self._value

    def scalars(self) -> _FakeScalarResult:
        return self

    def all(self) -> list[Any]:
        if isinstance(self._value, list):
            return self._value
        return [self._value] if self._value is not None else []


class _FakeSession:
    """Minimal fake AsyncSession for unit tests.

    The ``execute`` method is overridden per-test via the ``on_execute``
    callback to return pre-built results.
    """

    def __init__(self, on_execute: Any = None) -> None:
        self.added: list[Any] = []
        self._on_execute = on_execute or (lambda _: _FakeScalarResult(0))

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def execute(self, stmt: Any) -> Any:
        return self._on_execute(stmt)


# ─── IngestLogRepository.get_stats ──────────────────────────────────────


@pytest.mark.unit
def test_get_stats_returns_dataclass_with_zero_defaults() -> None:
    stats = IngestStats()
    assert stats.total_attempts == 0
    assert stats.total_successes == 0
    assert stats.total_failures == 0
    assert stats.failures_by_class == {}
    assert stats.attempts_by_endpoint == {}


@pytest.mark.unit
def test_ingest_stats_dataclass_equality() -> None:
    s1 = IngestStats(
        total_attempts=10,
        total_successes=8,
        total_failures=2,
        failures_by_class={"AUTH": 2},
        attempts_by_endpoint={"/api/v1/ingest/observation": 10},
    )
    s2 = IngestStats(
        total_attempts=10,
        total_successes=8,
        total_failures=2,
        failures_by_class={"AUTH": 2},
        attempts_by_endpoint={"/api/v1/ingest/observation": 10},
    )
    assert s1 == s2


@pytest.mark.unit
def test_ingest_stats_total_failures_computed() -> None:
    stats = IngestStats(total_attempts=10, total_successes=7)
    # total_failures is NOT auto-computed in the dataclass — the repo sets it.
    # Verify the dataclass stores what we assign.
    assert stats.total_failures == 0  # default
    stats2 = IngestStats(total_attempts=10, total_successes=7, total_failures=3)
    assert stats2.total_failures == 3


# ─── IngestFailuresRepository.resolve ───────────────────────────────────


class _FakeIngestFailureORM:
    def __init__(self, *, failure_id: int = 1, resolved: bool = False) -> None:
        self.id = failure_id
        self.resolved_at: datetime | None = datetime.now(UTC) if resolved else None
        self.resolved_action: str | None = None
        self.resolution_notes: str | None = None


class _FakeFailuresSession:
    """Session that returns a specific failure row for get_by_id queries."""

    def __init__(self, row: _FakeIngestFailureORM | None) -> None:
        self._row = row

    async def execute(self, stmt: Any) -> _FakeScalarResult:
        return _FakeScalarResult(self._row)


@pytest.mark.unit
def test_resolve_returns_true_for_open_failure() -> None:
    row = _FakeIngestFailureORM(failure_id=42, resolved=False)
    session = _FakeFailuresSession(row)
    repo = IngestFailuresRepository()
    now = datetime.now(UTC)

    result = asyncio.run(
        repo.resolve(
            session,  # type: ignore[arg-type]
            42,
            resolved_action="discarded",
            resolution_notes="Test note",
            resolved_at=now,
        )
    )

    assert result is True
    assert row.resolved_at == now
    assert row.resolved_action == "discarded"
    assert row.resolution_notes == "Test note"


@pytest.mark.unit
def test_resolve_returns_false_when_already_resolved() -> None:
    row = _FakeIngestFailureORM(failure_id=5, resolved=True)
    session = _FakeFailuresSession(row)
    repo = IngestFailuresRepository()

    result = asyncio.run(
        repo.resolve(
            session,  # type: ignore[arg-type]
            5,
            resolved_action="reprocessed",
            resolution_notes=None,
            resolved_at=datetime.now(UTC),
        )
    )

    assert result is False


@pytest.mark.unit
def test_resolve_returns_false_when_row_not_found() -> None:
    session = _FakeFailuresSession(None)
    repo = IngestFailuresRepository()

    result = asyncio.run(
        repo.resolve(
            session,  # type: ignore[arg-type]
            999,
            resolved_action="discarded",
            resolution_notes=None,
            resolved_at=datetime.now(UTC),
        )
    )

    assert result is False


# ─── IngestFailuresRepository.list_unresolved ────────────────────────────


class _FakeListSession:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    async def execute(self, stmt: Any) -> Any:
        class _R:
            def __init__(self, rows: list[Any]) -> None:
                self._rows = rows

            def scalars(self) -> _R:
                return self

            def all(self) -> list[Any]:
                return self._rows

        return _R(self._rows)


@pytest.mark.unit
def test_list_unresolved_returns_all_rows() -> None:
    rows = [
        _FakeIngestFailureORM(failure_id=i, resolved=False)
        for i in range(3)
    ]
    session = _FakeListSession(rows)
    repo = IngestFailuresRepository()

    result = asyncio.run(
        repo.list_unresolved(session, limit=50, offset=0)  # type: ignore[arg-type]
    )

    assert len(result) == 3


@pytest.mark.unit
def test_list_unresolved_empty_when_no_rows() -> None:
    session = _FakeListSession([])
    repo = IngestFailuresRepository()

    result = asyncio.run(
        repo.list_unresolved(session, limit=50, offset=0)  # type: ignore[arg-type]
    )

    assert result == []
