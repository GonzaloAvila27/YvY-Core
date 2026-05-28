"""ASGI middleware: response headers global a todo el server.

Headers added to EVERY response (including error responses):

- ``X-Server-Time``: ISO 8601 UTC with millisecond precision and ``Z``
  suffix. The edge agent uses this to recalibrate its own clock when it
  hits a CLOCK error (see v2 §5.2).
- ``X-Schema-Versions-Supported``: CSV of schemas the server accepts.
  The edge agent reads this on startup / after errors to know what to
  send.
- ``X-Server-Version``: ``yvy_server.__version__`` for traceability.
  Useful in logs / debug, also stamped on each IngestEvent's
  ``producer_version``.

The middleware reads ``app.state.settings`` for the supported schemas.
This means lifespan must have run before the first request — which is
the FastAPI default behavior.
"""
from __future__ import annotations

import datetime as _dt
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from yvy_server import __version__


class ServerHeadersMiddleware(BaseHTTPMiddleware):
    """Add YVY-specific response headers to every response."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)

        # Read settings from app.state (lifespan has populated it).
        # Defensive: if state.settings isn't set for some reason, skip
        # the schema header rather than crash. The other two always work.
        try:
            settings = request.app.state.settings
            schema_versions = ",".join(sorted(settings.supported_schemas))
            response.headers["X-Schema-Versions-Supported"] = schema_versions
        except AttributeError:
            pass

        # Server time: always emitted
        response.headers["X-Server-Time"] = _utc_now_iso_ms()
        response.headers["X-Server-Version"] = __version__

        return response


def _utc_now_iso_ms() -> str:
    """ISO 8601 UTC with millisecond precision and ``Z`` suffix."""
    return (
        _dt.datetime.now(_dt.UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
