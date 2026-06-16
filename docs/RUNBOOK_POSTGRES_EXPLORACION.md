# YVY Core — Runbook: Exploración y Monitoreo PostgreSQL

## Acceso

```bash
cd /opt/yvy-server
sudo docker compose exec postgres psql -U yvyserver -d yvy
```

Read-only (para consultas sin riesgo):
```bash
sudo docker compose exec postgres psql -U yvyreader -d yvy
```

---

## Navegación dentro de psql

```sql
\l              -- listar bases de datos
\dn             -- listar schemas (public, bronze)
\dt public.*    -- tablas del schema public
\dt bronze.*    -- tablas del schema bronze
\d public.station               -- estructura de una tabla
\d bronze.sensor_raw
\d bronze.empirical_observation_raw
\d bronze.ingest_failures
\d bronze.ingest_log
\x              -- activar/desactivar modo expandido (más legible)
\q              -- salir
```

---

## Queries operativas

### Últimas 10 entradas — sensor_raw

```sql
SELECT
    event_id,
    received_at,
    payload->>'observed_at' AS observado,
    payload->>'temp_outdoor' AS temp_ext,
    payload->>'hum_outdoor' AS hum_ext,
    payload->>'pressure_abs' AS presion,
    payload->>'wind_speed'   AS viento,
    payload->>'rain_total'   AS lluvia
FROM bronze.sensor_raw
ORDER BY received_at DESC
LIMIT 10;
```

### Últimas 10 entradas — empirical_observation_raw

```sql
SELECT
    event_id,
    station_uuid,
    received_at,
    payload
FROM bronze.empirical_observation_raw
ORDER BY received_at DESC
LIMIT 10;
```

### DLQ — fallos sin resolver

```sql
SELECT
    id,
    station_uuid,
    endpoint,
    failure_class,
    failure_reason,
    received_at
FROM bronze.ingest_failures
WHERE resolved_at IS NULL
ORDER BY received_at DESC
LIMIT 10;
```

### Estado de salud por EDGE (última actividad)

```sql
SELECT
    s.name,
    s.station_uuid,
    MAX(l.received_at)                                              AS ultimo_contacto,
    COUNT(l.id)                                                     AS total_requests,
    SUM(CASE WHEN l.failure_class IS NOT NULL THEN 1 ELSE 0 END)   AS fallos
FROM public.station s
LEFT JOIN bronze.ingest_log l ON s.station_uuid = l.station_uuid
GROUP BY s.station_uuid, s.name
ORDER BY ultimo_contacto DESC;
```

### Conteo global de todas las tablas

```sql
SELECT 'station'                   AS tabla, COUNT(*) FROM public.station
UNION ALL SELECT 'station_credentials',       COUNT(*) FROM public.station_credentials
UNION ALL SELECT 'sensor_raw',                COUNT(*) FROM bronze.sensor_raw
UNION ALL SELECT 'empirical_observation_raw', COUNT(*) FROM bronze.empirical_observation_raw
UNION ALL SELECT 'empirical_event_raw',       COUNT(*) FROM bronze.empirical_event_raw
UNION ALL SELECT 'ingest_log',                COUNT(*) FROM bronze.ingest_log
UNION ALL SELECT 'ingest_failures',           COUNT(*) FROM bronze.ingest_failures;
```

### Tamaño de la base

```sql
SELECT pg_size_pretty(pg_database_size('yvy'));
```

### Conexiones activas

```sql
SELECT count(*) FROM pg_stat_activity;
```

---

## Salud del servidor

```bash
# Estado de containers
sudo docker compose ps

# Logs en vivo
sudo docker compose logs -f yvy-api
sudo docker compose logs -f nginx

# Últimos 50 logs
sudo docker compose logs --tail=50 yvy-api

# Recursos del sistema
htop
df -h
free -h
```

---

## Roadmap pendiente

- [ ] Endpoints de salida API (`api.yvyedge.com`) para consumo externo
- [ ] Pipeline bronze → silver para uso en notebooks
- [ ] Visualización web de datos meteorológicos
