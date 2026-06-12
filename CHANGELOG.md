# Changelog

All notable changes to YVY_Core are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Fixed

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
