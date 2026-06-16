# Endpoints YVY Core — estado fáctico al 12/06/2026

## Implementados y activos

### Público
| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/v1/healthz` | Liveness check |
| GET | `/api/v1/serverinfo` | Versión, git commit, schemas soportados |

### Ingest — Edge → Core
Autenticación: HMAC-SHA256 sobre body (envelope canónico). Todos devuelven `X-Server-Sync-Version` en el header de respuesta.

| Método | Ruta | Estado |
|---|---|---|
| POST | `/api/v1/ingest/sensor` | Activo |
| POST | `/api/v1/ingest/observation` | Activo |
| POST | `/api/v1/ingest/event` | Activo |
| POST | `/api/v1/ingest/comment` | **Stub — devuelve 501** |

Datos exitosos van a `bronze.sensor_raw`, `bronze.empirical_observation_raw`, `bronze.empirical_event_raw`.
Fallos post-HMAC van a `bronze.ingest_failures` (DLQ).

### Edge Pull — Core → Edge
Autenticación: HMAC-SHA256 sobre headers (`key_version`, `nonce`, `sent_at_utc`, `station_uuid`).

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/v1/edge/sync` | Update check: devuelve `sync_version` y metadata del release activo si hay versión mayor |
| GET | `/api/v1/edge/updates/{filename}` | Descarga binario `.tar.gz` del release. Requiere `package_filename` registrado en `edge_release` |

### Admin
Autenticación: Bearer token (`YVY_ADMIN_TOKEN`).

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/v1/admin/healthz` | DB responsive + row counts |
| GET | `/api/v1/admin/stats` | Estadísticas de ingest (totales, por clase de error, por endpoint) |
| GET | `/api/v1/admin/failures` | Lista DLQ (`bronze.ingest_failures` sin resolver) |
| POST | `/api/v1/admin/failures/{id}/resolve` | Marcar fallo como resuelto (`reprocessed` / `discarded` / `fixed_at_source`) |

---

## No implementado (pendiente)

No existen endpoints de salida para consumidores externos. Todo lo que hay es inbound (Edge → Core) o interno (admin).

Pendiente para `api.yvyedge.com`:
- Datos de sensor agregados por estación / período
- Estado de nodos (último dato recibido, versión instalada)
- Feed/pizarra para la web
