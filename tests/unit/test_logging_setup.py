"""Unit tests for ``yvy_server.logging_setup``.

These tests capture log output into an in-memory ``StringIO`` instead
of stdout so assertions can parse and inspect each emitted line.
"""

from __future__ import annotations

import io
import json
import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest

from yvy_server.logging_setup import (
    StructuredJsonFormatter,
    _json_default,
    setup_logging,
)

# ─── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def captured_logs() -> Iterator[io.StringIO]:
    """Install a StringIO-backed handler with our formatter on root.

    Yields the buffer so tests can read it. Cleans up handlers after
    each test to avoid leaking into other tests' captures.
    """
    root = logging.getLogger()
    # Snapshot prior state
    prior_handlers = list(root.handlers)
    prior_level = root.level

    # Install isolated capture handler
    for h in prior_handlers:
        root.removeHandler(h)
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(StructuredJsonFormatter())
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)

    yield buf

    # Restore
    root.removeHandler(handler)
    for h in prior_handlers:
        root.addHandler(h)
    root.setLevel(prior_level)


def _read_one_record(buf: io.StringIO) -> dict[str, object]:
    """Read the single JSON line in the buffer and parse it."""
    lines = [line for line in buf.getvalue().splitlines() if line]
    assert len(lines) == 1, f"Expected 1 log line, got {len(lines)}: {lines}"
    return json.loads(lines[0])  # type: ignore[no-any-return]


def _read_all_records(buf: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line]


# ─── Basic shape ─────────────────────────────────────────────────────────


def test_basic_log_emits_valid_json(captured_logs: io.StringIO) -> None:
    logging.getLogger("yvy.test.basic").info("hello")
    record = _read_one_record(captured_logs)

    assert record["level"] == "INFO"
    assert record["logger"] == "yvy.test.basic"
    assert record["msg"] == "hello"
    assert "ts" in record


def test_timestamp_has_z_suffix_and_ms_precision(captured_logs: io.StringIO) -> None:
    logging.getLogger("yvy.test.ts").info("x")
    record = _read_one_record(captured_logs)

    ts = record["ts"]
    assert isinstance(ts, str)
    assert ts.endswith("Z")
    # Format: "2026-05-16T12:34:56.789Z" — at least 24 chars
    assert len(ts) >= 24
    # Should parse back as ISO-aware datetime
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_levels_pass_through(captured_logs: io.StringIO) -> None:
    log = logging.getLogger("yvy.test.levels")
    log.debug("d")
    log.info("i")
    log.warning("w")
    log.error("e")

    records = _read_all_records(captured_logs)
    assert [r["level"] for r in records] == ["DEBUG", "INFO", "WARNING", "ERROR"]


def test_message_formatting_with_args(captured_logs: io.StringIO) -> None:
    """``logger.info("hello %s", "world")`` should resolve the args."""
    logging.getLogger("yvy.test.args").info("hello %s, code=%d", "world", 42)
    record = _read_one_record(captured_logs)
    assert record["msg"] == "hello world, code=42"


# ─── Extra context ───────────────────────────────────────────────────────


def test_extra_fields_appear_in_payload(captured_logs: io.StringIO) -> None:
    logging.getLogger("yvy.test.extra").info(
        "envelope received",
        extra={
            "station_uuid": "550e8400-e29b-41d4-a716-446655440000",
            "event_id": "abcdef01-2345-6789-abcd-ef0123456789",
            "entity_type": "EMPIRICAL_OBSERVATION",
        },
    )
    record = _read_one_record(captured_logs)

    assert record["station_uuid"] == "550e8400-e29b-41d4-a716-446655440000"
    assert record["event_id"] == "abcdef01-2345-6789-abcd-ef0123456789"
    assert record["entity_type"] == "EMPIRICAL_OBSERVATION"


def test_no_extra_means_no_extra_keys(captured_logs: io.StringIO) -> None:
    logging.getLogger("yvy.test.noextra").info("plain")
    record = _read_one_record(captured_logs)

    expected_keys = {"ts", "level", "logger", "msg"}
    assert set(record.keys()) == expected_keys


def test_standard_log_fields_not_leaked(captured_logs: io.StringIO) -> None:
    """``record.filename``, ``lineno``, etc. must not appear in output."""
    logging.getLogger("yvy.test.leak").info("x")
    record = _read_one_record(captured_logs)

    for forbidden in ("filename", "lineno", "funcName", "process", "thread", "args"):
        assert forbidden not in record, f"{forbidden} leaked into output"


# ─── Exceptions ──────────────────────────────────────────────────────────


def test_exception_is_serialized(captured_logs: io.StringIO) -> None:
    log = logging.getLogger("yvy.test.exc")
    try:
        raise ValueError("bad thing")
    except ValueError:
        log.exception("caught it")

    record = _read_one_record(captured_logs)
    assert record["msg"] == "caught it"
    assert record["exc_type"] == "ValueError"
    assert record["exc_msg"] == "bad thing"
    assert isinstance(record["traceback"], str)
    assert "ValueError" in record["traceback"]


def test_no_exception_means_no_exc_keys(captured_logs: io.StringIO) -> None:
    logging.getLogger("yvy.test.noexc").info("clean")
    record = _read_one_record(captured_logs)
    assert "exc_type" not in record
    assert "exc_msg" not in record
    assert "traceback" not in record


# ─── Encoder (UUID, datetime, bytes, set) ────────────────────────────────


def test_uuid_encoded_as_str(captured_logs: io.StringIO) -> None:
    uid = uuid.UUID("550e8400-e29b-41d4-a716-446655440000")
    logging.getLogger("yvy.test.uuid").info("u", extra={"id": uid})
    record = _read_one_record(captured_logs)
    assert record["id"] == "550e8400-e29b-41d4-a716-446655440000"


def test_datetime_encoded_as_iso(captured_logs: io.StringIO) -> None:
    dt = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    logging.getLogger("yvy.test.dt").info("d", extra={"observed_at": dt})
    record = _read_one_record(captured_logs)
    assert str(record["observed_at"]).startswith("2026-05-16T12:00:00")


def test_date_encoded_as_iso(captured_logs: io.StringIO) -> None:
    d = date(2026, 5, 16)
    logging.getLogger("yvy.test.date").info("d", extra={"day": d})
    record = _read_one_record(captured_logs)
    assert record["day"] == "2026-05-16"


def test_bytes_encoded_as_str(captured_logs: io.StringIO) -> None:
    logging.getLogger("yvy.test.bytes").info("b", extra={"raw": b"hello"})
    record = _read_one_record(captured_logs)
    assert record["raw"] == "hello"


def test_bytes_with_invalid_utf8_uses_replacement(captured_logs: io.StringIO) -> None:
    # \xff is not valid UTF-8 on its own
    logging.getLogger("yvy.test.badbytes").info("b", extra={"raw": b"\xff\xff"})
    record = _read_one_record(captured_logs)
    # Should not raise, should produce something
    assert isinstance(record["raw"], str)


def test_set_encoded_as_sorted_list(captured_logs: io.StringIO) -> None:
    logging.getLogger("yvy.test.set").info("s", extra={"tags": {"b", "a", "c"}})
    record = _read_one_record(captured_logs)
    assert record["tags"] == ["a", "b", "c"]


def test_frozenset_encoded_as_sorted_list(captured_logs: io.StringIO) -> None:
    logging.getLogger("yvy.test.fset").info("s", extra={"schemas": frozenset({"1.1", "1.0"})})
    record = _read_one_record(captured_logs)
    assert record["schemas"] == ["1.0", "1.1"]


def test_unknown_type_falls_back_to_str(captured_logs: io.StringIO) -> None:
    class Custom:
        def __str__(self) -> str:
            return "<Custom obj>"

    logging.getLogger("yvy.test.unknown").info("u", extra={"obj": Custom()})
    record = _read_one_record(captured_logs)
    assert record["obj"] == "<Custom obj>"


def test_json_default_direct_calls() -> None:
    """Direct test of _json_default for types that pass through it."""
    assert _json_default(uuid.UUID(int=0)) == "00000000-0000-0000-0000-000000000000"
    assert _json_default(date(2026, 5, 16)) == "2026-05-16"
    assert _json_default(b"abc") == "abc"
    assert _json_default({"b", "a"}) == ["a", "b"]
    assert _json_default(frozenset({"y", "x"})) == ["x", "y"]


# ─── setup_logging idempotency ──────────────────────────────────────────


def test_setup_logging_is_idempotent() -> None:
    """Multiple calls must not duplicate handlers on root."""
    root = logging.getLogger()
    prior_handlers = list(root.handlers)
    try:
        setup_logging(level="INFO")
        first_count = len(root.handlers)
        setup_logging(level="DEBUG")
        second_count = len(root.handlers)
        setup_logging(level="WARNING")
        third_count = len(root.handlers)
        assert first_count == second_count == third_count == 1
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in prior_handlers:
            root.addHandler(h)


def test_setup_logging_sets_level() -> None:
    root = logging.getLogger()
    prior_handlers = list(root.handlers)
    prior_level = root.level
    try:
        setup_logging(level="WARNING")
        assert root.level == logging.WARNING
        setup_logging(level="DEBUG")
        assert root.level == logging.DEBUG
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in prior_handlers:
            root.addHandler(h)
        root.setLevel(prior_level)


def test_noisy_libraries_tamed() -> None:
    """Verify that library loggers get sensible thresholds after setup."""
    root = logging.getLogger()
    prior_handlers = list(root.handlers)
    try:
        setup_logging(level="DEBUG")
        assert logging.getLogger("sqlalchemy.engine").level == logging.WARNING
        assert logging.getLogger("asyncpg").level == logging.WARNING
        assert logging.getLogger("uvicorn.access").level == logging.INFO
        assert logging.getLogger("uvicorn.error").level == logging.INFO
        assert logging.getLogger("alembic").level == logging.INFO
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in prior_handlers:
            root.addHandler(h)
