"""Application settings via ``pydantic-settings``.

Settings load from environment variables and, optionally, from a ``.env``
file in the working directory. All variables use the ``YVY_`` prefix.

Access pattern
--------------

For code outside a FastAPI request context (CLI, scripts, lifespan
startup) use the cached singleton::

    from yvy_server.config import get_settings
    settings = get_settings()

For FastAPI endpoints, prefer dependency injection so tests can override::

    from typing import Annotated
    from fastapi import Depends
    from yvy_server.config import Settings, get_settings

    @router.get("/serverinfo")
    async def serverinfo(
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> dict[str, Any]:
        ...

In tests, override with::

    app.dependency_overrides[get_settings] = lambda: test_settings

Validation behavior
-------------------

- ``YVY_DB_DSN`` must start with ``postgresql+asyncpg://`` (async stack).
  A sync DSN raises early so the server never silently uses the wrong driver.
- ``YVY_CLOCK_SKEW_WARN_SECONDS`` must be strictly less than
  ``YVY_CLOCK_SKEW_MAX_SECONDS``.
- ``YVY_ADMIN_TOKEN`` must be at least 32 chars and not contain
  ``CHANGE_ME`` when ``YVY_ENV`` is ``staging`` or ``production``.
  In ``local`` env the check is skipped (test convenience).
- ``YVY_SUPPORTED_SCHEMAS`` is a CSV string parsed into a ``frozenset``;
  must not be empty.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Minimum admin token length (matches secrets.token_urlsafe(24) length floor)
MIN_ADMIN_TOKEN_LENGTH = 32


class Settings(BaseSettings):
    """YVY Server configuration loaded from env vars / `.env`."""

    model_config = SettingsConfigDict(
        env_prefix="YVY_",
        env_file=".env",
        env_file_encoding="utf-8",
        # ``extra="ignore"`` so env vars not in this model don't crash.
        # ``POSTGRES_*`` vars (consumed by the docker-compose postgres
        # service) would otherwise raise ValidationError on load here.
        extra="ignore",
        case_sensitive=False,
    )

    # ─── App ─────────────────────────────────────────────────────────────
    env: Literal["local", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Stored as frozenset internally; env var is CSV ("1.0,1.1,...").
    #
    # ``Annotated[..., NoDecode]`` tells pydantic-settings NOT to try
    # parsing the env var as JSON (which it does by default for any
    # "complex" type like ``frozenset[str]``). Without this, the env
    # value ``"1.0,1.1"`` would fail because it's not valid JSON.
    # With NoDecode, the raw string reaches our ``mode="before"``
    # validator below for CSV parsing.
    supported_schemas: Annotated[frozenset[str], NoDecode] = frozenset({"1.0"})

    # ─── Clock skew (seconds) ────────────────────────────────────────────
    clock_skew_max_seconds: int = Field(default=1800, ge=1)
    clock_skew_warn_seconds: int = Field(default=300, ge=1)

    # ─── Database ────────────────────────────────────────────────────────
    db_dsn: str = "postgresql+asyncpg://yvy:yvy@localhost:5432/yvy"
    db_pool_size: int = Field(default=10, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)

    # ─── Admin auth (bearer token for /admin/*) ─────────────────────────
    # ``SecretStr`` prevents the token from leaking through repr() or
    # default logging. Use ``.get_secret_value()`` to read.
    admin_token: SecretStr = SecretStr("CHANGE_ME_set_real_token_in_env_file")

    # ─── HTTP ────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)

    # ─── Canal Core → Edge ───────────────────────────────────────────────
    # Directory where published .tar.gz packages are stored. Relative paths
    # resolve from the working directory at runtime.
    releases_dir: str = "data/releases"

    # ─── Parsers ─────────────────────────────────────────────────────────
    @field_validator("supported_schemas", mode="before")
    @classmethod
    def _parse_supported_schemas_csv(cls, v: Any) -> Any:
        """Parse CSV string from env var into a frozenset.

        Accepts: ``"1.0,1.1"`` -> ``frozenset({"1.0", "1.1"})``.
        Whitespace around each entry is stripped; empty entries are
        ignored. If the value is already a set/frozenset/list (e.g. from
        a programmatic ``Settings(supported_schemas={"1.0"})`` call),
        it's returned as-is for Pydantic to coerce normally.
        """
        if isinstance(v, str):
            return frozenset(s.strip() for s in v.split(",") if s.strip())
        return v

    # ─── Validators ──────────────────────────────────────────────────────
    @field_validator("db_dsn")
    @classmethod
    def _validate_db_dsn_is_async(cls, v: str) -> str:
        if not v.startswith("postgresql+asyncpg://"):
            scheme = v.split("://", 1)[0] if "://" in v else v
            raise ValueError(
                "YVY_DB_DSN must start with 'postgresql+asyncpg://' "
                "(server uses async SQLAlchemy with asyncpg driver). "
                f"Got scheme: {scheme!r}"
            )
        return v

    @model_validator(mode="after")
    def _validate_clock_skew_relation(self) -> Settings:
        if self.clock_skew_warn_seconds >= self.clock_skew_max_seconds:
            raise ValueError(
                f"YVY_CLOCK_SKEW_WARN_SECONDS ({self.clock_skew_warn_seconds}) "
                f"must be strictly less than YVY_CLOCK_SKEW_MAX_SECONDS "
                f"({self.clock_skew_max_seconds})"
            )
        return self

    @model_validator(mode="after")
    def _validate_admin_token_strong_in_non_local(self) -> Settings:
        if self.env == "local":
            return self  # local env accepts weak tokens for test convenience
        token = self.admin_token.get_secret_value()
        if len(token) < MIN_ADMIN_TOKEN_LENGTH or "CHANGE_ME" in token:
            raise ValueError(
                f"YVY_ADMIN_TOKEN is too weak for env={self.env!r} "
                f"(must be >= {MIN_ADMIN_TOKEN_LENGTH} chars and not "
                f"contain 'CHANGE_ME'). Generate one with: "
                "python -c 'import secrets; print(secrets.token_urlsafe(48))'"
            )
        return self

    @model_validator(mode="after")
    def _validate_supported_schemas_nonempty(self) -> Settings:
        if not self.supported_schemas:
            raise ValueError(
                "YVY_SUPPORTED_SCHEMAS cannot be empty. "
                "Must include at least '1.0'."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached ``Settings`` singleton.

    The ``@lru_cache`` means env vars are read only once at first access.
    To force a re-read in tests, call ``get_settings.cache_clear()``.
    """
    return Settings()
