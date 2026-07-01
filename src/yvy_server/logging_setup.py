"""Structured JSON logging setup for YVY server.

Configures the root logger to emit one JSON object per line. Each record
includes the standard fields plus any extra context attached via the
``extra=`` keyword of ``logger.log(...)``.

Example::

    log = logging.getLogger("yvy.ingest")
    log.info(
        "envelope received",
        extra={
            "station_uuid": "550e8400-e29b-41d4-a716-446655440000",
            "event_id": "abcdef01-2345-6789-abcd-ef0123456789",
            "entity_type": "EMPIRICAL_OBSERVATION",
        },
    )

Output (single line, indented here for readability)::

    {
      "ts": "2026-05-16T12:34:56.789Z",
      "level": "INFO",
      "logger": "yvy.ingest",
      "msg": "envelope received",
      "station_uuid": "550e8400-e29b-41d4-a716-446655440000",
      "event_id": "abcdef01-2345-6789-abcd-ef0123456789",
      "entity_type": "EMPIRICAL_OBSERVATION"
    }

Design notes
------------

- Zero external dependencies. Stdlib ``logging`` + ``json.dumps``.
- The formatter is idempotent: calling ``setup_logging()`` multiple
  times leaves a single handler on root (prior handlers are cleared),
  which avoids duplicate output when uvicorn or pytest also touch the
  root logger.
- ``UUID``, ``datetime``, ``date`` and ``bytes`` are JSON-coerced to
  strings via the ``default=`` encoder hook. Unknown types fall back
  to ``str()``.
- Library loggers (sqlalchemy, asyncpg, uvicorn, alembic) propagate
  to root, so they inherit the formatter automatically; only their
  level threshold is adjusted to reduce noise.

See ``YVY_FASE9_Arquitectura_v2.md`` §11.6.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import sys
import uuid
from typing import Any, TextIO

# Fields that ``logging.LogRecord`` defines by default in Python 3.12.
# Anything found on a record NOT in this set is treated as extra/user
# context and included in the JSON output verbatim.
_STANDARD_LOG_FIELDS: frozenset[str] = frozenset(
    {
        "args",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",  # set by Formatter.format()
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",  # py 3.12+
        "thread",
        "threadName",
    }
)


class StructuredJsonFormatter(logging.Formatter):
    """Format log records as single-line JSON objects.

    Always emits UTC timestamps with millisecond precision and ``Z``
    suffix. Non-JSON-serializable values are coerced via
    :func:`_json_default`.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Base payload: fixed fields first for log readability
        ts = _dt.datetime.fromtimestamp(record.created, tz=_dt.UTC)
        payload: dict[str, Any] = {
            "ts": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Exception info (from logger.exception() or exc_info=True)
        if record.exc_info:
            exc_type, exc_val, _exc_tb = record.exc_info
            payload["exc_type"] = exc_type.__name__ if exc_type else None
            payload["exc_msg"] = str(exc_val) if exc_val is not None else None
            payload["traceback"] = self.formatException(record.exc_info)

        # Stack info (from logger.log(..., stack_info=True))
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        # Append extra context fields, preserving insertion order
        for key, value in record.__dict__.items():
            if key in _STANDARD_LOG_FIELDS or key.startswith("_"):
                continue
            payload[key] = value

        return json.dumps(payload, default=_json_default, ensure_ascii=False)


def _json_default(obj: Any) -> Any:
    """JSON encoder fallback for types we want stringified.

    Order matters: ``isinstance(obj, datetime)`` returns True for both
    ``datetime`` and ``date`` (since datetime subclasses date), so we
    check datetime first.
    """
    if isinstance(obj, _dt.datetime):
        # Already-aware datetime: isoformat keeps tz; naive ones become
        # ambiguous strings, which is the caller's bug.
        return obj.isoformat()
    if isinstance(obj, _dt.date):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, set | frozenset):
        return sorted(obj, key=str)
    # Last resort: stringify
    return str(obj)


def setup_logging(level: str = "INFO", *, stream: TextIO | None = None) -> None:
    """Configure the root logger with the structured JSON formatter.

    Idempotent: clears existing handlers on the root logger before
    adding a single ``StreamHandler`` with our formatter. Library loggers
    (``sqlalchemy``, ``asyncpg``, ``uvicorn``, ``alembic``) propagate to
    root, so they inherit this configuration; only their level threshold
    is adjusted here to reduce noise.

    Args:
        level: Log level for the root logger, as a string
            (``"DEBUG"`` | ``"INFO"`` | ``"WARNING"`` | ``"ERROR"`` |
            ``"CRITICAL"``).
        stream: File-like target for the handler. Defaults to
            ``sys.stdout`` (12-factor for server processes — systemd
            journal captures stdout). The CLI passes ``sys.stderr``
            so structured logs don't mix with the command's JSON output
            on stdout.
    """
    if stream is None:
        stream = sys.stdout

    root = logging.getLogger()

    # Clear prior handlers to avoid duplicated lines if setup_logging
    # ran already, or if uvicorn/pytest attached its own handler.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredJsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)

    # Tame noisy libraries. They propagate to root, so this only sets
    # their level threshold; the formatter is inherited.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
    logging.getLogger("asyncpg").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("alembic").setLevel(logging.INFO)
