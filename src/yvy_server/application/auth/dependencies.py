"""FastAPI dependencies for handler injection.

The composition root in ``main.py`` builds long-lived objects once at
startup (lifespan) and stores them on ``app.state``. These dependency
functions read from ``app.state`` and expose the objects to handlers
via ``Depends(...)``.

Why ``app.state`` and not module-level globals?

- Lifespan-scoped construction (engine, session_factory, repos, use
  case) needs ``settings`` which is only known at startup.
- ``app.state`` is per-application: multiple FastAPI instances can
  coexist (rare, but TestClient sometimes does this).
- FastAPI's ``Depends`` makes overriding in tests trivial:
  ``app.dependency_overrides[get_observation_use_case] = lambda: mock``.
"""
from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from yvy_server.application.use_cases import IngestObservationUseCase
from yvy_server.config import Settings
from yvy_server.infrastructure.db import SessionFactory


# ─── State accessors ────────────────────────────────────────────────────


def get_settings_dep(request: Request) -> Settings:
    """Return the ``Settings`` instance loaded at app startup.

    Stored on ``app.state.settings`` by the lifespan.
    """
    return request.app.state.settings  # type: ignore[no-any-return]


def get_session_factory_dep(request: Request) -> SessionFactory:
    """Return the ``async_sessionmaker`` instance from app.state."""
    return request.app.state.session_factory  # type: ignore[no-any-return]


async def get_session_dep(
    factory: Annotated[SessionFactory, Depends(get_session_factory_dep)],
) -> AsyncIterator[AsyncSession]:
    """Yield a fresh ``AsyncSession`` scoped to one handler invocation.

    The session is closed automatically when the handler returns.
    Transaction management is the handler/repo's responsibility
    (via ``async with session.begin():`` or explicit commit/rollback).
    """
    async with factory() as session:
        yield session


def get_observation_use_case(request: Request) -> IngestObservationUseCase:
    """Return the ``IngestObservationUseCase`` instance from app.state."""
    return request.app.state.ingest_observation_use_case  # type: ignore[no-any-return]


# ─── Admin auth ─────────────────────────────────────────────────────────


async def verify_admin_token(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Bearer token verification for ``/api/v1/admin/*`` endpoints.

    Reads the ``Authorization`` header, expects ``Bearer <token>``, and
    compares the token (constant-time) to ``settings.admin_token``.

    Raises ``HTTPException(401)`` on any failure (missing header, wrong
    scheme, bad token). The response body matches the ingest error
    shape (``{"error", "error_class"}``) so the client logic is uniform.
    """
    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "Missing Authorization header",
                "error_class": "AUTH",
            },
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "Invalid auth scheme; expected 'Bearer <token>'",
                "error_class": "AUTH",
            },
        )

    expected = settings.admin_token.get_secret_value()
    if not secrets.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid admin token", "error_class": "AUTH"},
        )
