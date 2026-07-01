# Changelog

All notable changes to YVY_Core are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Fixed (2026-06-16 — CI/CD pre-checks `plan_fases_api`)

- **ruff** — 28 lint errors eliminated: import ordering (`I001`), unsorted `__all__`
  (`RUF022`), stale `# noqa: BLE001` (`RUF100`), missing end-of-file newlines (`W292`),
  `E402` in `alembic/env.py`, and regex match strings missing `r` prefix (`RUF043` ×4).

- **mypy** — 34 type errors resolved across 6 files:
  - `src/yvy_server/__init__.py` — corrected `type: ignore` code from `[import-untyped]`
    to `[import-not-found]` (module `_version` is absent, not untyped).
  - `tests/unit/test_logging_setup.py` — added `# type: ignore[no-any-return]` on
    `json.loads()` return; cast `record["observed_at"]` to `str` before `.startswith()`.
  - `tests/unit/test_config.py` — added `# type: ignore[call-arg]` on `Settings(_env_file=…)`.
  - `tests/unit/test_admin_repos.py` — replaced bare `list` return/param annotations with
    `list[Any]` (×3).
  - `tests/unit/test_envelope_validator.py` — typed sentinel `_SESSION: Any = object()` so
    it can be passed to async repo methods without 9 repeated `[arg-type]` errors.
  - `tests/unit/test_ingest_sensor_event_use_cases.py` — replaced bare `dict` / `list[dict]`
    with `dict[str, Any]` / `list[dict[str, Any]]` (×5); removed stale `# type: ignore[misc]`
    on `@asynccontextmanager` functions; added `# type: ignore[arg-type]` to fake-object
    constructor args (validator, publisher, log_repo, failures_repo, session_factory ×2
    use-case builders).

  All 65 source files pass `mypy .` with no errors.

- **pytest** — 142 unit tests pass (`pytest -x -v`). No DB required (pure unit tests).

### Fixed (2026-06-12)

- **`GET /api/v1/edge/updates/{filename}` devolvía HTTP 500 por colisión de key de log** —
  `edge_download` pasaba `"filename": filename` dentro de `extra={}` en sus logs
  estructurados. `filename` es un atributo reservado del `LogRecord` de Python → al
  emitir el log se lanzaba `KeyError`, convirtiéndose en un 500 que rompía la descarga
  de paquetes OTA. Renombrada la key a `"package_filename"` (el valor no cambia) en los
  dos puntos afectados: el log de éxito (`update.downloaded`) y el de archivo faltante
  (`update.download.failed`). Barrido del resto de `src/` confirmó que no hay otras keys
  reservadas en `extra={}`.

  **Files changed:**
  - `src/yvy_server/presentation/api/routers/edge.py` — `edge_download`: `filename` →
    `package_filename` en `_log.info("update.downloaded")` y `_log.error("update.download.failed")`

- **CLOCK error response now includes `skew_seconds`** — `IngestErrorResponse` gains an
  optional `skew_seconds: int | None` field. For CLOCK errors (422) the server now returns
  the measured clock difference (in seconds) in the response body. This aligns the contract
  with the Edge agent which calls `result.body_json.get("skew_seconds")` for diagnostic
  logging. Previously the field was computed and persisted to `bronze.ingest_log` but never
  sent back to the caller, so Edge logs always showed `skew_seconds: null`.

  **Files changed:**
  - `src/yvy_server/presentation/api/schemas.py` — `IngestErrorResponse` + `skew_seconds`
  - `src/yvy_server/application/use_cases/ingest_observation.py` — `IngestFailure` +
    `skew_seconds`; propagated from `EnvelopeError.skew_seconds` at the catch site
  - `src/yvy_server/presentation/api/routers/ingest.py` — `_run_ingest` passes
    `outcome.skew_seconds` to `_error_response()`; `_error_response()` accepts and forwards
    the field

  **Compatibility:** additive change — `skew_seconds` is `None` for all non-CLOCK errors,
  so existing Edge clients that do not read the field are unaffected. Edge v1.x already
  uses `.get("skew_seconds")` (safe default `None`), so no Edge changes required.

### Notes on Edge compatibility audit (Fase 8.5a empirical observation — 2026-06-12)

Full compatibility check between `YVY_Core/src/yvy_server/presentation/api/schemas.py`
and the Edge sync/ingest layer. Summary of findings:

| Contract | Status | Notes |
|---|---|---|
| `SensorReadingPayload` field types | ✅ OK | Edge serializes all Decimals via `dec_to_str()`; `solar_radiation` is string, not int |
| `EmpiricalObservationPayload` required fields | ✅ OK | `observed_at_utc` present; extra fields tolerated via `extra="allow"` |
| `EmpiricalEventPayload` required fields | ✅ OK | All required fields present; extra fields tolerated |
| `EdgeSyncResponse` — `updates` dict | ✅ OK | `updates: dict[str, Any]` matches Edge key reads |
| `IngestSuccessResponse` — `ingest_result` | ✅ OK | |
| `IngestErrorResponse` — `skew_seconds` missing | ✅ Fixed (this release) | Edge uses `.get()`, only for logging |
| `observer_id` vs `created_by_user_id` | ⏳ Pendiente Fase 10 | Documented in schema comment; `observer_id` stays `None` until Edge updates payload |
