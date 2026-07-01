"""Unit tests for ``yvy_server.config``.

These tests are pure (no I/O). They don't touch the filesystem ``.env``
because ``_env_file=None`` is passed to disable that auto-load.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from yvy_server.config import Settings, get_settings

# A "strong" token used in tests where the production check must pass.
# 48 chars, no "CHANGE_ME" substring.
STRONG_TOKEN = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUV"
assert len(STRONG_TOKEN) >= 32


# ─── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_yvy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip all YVY_* env vars so each test starts from defaults."""
    for key in list(os.environ):
        if key.startswith("YVY_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _clear_get_settings_cache() -> Any:
    """Reset the ``get_settings`` LRU cache around every test."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _build(**overrides: Any) -> Settings:
    """Construct Settings ignoring any ``.env`` file on disk."""
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


# ─── Defaults ────────────────────────────────────────────────────────────


def test_defaults_are_local() -> None:
    s = _build()
    assert s.env == "local"
    assert s.log_level == "INFO"
    assert s.supported_schemas == frozenset({"1.0"})
    assert s.clock_skew_max_seconds == 1800
    assert s.clock_skew_warn_seconds == 300
    assert s.db_pool_size == 10
    assert s.db_max_overflow == 5
    assert s.host == "0.0.0.0"
    assert s.port == 8000


def test_db_dsn_default_is_async() -> None:
    s = _build()
    assert s.db_dsn.startswith("postgresql+asyncpg://")


# ─── Supported schemas (CSV → frozenset) ────────────────────────────────


def test_supported_schemas_single_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YVY_SUPPORTED_SCHEMAS", "1.0")
    s = _build()
    assert s.supported_schemas == frozenset({"1.0"})


def test_supported_schemas_csv_multiple(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YVY_SUPPORTED_SCHEMAS", "1.0,1.1,2.0")
    s = _build()
    assert s.supported_schemas == frozenset({"1.0", "1.1", "2.0"})


def test_supported_schemas_csv_strips_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YVY_SUPPORTED_SCHEMAS", " 1.0 , 1.1 , 2.0 ")
    s = _build()
    assert s.supported_schemas == frozenset({"1.0", "1.1", "2.0"})


def test_supported_schemas_csv_ignores_empty_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YVY_SUPPORTED_SCHEMAS", "1.0,,1.1,")
    s = _build()
    assert s.supported_schemas == frozenset({"1.0", "1.1"})


def test_supported_schemas_empty_string_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YVY_SUPPORTED_SCHEMAS", "")
    with pytest.raises(ValidationError, match="empty"):
        _build()


def test_supported_schemas_only_whitespace_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YVY_SUPPORTED_SCHEMAS", "  , , ,  ")
    with pytest.raises(ValidationError, match="empty"):
        _build()


# ─── DB DSN validation ───────────────────────────────────────────────────


def test_db_dsn_sync_scheme_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YVY_DB_DSN", "postgresql://yvy:yvy@localhost:5432/yvy")
    with pytest.raises(ValidationError, match="asyncpg"):
        _build()


def test_db_dsn_mysql_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YVY_DB_DSN", "mysql://yvy:yvy@localhost:3306/yvy")
    with pytest.raises(ValidationError, match="asyncpg"):
        _build()


def test_db_dsn_no_scheme_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YVY_DB_DSN", "just_a_bare_string_no_scheme")
    with pytest.raises(ValidationError, match="asyncpg"):
        _build()


def test_db_dsn_async_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YVY_DB_DSN", "postgresql+asyncpg://user:pass@host:5432/dbname")
    s = _build()
    assert s.db_dsn == "postgresql+asyncpg://user:pass@host:5432/dbname"


# ─── Clock skew relation ────────────────────────────────────────────────


def test_clock_skew_warn_must_be_strictly_less_than_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YVY_CLOCK_SKEW_MAX_SECONDS", "1800")
    monkeypatch.setenv("YVY_CLOCK_SKEW_WARN_SECONDS", "1800")
    with pytest.raises(ValidationError, match="strictly less"):
        _build()


def test_clock_skew_warn_greater_than_max_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YVY_CLOCK_SKEW_MAX_SECONDS", "100")
    monkeypatch.setenv("YVY_CLOCK_SKEW_WARN_SECONDS", "500")
    with pytest.raises(ValidationError, match="strictly less"):
        _build()


def test_clock_skew_warn_lt_max_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YVY_CLOCK_SKEW_MAX_SECONDS", "1800")
    monkeypatch.setenv("YVY_CLOCK_SKEW_WARN_SECONDS", "300")
    s = _build()
    assert s.clock_skew_max_seconds == 1800
    assert s.clock_skew_warn_seconds == 300


def test_clock_skew_zero_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YVY_CLOCK_SKEW_MAX_SECONDS", "0")
    with pytest.raises(ValidationError):
        _build()


# ─── Admin token: SecretStr + production weakness check ────────────────


def test_admin_token_is_secretstr() -> None:
    s = _build(admin_token=STRONG_TOKEN)
    assert isinstance(s.admin_token, SecretStr)
    assert s.admin_token.get_secret_value() == STRONG_TOKEN


def test_admin_token_does_not_leak_in_repr() -> None:
    s = _build(admin_token=STRONG_TOKEN)
    assert STRONG_TOKEN not in repr(s)
    assert STRONG_TOKEN not in str(s)
    # SecretStr repr should look like "SecretStr('**********')"
    assert "**" in repr(s.admin_token)


def test_admin_token_weak_allowed_in_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YVY_ENV", "local")
    monkeypatch.setenv("YVY_ADMIN_TOKEN", "short")
    s = _build()
    assert s.admin_token.get_secret_value() == "short"


def test_admin_token_short_rejected_in_staging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YVY_ENV", "staging")
    monkeypatch.setenv("YVY_ADMIN_TOKEN", "short")
    with pytest.raises(ValidationError, match="weak"):
        _build()


def test_admin_token_short_rejected_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YVY_ENV", "production")
    monkeypatch.setenv("YVY_ADMIN_TOKEN", "short")
    with pytest.raises(ValidationError, match="weak"):
        _build()


def test_admin_token_change_me_rejected_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YVY_ENV", "production")
    # Long enough to pass length check but still contains CHANGE_ME
    monkeypatch.setenv(
        "YVY_ADMIN_TOKEN",
        "CHANGE_ME_padding_to_make_this_string_longer_than_32_chars",
    )
    with pytest.raises(ValidationError, match="CHANGE_ME"):
        _build()


def test_admin_token_strong_accepted_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YVY_ENV", "production")
    monkeypatch.setenv("YVY_ADMIN_TOKEN", STRONG_TOKEN)
    s = _build()
    assert s.env == "production"
    assert s.admin_token.get_secret_value() == STRONG_TOKEN


# ─── Port range ──────────────────────────────────────────────────────────


def test_port_too_low_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YVY_PORT", "0")
    with pytest.raises(ValidationError):
        _build()


def test_port_too_high_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YVY_PORT", "70000")
    with pytest.raises(ValidationError):
        _build()


# ─── extra="ignore" — POSTGRES_* vars shouldn't break ──────────────────


def test_unknown_yvy_env_var_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """POSTGRES_* and other non-YVY_ vars must not raise.

    docker-compose injects POSTGRES_DB, POSTGRES_USER, etc. into the
    container's environment for the postgres service. The app container
    happens to inherit them too. We must ignore those silently.
    """
    monkeypatch.setenv("POSTGRES_DB", "yvy")
    monkeypatch.setenv("POSTGRES_USER", "yvy")
    monkeypatch.setenv("POSTGRES_PASSWORD", "yvy")
    # Should not raise
    s = _build()
    assert s.env == "local"


# ─── get_settings() caching ──────────────────────────────────────────────


def test_get_settings_returns_same_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YVY_ADMIN_TOKEN", STRONG_TOKEN)
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


def test_get_settings_cache_clear_refetches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YVY_ADMIN_TOKEN", STRONG_TOKEN)
    monkeypatch.setenv("YVY_PORT", "8000")
    s1 = get_settings()
    assert s1.port == 8000

    get_settings.cache_clear()
    monkeypatch.setenv("YVY_PORT", "9000")
    s2 = get_settings()
    assert s2.port == 9000
    assert s1 is not s2
