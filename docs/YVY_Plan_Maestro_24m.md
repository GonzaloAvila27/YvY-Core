# YVY · Plan Maestro 24 meses — Plataforma de Datos Ambientales

**Versión:** 1.0
**Fecha:** 15-Jun-2026
**Autor:** Gonzalo Avila (revisión arquitectónica asistida)
**Horizonte:** Jun-2026 → Jun-2028 (24 meses), con vista al año 3 (transición a streaming/orquestación)
**Estado:** PROPUESTA PARA APROBACIÓN — no es plan de implementación

---

## Cómo leer y usar este documento

Este es el **mapa general** de los próximos 24 meses. No contiene prompts de implementación
ni código final. Define:

- La arquitectura objetivo (modelo canónico ambiental, capas, tracks).
- Las **decisiones bloqueantes** que deben tomarse en 2026 antes de implementar.
- Las **fases generales** de cada track, con dependencias cruzadas.
- Qué se difiere y por qué no genera deuda técnica.

El flujo de trabajo posterior a la aprobación es: por cada fase general, abrir una sesión
de planificación específica ("vamos con Fase N, estos son los requisitos, este es el resumen
de la fase anterior, tomemos las decisiones finas y documentamos"), generar el prompt
específico para Claude Code, implementar, cerrar fase con documentación.

Este documento se acompaña de:
- `RUNBOOK_CICDv3.md` — migración del modelo canónico + backfill de los 10k registros actuales.
- `YVY_Plan_Fases_Web_Core_v3.md` — Web/Core actualizado al modelo canónico (contrato externo intacto).
- `YVY_DQS_Q1_RUNBOOK_v3.md` — DQS actualizado a leer Silver canónica.

---

## Principio rector

> **YVY no es una plataforma meteorológica. Es una plataforma ambiental, territorial y de
> conocimiento. La meteorología es la primera fuente de datos implementada, no la única.**

De ahí se deriva el invariante de diseño que gobierna todo este plan:

> **Agregar una nueva fuente de datos no debe requerir refactorizar el Core.**

La genericidad NO se logra con un modelo entidad-atributo-valor (EAV) plano. Se logra con un
**modelo canónico de identidad + un registro de metadata** (variables, unidades, fuentes,
sensores, indicadores) que describe el universo de datos, combinado con almacenamiento
**long-format genérico** en Silver/Gold y **proyecciones tipadas por dominio** que son
artefactos derivados, regenerables y descartables. La genericidad la paga la proyección,
nunca el modelo de verdad ni las queries del cliente.

---

## Las dos rutas de ingesta (decisión estructural)

Los datos entran al Core por exactamente dos caminos, con propiedades de confianza distintas
que deben quedar marcadas desde Bronze:

| Ruta | Quién entra | Autenticación | Confianza | `source_type` |
|---|---|---|---|---|
| **Edge** | Estaciones y sensores físicos vía YVY Edge (con o sin GUI) | HMAC-SHA256, envelope firmado, idempotencia por `event_id` | **Ground truth** — dato propio medido | `weather_station`, `soil_sensor`, etc. |
| **API externa** | Open-Meteo, satélite, topografía, NASA POWER, etc. Poleadas por el Core | Sin firma (el Core es el consumidor) | **Referencia** — dato de terceros | `external_api` |

**Regla legal/reputacional bloqueante:** un dato `external_api` NUNCA se sirve ni se vende
como medición propia de YVY. Se usa para validación cruzada (DQS), enriquecimiento y contexto.
La distinción vive en `source_type` desde el primer registro. Sin esto, en 12-24 meses no se
puede separar "lo que YVY midió" de "lo que YVY copió", y eso es un problema al vender datasets.

Los sensores físicos (incluidas las Raspberry sin GUI, captura+persistencia+envío oportunista)
entran **todos por el contrato Edge existente** (mismo `POST /ingest/sensor`, misma firma HMAC,
mismo `station_uuid` v5). Las edges sin GUI son una variante de empaquetado del Edge, **cero
cambio en el Core**.

---

# PARTE I — ARQUITECTURA OBJETIVO

## 1. Modelo canónico (el cimiento)

Inspirado en OGC Observations & Measurements / SOSA-SSN, adaptado a la escala de YVY. La idea:
una observación es un **sensor** (`procedure`) que produjo un **valor** (`result`) de una
**variable** (`observedProperty`) en un **momento** (`phenomenonTime`), asociado a una
**fuente** (`featureOfInterest`).

```
canonical.source_type   -- clase de productor de datos (extensible)
canonical.unit          -- catálogo de unidades canónicas (mapeo UCUM)
canonical.variable      -- propiedad observable, independiente del sensor
canonical.source        -- instancia concreta que produce datos (estación, API, docente)
canonical.sensor        -- componente medidor dentro de un source
canonical.indicator     -- definición de un indicador derivado (Gold)
```

Puntos clave del diseño:

- `canonical.variable` es un **catálogo en DB**, no columnas. Agregar "humedad de hoja" o
  "pH de suelo" es un `INSERT`, no una migración. Cada variable declara su dominio
  (`meteorology`, `soil`, `water`, `territorial`, `education`), unidad canónica, tipo de valor
  y rango físico.
- `public.station` actual **se mantiene** y se mapea a `canonical.source` con
  `source_type='weather_station'` y `source.external_ref = station.station_uuid`. Migración
  aditiva, no destructiva.
- Las fuentes externas reciben un `source_id` UUID v5 determinístico:
  `uuidv5(NAMESPACE_YVY, "open-meteo:posadas")`. Así Bronze siempre tiene `source_id` no-nulo,
  sea estación o API.

## 2. Las capas

```
                        ┌─────────────────────────────────────┐
                        │   CANONICAL MODEL (metadata)         │
                        │   source_type, source, sensor,       │
                        │   variable, unit, indicator          │  ← registro que
                        └─────────────────────────────────────┘     da genericidad
                                       │ describe
        ┌──────────────────────────────┼──────────────────────────────┐
        ↓                              ↓                               ↓
   ┌─────────┐                   ┌──────────┐                   ┌────────────┐
   │ BRONZE  │  promote          │  SILVER  │  derive           │   GOLD     │
   │raw_record──────────────────→│measurement──────────────────→│indicator   │
   │(genérico│                   │(long +   │                   │_value      │
   │ JSONB)  │                   │ proj.)   │                   │(long,      │
   └─────────┘                   └──────────┘                   │multi-dom)  │
        │                              │                         └────────────┘
        │                              │                               │
        └──────────┐                   │         ┌─────────────────────┘
                   ↓                   ↓         ↓
              ┌──────────┐        ┌─────────────────────────┐
              │KNOWLEDGE │        │   SERVING / API INST     │
              │document, │───────→│   (Track DATA)           │
              │chunk,    │        └─────────────────────────┘
              │embedding │                   ↑
              └──────────┘                   │ consume vía API
                   ↑                          │  (mismos permisos)
                   │         ┌────────────────┴──────┐
                   └─────────│   TRACK IA            │
                             │   ports → RAG → agents│
                             └───────────────────────┘
```

### 2.1 Bronze — Data Lake genérico

Tabla única append-only `bronze.raw_record`, referencia `source_id` (genérico, no
`station_uuid`), particionada por `received_at`. Recibe sin cambios estructurales: JSON
meteorológico, datos RS485, CSV institucionales, respuestas de APIs externas, observaciones
humanas, metadata de documentos, eventos geoespaciales. El `record_kind` y `source_type`
distinguen el contenido. Idempotencia por `record_id` (= `event_id` actual).

### 2.2 Silver — long format + proyecciones

`silver.measurement`: una fila por variable medida (long format). Genérico por diseño —
agregar una variable es un INSERT con nuevo `variable_key`, cero migración. Valor polimórfico
acotado (`value_num` / `value_text` / `value_bool`) según el `value_type` declarado en el
catálogo. Quality flags y score **por measurement**. Particionado por `observed_at`.

El costo del long format (pivots, volumen de filas) se paga con **proyecciones materializadas
por dominio** (`silver_proj.meteorology`, futuro `silver_proj.soil`, etc.) generadas
automáticamente desde el catálogo. Las queries rápidas y el DQS leen de las proyecciones. Son
artefactos derivados regenerables, no fuente de verdad.

Derivadas (VPD, dewpoint) se computan en la promoción y se almacenan como measurements más,
con flag `is_derived`.

### 2.3 Gold — indicadores multi-dominio

`gold.indicator_value`: long format, una fila por indicador computado. `canonical.indicator`
define cada indicador (VPD agregado, riesgo de helada, estrés hídrico, riesgo de incendio,
riesgo de deslizamiento, indicadores educativos), su dominio, su versión de método (citable),
sus inputs y su tipo de salida. Agregar "riesgo de inundación" = INSERT en catálogo + un job.
Cero refactor de schema.

Indicadores territoriales/regionales usan `feature_ref` (localidad, polígono, cuenca) en lugar
de `source_id`, confirmando que Gold no es meteorológico.

Criterio Silver vs Gold: derivada 1:1 de una observación (VPD, dewpoint) → Silver. Indicador
que integra ventana temporal, múltiples variables o contexto → Gold.

### 2.4 Knowledge — capa ortogonal (no secuencial)

Knowledge NO está en la cadena `Bronze→Silver→Gold`. Documentos y papers no fluyen por la
medallion; tienen su propio pipeline (ingesta → chunk → embedding) paralelo, que **referencia**
al modelo canónico pero no deriva de él.

```
knowledge.document   -- PDF, paper, manual, norma, nota docente, reporte
knowledge.chunk      -- fragmentos para retrieval
knowledge.embedding  -- pgvector
knowledge.entity     -- entidades semánticas (cultivo, patógeno, fenómeno, lugar, variable)
knowledge.chunk_entity -- relación chunk ↔ entidad
```

Embeddings con **pgvector** (extensión Postgres). Para la escala de YVY (miles de documentos)
pgvector sobra; no se requiere vector DB externa.

### 2.5 Serving — API institucional

Definida en el Track DATA. Sirve datos (Silver/Gold) y catálogo a consumidores externos con
auth por API key, permisos por dataset/estación/org, cuotas y auditoría. Detalle en Parte III.

## 3. Pipeline de IA agnóstico de modelo (hexagonal)

El Track IA respeta la arquitectura hexagonal existente. Los modelos (Ollama, OpenAI,
Anthropic, futuros) son **adapters** detrás de **puertos** (Protocols). Cambiar de proveedor =
nuevo adapter, cero cambio en domain/application.

```
domain/ia/ports.py        -- EmbeddingProvider, LLMProvider, Retriever (Protocols)
domain/ia/context.py      -- construcción de contexto (lógica pura)
domain/ia/citation.py     -- trazabilidad de fuentes (pura, obligatoria)
infrastructure/ia/...     -- adapters por proveedor
```

La IA institucional consume **las mismas APIs institucionales** que un cliente externo,
respetando permisos y cuotas. La IA no tiene acceso directo a la DB. Esto da trazabilidad,
control de acceso y obliga a que la API sea buena.

---

# PARTE II — DECISIONES BLOQUEANTES (tomar en 2026)

Estas decisiones condicionan el schema y deben cerrarse **antes** de implementar Silver, la
Web/Core v3 y el DQS v3. Se materializan en `RUNBOOK_CICDv3.md`.

| # | Decisión | Estado | Justificación |
|---|---|---|---|
| B1 | **Modelo canónico** (`canonical.variable/unit/source/sensor/source_type/indicator`) creado antes de Silver | APROBADO | Cimiento de toda la genericidad. Silver sin esto = refactor a 12m. |
| B2 | **Silver = `measurement` long + proyecciones por dominio**, no tabla tipada meteorológica | APROBADO | El refactor central. Reemplaza el diseño D1 tipado del borrador anterior. |
| B3 | **Gold = `indicator_value` long multi-dominio**, no `gold.weather_metrics` | APROBADO | Evita hardcodear meteorología en Gold. Solo diseño ahora. |
| B4 | **`source_id` como referencia canónica** (estación = caso de source); `public.station` mapeada, no eliminada | APROBADO | Aditivo. Permite fuentes no-estación sin tocar el resto. |
| B5 | **Bronze migra a `bronze.raw_record` único genérico** con `source_id`; backfill de las 3 tablas actuales + 10k filas; drop de las viejas | APROBADO | Momento barato (10k filas, segundos). DQS no implementado aún. |
| B6 | **`source_type` distingue Edge (ground truth) de external_api (referencia)** desde Bronze | APROBADO | Bloqueante legal/reputacional para venta de datasets. |
| B7 | **Instalar pgvector** en Postgres 16 | APROBADO | Habilita Knowledge/IA sin vector DB externa. `CREATE EXTENSION`. |
| B8 | **Instalar PostGIS** en Postgres 16, latente (sin tablas geoespaciales todavía) | APROBADO | Riesgo de deslizamiento en la provincia lo va a requerir pronto. Instalar una vez. |
| B9 | **Definir `domain/ia/ports.py`** (Protocols de Embedding/LLM/Retriever) | APROBADO | Protege el Core de la elección de modelo. ~1 día. |
| B10 | **Versionado persistido** en cada capa (`promotion_version`, `method_version`, `dataset_version`, `pipeline_version`) | APROBADO | Trazabilidad citable. Retroactivo es imposible. |
| B11 | **Cursor-based pagination** en API institucional (nunca offset) | APROBADO | Offset rompe con concurrencia; reescribir clientes a 6m es caro. |
| B12 | **Todo valor lleva `unit_key` explícita** | APROBADO | Sin unidad explícita no hay conversión ni interoperabilidad. |
| B13 | **`data.yvyedge.com` separado de `api.yvyedge.com`** para Track DATA institucional | APROBADO | Cache/TLS/rate-limit policies distintas; permite mover Track DATA a otro server sin migrar Core. |
| B14 | **IA consume la API institucional, no la DB directa** | APROBADO | Trazabilidad y control de acceso. |

### Decisiones NO bloqueantes (diferibles sin deuda)

| Diferible | Hasta cuándo | Por qué no genera deuda |
|---|---|---|
| Poblar fuentes no-meteo (suelo, agua) en Bronze/Silver | Cuando existan los sensores | Catálogo soporta INSERT |
| Knowledge Layer completo | Track IA | Schema referencia canonical desde el diseño |
| Pipeline RAG completo (chunking avanzado, reranking) | Track IA | Ports protegen el Core |
| Modelos predictivos (I8) | Apenas Silver cierre | Rama independiente |
| Tablas geoespaciales / capas GIS | Primera capa GIS real | PostGIS ya instalada (B8) |
| Object store para rasters/exports | >50GB acumulado | `storage_uri` ya abstrae |
| Multi-agente (I9) | Mucho después | Un agente bueno primero |
| Kafka / Airflow | Año 3, si el éxito lo exige | Volumen actual no lo justifica |

---

# PARTE III — LOS CUATRO TRACKS

El trabajo se organiza en cuatro tracks paralelos con dependencias cruzadas explícitas. Cada
track tiene numeración propia.

```
TRACK CORE PÚBLICO   W0-W6 + C10-C16   (en curso, se actualiza a v3)
TRACK DATA           M0-D9             (institucional, datasets, exports)
TRACK IA             I0-I9             (knowledge, RAG, asistentes)
TRACK FUENTES        F1-F8             (adapters de nuevas fuentes de datos)
```

## Track Core Público (actualizado a v3)

Continúa el plan Web/Core existente. **El único cambio del refactor canónico**: las views
públicas (C10: `v_station_summary`, `v_network_status`) pasan a leer del modelo canónico vía
proyección meteorología, no directo de `bronze.sensor_raw`. **El contrato de datos público
(DATA-CONTRACT) NO cambia** — el JSON que ve la Web es idéntico. La Web V1 no se entera del
refactor. Detalle en `YVY_Plan_Fases_Web_Core_v3.md`.

| Fase | Estado | Cambio v3 |
|---|---|---|
| W0-W1 | Pendiente | Sin cambios |
| W2-W6 | Pendiente | Sin cambios (consume API pública igual) |
| C10 | ✅ Cerrada | **Re-apuntar a proyección canónica** (parte de CICDv3) |
| C11 | ✅ Cerrada | Sin cambios de contrato |
| C12-C16 | Pendiente | Leen Silver canónica en vez de Bronze JSONB |

## Track DATA — fases generales

```
M0  Migración canónica + CICDv3        [BLOQUEANTE de todo Track DATA y refactor de Core/DQS]
D1  Silver Layer (measurement long + proyecciones)
D2  DQS por observación + extensión referencia externa (Open-Meteo)
D3  Gold Layer (indicator_value multi-dominio)
D4  API Institucional v1 (lectura)
D5  Sistema de Exports asíncrono (CSV/XLSX)
D6  Parquet writer + manifests reproducibles
D7  Catálogo DCAT
D8  API Keys + Permisos + Cuotas + Auditoría
D9  Outliers contextuales cross-station   [DIFERIDO hasta ≥6 IEAs activas]
```

### M0 — Migración canónica (RUNBOOK_CICDv3)

**Bloqueante de:** todo. Independiente del launch del Track Core (se puede hacer antes).

Crea `canonical.*`, `silver.*`, `gold.*`, `knowledge.*`, `auth.*`, `exports.*` (schemas), migra
Bronze a `raw_record`, backfillea los 10k registros, instala pgvector y PostGIS, seedea las 8
variables meteorológicas y las fuentes actuales. Actualiza Web/Core v3 y DQS v3 para que lean
del nuevo modelo. Detalle completo en `RUNBOOK_CICDv3.md`.

### D1 — Silver Layer

**Depende de:** M0. **Bloqueante de:** D2, D3, D4.

`silver.measurement` poblada. Job de promoción Bronze→Silver (systemd timer cada 5min, mismo
patrón que DQS). CLI `yvy-server promote-silver`. Proyección `silver_proj.meteorology`
materializada. Backfill de IEA-07. Tests de equivalencia Bronze-via-views = Silver.

**Quick win:** los endpoints `/public/dashboard` del Core pueden migrarse a leer Silver y ganar
10x en latencia, reverse-compatible.

### D2 — DQS por observación + referencia externa

**Depende de:** D1. **Bloqueante de:** D4.

`quality_flags`/`quality_score` por measurement en la promoción. Open-Meteo client como
`source_type='external_api'`. `reference_score`/`drift_score` poblados.
`station_quality_daily` generalizada a `source_quality_daily`. `dqs_internal` recalculado como
`AVG(quality_score)` desde Silver. Detalle de la actualización en `YVY_DQS_Q1_RUNBOOK_v3.md`.

### D3 — Gold Layer

**Depende de:** D1. **Bloqueante de:** indicadores avanzados, modelos predictivos (I8).

`canonical.indicator` + `gold.indicator_value`. Seed con VPD agregado y un indicador de riesgo
(helada) como prueba de concepto multi-dominio. Job de cómputo (systemd timer).

### D4 — API Institucional v1

**Depende de:** D1, D2, D8 (auth). **Bloqueante de:** D5.

DNS + cert `data.yvyedge.com`. Endpoints `/inst/sources/{id}/observations`, `/aggregations`,
`/quality`, `/inst/network/*`, `/inst/quality/explain`, `/inst/derived/explain`. Paginación por
cursor. Headers de rate limit. Respuesta en formato `columns`+`rows` (compacto). Filtros
estándar (`from`, `to`, `variables`, `sources`, `aggregation`, `quality_min`, `format`).

### D5 / D6 — Exports

**Depende de:** D1, D4, D8.

Cola en Postgres (`exports.job`), worker systemd, writers CSV/XLSX (D5) y Parquet (D6).
Generación bajo demanda asíncrona. Almacenamiento temporal en filesystem (`/opt/yvy-server/
exports/`), TTL 72h. `manifest.json` con `query_hash` para reproducibilidad. nginx
`X-Accel-Redirect` para servir con auth.

### D7 — Catálogo DCAT

**Depende de:** D1. **No depende de:** D4, D5 (puede listar datasets antes de que estén
accesibles).

DCAT v3 subset, compatible con datos.gob.ar. `catalog.dataset` con metadata multi-idioma,
licencia, niveles de acceso (`public`/`institutional`/`restricted`), `qualityIndicators`
poblados desde DQS. Seed con `iea_observations` v1.0.0.

**Quick win:** publicable antes que el resto. Statement de intención para stakeholders.

### D8 — Auth, permisos, cuotas, auditoría

**Depende de:** M0. **Bloqueante de:** D4, D5.

`auth.organization`, `auth.api_key`, `auth.dataset_permission`, `auth.usage_log` (particionada).
API keys con prefijo legible (`yvy_live_...`). Scopes string-based. Constraints por
estación/variable/org en JSONB, aplicados server-side. Cuotas (minuto/día/filas-mes) en
Postgres (migrar a Redis solo si duele). Auditoría con retención 24m.

### D9 — Outliers contextuales

**DIFERIDO** hasta ≥6 IEAs activas. Sin densidad de red no aporta señal.

## Track IA — fases generales

```
I0  IA Architecture Foundation   (ports hexagonales + pgvector)
I1  Knowledge Layer (schemas)
I2  RAG Ingestion (chunk + embed, fundidos)
I3  Context Builder              [el corazón: retrieval + datos vivos + permisos]
I4  Insight Generation
I5  Agronomic Assistant
I6  Institutional Assistant      (consume API institucional)
I7  Predictive Models            [rama casi independiente, depende de Silver/Gold no de RAG]
I8  Multi-Agent Layer            [DIFERIDO agresivamente]
```

Nota: el "I3 Embeddings" del borrador inicial se funde en I2 (ingestar sin embeddear no sirve).
El Context Builder (I3 acá) gana peso propio: une retrieval semántico + datos en vivo +
permisos del cliente. I7 Predictive puede arrancar apenas Silver (D1) cierre, sin esperar todo
el RAG.

**Decisión 2026 del Track IA:** solo I0 (ports + pgvector). Permite que los ayudantes
prototipen con Ollama local (costo cero) mientras el contrato queda fijo. Todo lo demás se
difiere sin deuda.

**Trazabilidad obligatoria desde I2:** cada respuesta de IA debe poder citar su fuente (chunk
del documento X, o measurement Z). Sin esto la IA no es citable y por tanto no es usable en
contexto educativo/científico. Es requisito, no feature.

## Track FUENTES — fases generales

Cada nueva fuente es una fase. Todas comparten el patrón: adapter de ingesta + seed de
`canonical.variable/source/sensor` + mapeo de payload a `variable_key`. **Ninguna requiere
refactor del Core** gracias al modelo canónico.

```
F1  Open-Meteo (referencia)          [parte de D2, primera external_api]
F2  Estaciones multi-fabricante      (Davis, Ecowitt, Campbell) vía Edge
F3  Sensores de suelo (RS485)        vía Edge
F4  Sensores de agua / pH / conductividad   vía Edge
F5  NASA POWER / SMN (referencia)    external_api
F6  Satelital (Sentinel)             metadata en DB, raster en storage
F7  Topografía / DEM (SRTM)          metadata + PostGIS
F8  Capas GIS                        PostGIS
```

Evaluación de compatibilidad (ninguna rompe el Core):

| Fuente | ¿Cambio de arquitectura? | Qué necesita |
|---|---|---|
| Davis / Ecowitt / Campbell | No | `source` + `sensor_type` nuevos, mapeo en promoción, adapter Edge |
| RS485 | No | Adapter Edge (traduce a JSON), entra a Bronze genérico |
| LoRaWAN / MQTT | No (Core); gateway en Edge | El Core recibe JSON; el protocolo lo resuelve el gateway |
| Open-Meteo / SMN / NASA POWER | No | `source_type='external_api'`, job de polling → Bronze |
| Sentinel (satelital) | Parcial (storage) | Metadata en DB, raster en object store vía `storage_uri` |
| SRTM / DEM | Parcial (PostGIS) | Metadata en DB, raster en storage, queries espaciales con PostGIS |
| Capas GIS | Aditivo (PostGIS) | Geometrías como tipo nativo |

---

# PARTE IV — SECUENCIA Y DEPENDENCIAS CRUZADAS

```
                          ┌─→ TRACK CORE (W2-W6) ──→ Launch Web V1
                          │      (mocks → API real)
M0 (CICDv3) ──┬───────────┤
              │           └─→ C10 re-apunta a Silver  (parte de CICDv3)
              │
              ├─→ D1 Silver ──┬─→ D2 DQS+ref ──┐
              │               ├─→ D3 Gold      │
              │               │                ├─→ D4 API Inst ──→ D5 Exports ──→ D6 Parquet
              │               └─→ I7 Predictive│         ↑
              │                                │         │
              ├─→ D8 Auth ─────────────────────┘─────────┘
              │
              ├─→ D7 Catálogo (paralelo, publicable temprano)
              │
              └─→ I0 IA Foundation ──→ I1 Knowledge ──→ I2 RAG ──→ I3 Context ──→ I4-I6 Asistentes
                                                                                       ↑
                                                              (I6 consume D4 API Inst)─┘

Fuentes (F2-F8): se enchufan a M0+D1 cuando el hardware/acceso exista. No bloquean nada.
```

**Camino crítico mínimo viable de plataforma de datos:** `M0 → D1 → D2 → D8 → D4 → D5`.
Catálogo (D7) e IA Foundation (I0) en paralelo.

---

# PARTE V — RUTA DE 24 MESES (4 fases generales temporales)

Sin estimaciones absolutas. Bloques de prioridad que desembocan en el año 3 preparado para
streaming/orquestación pero sin necesitarlo.

### Bloque 1 — Cimiento + Launch Web (meses 1-6)

Objetivo: validación comercial + base canónica sólida.

- **M0** (CICDv3): migración canónica, backfill, pgvector + PostGIS. Bloqueante.
- Track Core: W2-W6, C12-C16 sobre Silver. Launch Web V1.
- **D1** Silver + **D2** DQS+referencia.
- **I0** IA Foundation (ports + pgvector) — barato, en paralelo.
- **D7** Catálogo (un dataset) — quick win institucional.

Resultado: Web pública en vivo, datos validados con referencia externa, base lista para
heterogeneidad.

### Bloque 2 — Plataforma de datos institucional (meses 6-12)

Objetivo: vender/entregar datasets a INTA, INET, etc.

- **D8** Auth + permisos + cuotas.
- **D3** Gold (indicadores multi-dominio: VPD agregado, riesgo de helada).
- **D4** API Institucional v1.
- **D5/D6** Exports CSV/XLSX/Parquet.
- **F2** primeras estaciones multi-fabricante si hay hardware.

Resultado: `data.yvyedge.com` operativo. Datasets descargables, citables, con calidad
auditable.

### Bloque 3 — Conocimiento + IA (meses 12-18)

Objetivo: diferenciación por IA, asistentes.

- **I1** Knowledge Layer + **I2** RAG Ingestion.
- **I3** Context Builder + **I4** Insight Generation.
- **I5/I6** Asistentes (agronómico, institucional).
- **I7** Modelos predictivos (helada, en paralelo desde antes).
- **F3/F4** sensores de suelo/agua si hay hardware.

Resultado: asistente que combina datos en vivo + conocimiento documental, con trazabilidad.

### Bloque 4 — Expansión territorial + preparación año 3 (meses 18-24)

Objetivo: plataforma ambiental completa, lista para escalar.

- **F6/F7/F8** satelital, topografía, GIS (riesgo de deslizamiento/inundación con PostGIS).
- **D9** outliers contextuales (si ≥6 IEAs).
- Indicadores territoriales en Gold (riesgo de incendio, deslizamiento).
- **Evaluación de transición a Kafka/Airflow**: si el volumen y la cantidad de pipelines lo
  justifican. El diseño actual (systemd timers + CLI commands + ports) permite la transición
  sin reescribir lógica: un DAG de Airflow llama al mismo CLI; un consumer de Kafka escribe al
  mismo Bronze genérico.

Resultado: plataforma ambiental territorial con IA, preparada para streaming sin haberlo
necesitado todavía. Si la transición se vuelve necesaria, es porque hubo éxito.

---

# PARTE VI — QUICK WINS (alta señal / bajo costo)

Ordenados por relación señal/esfuerzo:

1. **D7 Catálogo DCAT con un dataset** — statement de intención técnica, antes del resto.
2. **M0 con schemas vacíos** (silver/gold/auth/exports/knowledge/canonical) — una migración,
   ahorra refactor enorme.
3. **DQS `/explain` endpoint versionado** — sin esto no podés citar el DQS en un paper.
4. **`silver.measurement` definida** + particionado — bloqueo de futuro refactor.
5. **`LICENSE.md` de datos** + conversación con legales del ministerio — bloqueante institucional.

---

# PARTE VII — RIESGOS Y DEUDA

### Deuda silenciosa a evitar (no se ve hasta que duele)

1. Derivadas como columnas tipadas en Silver → usar measurements con `is_derived`.
2. Offset pagination en API institucional → cursor desde el día 1 (B11).
3. Unidades hardcodeadas → `unit_key` siempre (B12).
4. IA con acceso directo a DB → consume API (B14).
5. Mezclar ground truth con referencia externa → `source_type` desde Bronze (B6).

### Riesgo no técnico — Bus factor 1

Documentado en `propósito.md`. El Track DATA + IA aumenta la superficie que solo Gonzalo puede
mantener. Mitigación ejecutable: documentación obligatoria por endpoint (OpenAPI completo),
tests de integración como spec ejecutable, runbook operativo por track. La cohorte de ayudantes
2do→3er año absorbe parte vía los prompts específicos por fase.

### Riesgo legal / regulatorio

Antes de exponer un dataset público: confirmar con legales del ministerio propiedad de datos de
IEAs, restricciones del convenio INET, uso comercial. Si hay ambigüedad, salir con
`accessRights: "public"` pero con términos custom (retención de derechos) en vez de CC-BY-4.0.
Cambiar a CC-BY-4.0 después es trivial; revocar CC-BY ya otorgada es legalmente complicado.

### Nota sobre metadata sensible de sensores

La metadata técnica del sensor (modelo, tipo, firmware) es inocua y puede vivir en
`canonical.sensor.metadata` (JSONB), recibida on-request del nodo. Pero la **ubicación fina de
instalación** (lat/lon exacta de un lote privado) es dato con permiso, no metadata pública —
tratarla bajo control de acceso cuando lleguen clientes B2B. A definir en sesión de YVY Edge.

---

# PARTE VIII — RESUMEN EJECUTIVO

| Pregunta | Respuesta |
|---|---|
| ¿Silver/Gold demasiado meteorológicos? | Sí en el borrador previo. Corregido: long format genérico + proyecciones por dominio. |
| ¿Modelo de observaciones genérico? | Sí, vía modelo canónico (SOSA/O&M), no EAV plano. |
| ¿Catálogo soporta otros dominios? | Sí, DCAT es agnóstico de dominio por diseño. |
| ¿API institucional soporta datasets heterogéneos? | Sí, `dataset_id` + `?variables=` no asumen meteorología. |
| ¿Hay que refactorizar Bronze ahora? | Sí — `raw_record` genérico + `source_id`. 10k filas, barato. |
| ¿Knowledge en la cadena medallion? | No — capa ortogonal, pipeline propio. |
| ¿IA ciudadano de primera clase? | Sí — Track IA con ports hexagonales, consume la propia API. |
| ¿Pipeline IA agnóstico de modelo? | Sí — puertos + adapters. Decisión 2026: solo ports + pgvector. |
| ¿Alguna fuente futura rompe el Core? | Ninguna. Satelital/GIS tocan storage y extensiones, no el modelo. |
| ¿Cuándo Kafka/Airflow? | Año 3, si el éxito lo exige. El diseño permite transición sin reescribir lógica. |
| Decisión más importante de 2026 | Modelo canónico antes de Silver (B1). |
| Lo más sobrevalorado | Kafka, Airflow, Redis, vector DB externa, multi-agente en V1. |

---

**Fin del Plan Maestro · YVY Data Platform v1.0**

Próximo paso tras aprobación: descomponer cada fase general en fases específicas en sesiones
dedicadas. Aplicar `RUNBOOK_CICDv3.md` (M0) como primer movimiento, ya que bloquea todo lo demás.
