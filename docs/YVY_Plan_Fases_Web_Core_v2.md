# YVY · Plan de Fases Web + Core (v2.0)

**Versión:** 2.0
**Fecha:** 08 de Junio de 2026
**Autor:** Gonzalo Avila
**Propósito:** Plan de implementación dual de `web_yvyedge` (landing pública en `yvyedge.com`) más el track de lectura pública del Core (continúa post Fase 9.3 del server `yvy_server`). Diseñado para usarse entre sesiones de Claude Code sin perder contexto, con peer review por fase antes de pasar a implementación.

**Cambios respecto a v1.0:**
- Filosofía V2 alineada al modelo comercial (Landing + Capa Comercial): el público ve agregaciones provinciales reales; la granularidad por estación queda gated para acceso autenticado futuro.
- Arquitectura de tres dominios consolidada (`yvyedge.com` + `edge.yvyedge.com` + `api.yvyedge.com`).
- Sección "Timeline de eventos meteorológicos" eliminada en V1. Reemplazada por "Novedades + Insights" con contenido editorial publicado vía CLI.
- Detección automática de eventos meteorológicos diferida a fase post-launch (cuando IA esté integrada).
- Renombre del repo Web a `web_yvyedge` (snake_case).
- Estimaciones de tiempo removidas — solo se mantiene estado y dependencias.

---

## Cómo usar este documento

Antes de empezar una sesión, identificar la fase activa (Web o Core), copiar el "Prompt de inicio" en una nueva conversación con Claude Code local sobre el repo correspondiente. Al terminar la sesión, actualizar el estado, agregar notas de implementación, marcar consultas resueltas y hacer commit del cambio del plan junto con el código.

El flujo recomendado es peer review por fase: antes de redactar el prompt definitivo, revisar el draft con Claude en chat para cerrar decisiones pendientes; luego se pasa el prompt cerrado a Claude Code.

Cada fase produce documentación obligatoria al cerrar: resumen en `docs/phases/PHASE-N-*.md`, actualización de `docs/guides/` si corresponde, prompts efectivos archivados en `docs/prompts/phase-N/`. Si una decisión cambia, actualizar la sección "Decisiones cerradas" con la fecha del cambio y la justificación.

---

## Contexto del proyecto

**Estado al 08-Jun-2026:**

Core server `yvy_server` con Fase 9.1 cerrada, 9.2 en curso, 9.3 pendiente (nginx + TLS + wiring + smoke E2E). Stack Postgres 16 + FastAPI async + nginx + Let's Encrypt sobre Debian 12. CGNAT resuelto, IP fija, dominio operativo.

Edge `yvy` con IEA Nº7 Garupá en producción capturando datos. Los 18 nodos restantes se esperan en los próximos meses.

Web `web_yvyedge` no existe todavía. Este plan define su construcción desde cero más los endpoints públicos del Core que la habilitan.

Dominio público `yvyedge.com` propiedad de Gonzalo (GoDaddy), actualmente apunta a IP del ESIM IFDyT. DNS a repuntar en Fase W0.

**Arquitectura de tres dominios (consolidada):**

```
yvyedge.com          → Presentación. Landing pública (Next.js en Vercel).
                       No almacena datos. Consume desde api.yvyedge.com.

edge.yvyedge.com     → Captura. Ingesta desde nodos Edge (POST /ingest/*).
                       Ya operativo recibiendo de los nodos instalados.

api.yvyedge.com      → Consulta. Lectura agregada para la Web pública y
                       (eventualmente) para clientes externos autenticados.
                       Subdominio nuevo a crear en Fase C11.
```

La separación corresponde a tres responsabilidades distintas: presentación, captura y consulta. La infraestructura interna puede evolucionar (Kafka, Lakehouse, Redis) sin romper los contratos externos de estos tres dominios.

**Modelo de negocio (contexto, no scope técnico):**

Capa pública: institucional, dirigida a INET, ministerio, provincia. La Web V1 sirve a esta capa.

Capa privada (post-V1, fuera de scope inmediato): cooperativas, empresas, productores. Datos específicos por zona, instalación de nodos, servicios diferenciados. Las CTAs de V1 sirven para validación de mercado de esta capa.

---

## Decisiones cerradas (al 08-Jun-2026)

| # | Decisión | Valor | Fecha cierre |
|---|---|---|---|
| W1 | Framework Web | Next.js 14 App Router + TypeScript estricto | 08-06 |
| W2 | Estilos | Tailwind CSS con tokens YVY como CSS variables | 08-06 |
| W3 | Internacionalización | `next-intl` desde Fase W1. Idiomas: ES (principal), PT, EN | 08-06 |
| W4 | Mapa | Leaflet + OpenStreetMap tiles (gratuito, sin token) | 08-06 |
| W5 | Gráficos | Recharts | 08-06 |
| W6 | Data fetching | SWR con `refreshInterval: 60000` | 08-06 |
| W7 | Animaciones | Framer Motion (opcional, donde aporte) | 08-06 |
| W8 | Deploy inicial | Vercel + GitHub OAuth (repo privado) | 08-06 |
| W9 | Portabilidad | Dockerfile + docker-compose.yml desde Fase W0 | 08-06 |
| W10 | Branching | `main` estable + `develop` activa + branch por fase, merge al cierre con tag `vN.M-phaseX` | 08-06 |
| W11 | Documentación | `docs/{decisions,phases,guides,prompts}/` en repo Web | 08-06 |
| W12 | Form de contacto | `mailto:yvyedge@gmail.com` simple en Fase W5, sin backend | 08-06 |
| W13 | Datos demo | Mocks en `/public/mock/` que respetan el contrato. Swap por Core cuando endpoints estén listos | 08-06 |
| W14 | IEA activa real | Nº7 Garupá (única en producción al 08-Jun). Resto se muestra como "en despliegue" | 08-06 |
| W15 | Granularidad de gráficos públicos | `/public/dashboard` solo acepta `station=network`. Granularidad por estación específica gated para acceso autenticado futuro | 08-06 |
| W16 | Sin números truchos ni blur | Lo que no se muestra públicamente se reemplaza por CTA contextual ("¿Querés esto para tu zona? Contactanos"). Funciona como validación de mercado | 08-06 |
| W17 | Repo Web nombre | `web_yvyedge` (snake_case) | 08-06 |
| C1 | Endpoints públicos Core | Prefijo `/api/v1/public/*`, sin auth, lectura agregada. Servidos desde `api.yvyedge.com` | 08-06 |
| C2 | Contrato de datos | Definido del lado Web en `web_yvyedge/docs/guides/DATA-CONTRACT.md`. Core implementa contra esa spec | 08-06 |
| C3 | Capa de agregación Core | Views SQL regulares (no materialized). Refresh on-demand. Cache HTTP del lado Core en C16 | 08-06 |
| C4 | Cache HTTP | `Cache-Control: public, max-age=30, stale-while-revalidate=60` en endpoints públicos | 08-06 |
| C5 | Novedades | Publicadas manualmente vía CLI `yvy-server publish-news`. No hay detector automático de eventos meteorológicos en V1 | 08-06 |
| C6 | Insights | Publicados vía CLI `yvy-server publish-insight`. Attachments en filesystem local servidos por nginx bajo `/assets/insights/` | 08-06 |
| C7 | Detector automático de eventos meteorológicos | Diferido a fase post-launch, cuando IA esté integrada. No va en V1 | 08-06 |
| WC1 | Subdominio Core para Web | `api.yvyedge.com` (subdominio nuevo a crear en C11). `edge.yvyedge.com` queda exclusivo para ingesta de nodos | 08-06 |
| WC2 | Aviso demo | Badge discreto "Demo · Red en despliegue" en header hasta que los 18 nodos estén operativos | 08-06 |
| WC3 | Storage de attachments de Insights | Filesystem local del server (`/opt/yvy-server/insights-assets/`) servido por nginx con URL pública. Migrar a object store si volumen lo justifica | 08-06 |

---

## Decisiones abiertas

Ninguna al momento de generar v2.0. Si surgen durante implementación, agregar acá con fecha y resolverlas en la fase respectiva antes de cerrar.

---

## Estructura general

### Track Web (`web_yvyedge`)

| Fase | Nombre | Estado | Bloqueante de | Depende de Core |
|---|---|---|---|---|
| W0 | Bootstrap del repo + DNS + deploy hello world | Pendiente | Todas las Web | — |
| W1 | Skeleton estático + i18n + mocks (10 secciones V2) | Pendiente | W2+ | — |
| W2 | Mapa + Estado de Red en vivo | Pendiente | W3+ | C10, C11 |
| W3 | Inteligencia Aplicada (gráficos provinciales) | Pendiente | W4+ | C12, C13 |
| W4 | Insights + Novedades | Pendiente | W5+ | C14, C15 |
| W5 | Form de contacto + CTAs persistentes | Pendiente | W6 | — |
| W6 | Production readiness + SEO + analytics | Pendiente | Launch | — |

### Track Core (continúa la numeración post Fase 9.3)

| Fase | Nombre | Estado | Bloqueante de Web |
|---|---|---|---|
| C10 | Capa de agregación SQL + views | Pendiente | W2 |
| C11 | Subdominio `api.yvyedge.com` + endpoints `/public/network` y `/public/stations` | Pendiente | W2 |
| C12 | Endpoints `/public/metrics` y `/public/dashboard` (provincial) | Pendiente | W3 |
| C13 | Variables derivadas (VPD, dewpoint) | Pendiente | W3 |
| C14 | Sistema de Novedades + CLI `publish-news` + endpoint `/public/news` | Pendiente | W4 |
| C15 | Sistema de Insights + CLI `publish-insight` + endpoint `/public/insights` + assets local | Pendiente | W4 |
| C16 | Cache layer en endpoints públicos | Pendiente | Launch (recomendado) |

### Secuencia recomendada

```
Bloque 1 (Web foundation):  W0 → W1
Bloque 2 (Core network):    C10 → C11
Bloque 3 (Web mapa):        W2
Bloque 4 (Core dashboard):  C12 → C13
Bloque 5 (Web inteligencia): W3
Bloque 6 (Core contenido):  C14 → C15
Bloque 7 (Web contenido):   W4
Bloque 8 (cierre):          W5 → C16 (paralelo) → W6
```

Alternancia Web/Core sin context-switching agresivo. Si una fase Core se atrasa, la Web sigue con mocks; cuando Core cierra, swap directo.

---

# Fase W0 — Bootstrap del repo + DNS + deploy hello world

**Estado:** Pendiente
**Bloqueante de:** Todas las fases Web

## Prompt de inicio

> Estoy iniciando la Fase W0 del proyecto `web_yvyedge`. Es un repo Next.js 14 nuevo, vacío, sobre un dominio `yvyedge.com` que ya tengo en GoDaddy y necesito repuntar. Necesito hacer el bootstrap completo:
>
> 1. Inicializar Next.js 14 con TypeScript estricto, App Router, Tailwind CSS, ESLint, sin directorio `src/` (`app/` y `components/` en la raíz).
> 2. Configurar Tailwind con las variables CSS del sistema YVY como tokens del theme (paleta y tipografía las paso en el contexto).
> 3. Cargar tipografía Jost localmente vía `next/font/local` (no Google Fonts en runtime). Inter como fallback de body.
> 4. Generar Dockerfile multi-stage (build + runtime slim) y `docker-compose.yml` para desarrollo local.
> 5. Configurar Husky + lint-staged + Prettier para pre-commit.
> 6. Crear estructura de carpetas:
>    ```
>    app/
>      [locale]/
>        page.tsx
>        layout.tsx
>      api/
>    components/
>      ui/
>      sections/
>      map/
>    lib/
>    public/
>      mock/
>      images/
>      logos/
>    docs/
>      decisions/
>      phases/
>      guides/
>      prompts/
>    ```
> 7. Generar `README.md` raíz con propósito, requisitos, instalación, comandos de desarrollo, estructura.
> 8. Generar `docs/guides/DEV-GUIDE.md` con setup paso a paso.
> 9. Generar `docs/decisions/0001-tech-stack.md` con todas las decisiones W1-W17 del plan.
> 10. Generar `docs/guides/DATA-CONTRACT.md` copiando el Apéndice A de este plan.
> 11. Generar `.env.example` con variables documentadas (`NEXT_PUBLIC_SITE_URL`, `YVY_CORE_URL=https://api.yvyedge.com`, `YVY_CORE_API_VERSION=v1`, `NEXT_PUBLIC_USE_MOCK=true`).
> 12. Generar página `app/[locale]/page.tsx` con un "YVY Edge — Hello World" mínimo.
> 13. Commit inicial: `chore: bootstrap web_yvyedge (phase W0)`.
>
> Confirmar antes de generar:
> - La paleta YVY: azul profundo `#1E3A5F`, azul claro `#3B5F82`, dorado `#C9A227`, oliva `#9AA223`, crema `#F5F1E8`, tierra `#2B2621`.
> - Estructura de carpetas final.
> - Si querés `pnpm` (recomendado) o `npm`.

## Objetivo

Repo `web_yvyedge` inicializado, instalable, con `pnpm dev` corriendo en `localhost:3000`, deployado en Vercel respondiendo en `yvyedge.com` con un hello world.

## Tareas concretas

- [ ] Crear repo privado `web_yvyedge` en GitHub.
- [ ] Inicializar Next.js 14 con TypeScript + Tailwind + App Router.
- [ ] Configurar Tailwind con tokens YVY (paleta + tipografía Jost local).
- [ ] Dockerfile multi-stage + docker-compose.yml.
- [ ] Husky + lint-staged + Prettier.
- [ ] Estructura de carpetas completa con README placeholder.
- [ ] `docs/decisions/0001-tech-stack.md` con W1-W17.
- [ ] `docs/guides/DEV-GUIDE.md` con setup.
- [ ] `docs/guides/DATA-CONTRACT.md` (copia del Apéndice A).
- [ ] `.env.example` con todas las variables.
- [ ] Página hello world en `app/[locale]/page.tsx`.
- [ ] Conectar repo a Vercel con auto-deploy desde `main`.
- [ ] **DNS: cambiar el A record de yvyedge.com en GoDaddy** del IP actual del ESIM IFDyT al CNAME de Vercel (`cname.vercel-dns.com`).
- [ ] Esperar propagación DNS y verificar HTTPS automático de Vercel.
- [ ] Configurar branch protection en `main`.

## Criterios de cierre

`pnpm install && pnpm dev` levanta la app en localhost:3000 sin warnings. `pnpm lint` y `pnpm format` ejecutan sin errores. `docker compose up` levanta la app en localhost:3000. Push a `main` triggea auto-deploy a Vercel. `https://yvyedge.com` responde con el hello world y certificado SSL válido. Branch `develop` creada y configurada como default para PRs.

## Notas de implementación

DNS de GoDaddy puede tardar hasta 30 minutos. Mientras tanto se accede vía `web-yvyedge.vercel.app` (Vercel normaliza guiones bajos a guiones). El plan free de Vercel cubre la carga proyectada. No instalar `next-intl`, `recharts`, `leaflet` en W0 — eso es W1+. No usar `src/` directory.

## Log de consultas

*(vacío)*

---

# Fase W1 — Skeleton estático + i18n + mocks

**Estado:** Pendiente
**Bloqueante de:** W2+
**No depende de Core.**

## Prompt de inicio

> Estoy iniciando la Fase W1 del proyecto `web_yvyedge`. La Fase W0 está cerrada (repo bootstrapped, Tailwind con tokens YVY, deploy en Vercel funcionando, dominio apuntado). Ahora voy a maquetar las 10 secciones de la landing V2 con datos mock estáticos, con i18n configurado desde el día uno.
>
> Necesito que generes:
>
> 1. **i18n con `next-intl`:** configuración en `i18n.ts` raíz, idiomas `es` (default), `pt`, `en`. Estructura `messages/{es,pt,en}.json` con todas las claves necesarias. Middleware de detección de locale por Accept-Language. Componente `LocaleSwitcher` en el header.
>
> 2. **Layout y header global:**
>    - `app/[locale]/layout.tsx` con metadata, fonts, providers de SWR + next-intl.
>    - Header sticky con: logo YVY Edge horizontal pequeño sobre fondo transparente, navegación interna por anchors (`#red`, `#cobertura`, `#capacidades`, `#inteligencia`, `#novedades`, `#educacion`, `#nosotros`), LocaleSwitcher, badge "Demo · Red en despliegue" (decisión WC2), CTA "Solicitar demostración" alineado a la derecha.
>
> 3. **Mocks completos en `/public/mock/`:**
>    - `network.json`: 18 nodos, 1 activo real (IEA Nº7 Garupá, coordenadas `-27.4869, -55.8276`), resto en `pending`. Total records mock razonable.
>    - `stations.json`: array de 18 estaciones con id, code, name, locality, coordinates, status, lastSyncAt, lastValues (T/H/P solamente — sin granularidad histórica).
>    - `metrics.json`: KPIs agregados a nivel provincial para período `24h`.
>    - `dashboard.json`: serie temporal mock de temperatura provincial (7 días). Solo `station=network`.
>    - `news.json`: 5 items mockeados de novedades editoriales (despliegues, hitos).
>    - `insights.json`: 3 items mockeados con title, summary, body markdown corto, chartConfig opcional, publishedAt.
>    - Cada mock debe respetar el schema definido en `docs/guides/DATA-CONTRACT.md`.
>
> 4. **Las 10 secciones de la landing en orden:**
>
>    a. `Hero.tsx` — 80vh, foto del sensor de IEA Nº7 Garupá como background (subo a `/public/images/hero/`), overlay azul oscuro. Título: "Información territorial para anticipar riesgos". Subtítulo: "Red distribuida de observación ambiental para prevención, mitigación y toma de decisiones". CTAs: "Solicitar demostración" (primary) y "Conocer la red" (scroll a #red).
>
>    b. `WhatIsYvy.tsx` — Sección breve (3-4 líneas) explicando qué es YVY Edge. Mensaje: red distribuida de monitoreo ambiental que captura observaciones territoriales y las transforma en información para toma de decisiones. Tres badges: Captura (Edge) · Procesamiento (Core) · Inteligencia (IA).
>
>    c. `NetworkStatus.tsx` — Sección "#red". 4 KPIs grandes: Nodos activos, Localidades cubiertas, Observaciones acumuladas, Disponibilidad de red. Animación de contador con Framer Motion al entrar en viewport. Sin variables meteorológicas en esta sección.
>
>    d. `CoverageMap.tsx` — Sección "#cobertura". Placeholder en W1 (mapa real en W2): imagen estática del mapa de Misiones con los 18 puntos numerados. Texto explicativo sobre cobertura territorial.
>
>    e. `Capabilities.tsx` — Sección "#capacidades". 4 bloques visuales con icono + título + descripción corta:
>       - Municipios → "Monitoreo territorial y prevención de eventos extremos"
>       - Producción → "Información ambiental localizada para decisiones agropecuarias"
>       - Seguros → "Evaluación y mitigación de riesgos climáticos"
>       - Investigación → "Datos estructurados, trazables y de alta resolución"
>
>    f. `AppliedIntelligence.tsx` — Sección "#inteligencia". Placeholder en W1 (gráficos reales en W3): 4 cards conceptuales para los gráficos a nivel provincial que vienen en W3 (Temperatura, Humedad+VPD, Presión, Viento). Cada card con un CTA "¿Querés esto para tu zona o estación específica? Contactanos" → scroll a contacto.
>
>    g. `NewsAndInsights.tsx` — Sección "#novedades". Placeholder en W1 (contenido real en W4): 3-5 cards de novedades con title, date, summary, link "Leer más". Mostrar también una franja con últimos insights (artículos cortos editoriales).
>
>    h. `Education.tsx` — Sección "#educacion". Mantiene la capa pública/educativa del proyecto. Explica: Instituciones Educativas Agropecuarias (IEA), formación tecnológica, ciencia de datos, participación territorial, articulación con INET y ministerio.
>
>    i. `About.tsx` — Sección "#nosotros". Misión, visión, origen del proyecto, equipo.
>
>    j. `Contact.tsx` — Sección "#contacto". CTA principal "Solicitar demostración" (form completo en W5). CTA secundario "Conocer oportunidades de implementación". Por ahora ambos navegan a un mailto placeholder.
>
> 5. **Componentes UI base en `components/ui/`:**
>    - `MetricCard.tsx` — número grande + unidad + label + trend opcional.
>    - `StatusBadge.tsx` — pill coloreado por estado (online/sync_pending/offline/pending).
>    - `NewsCard.tsx` — card con imagen opcional, fecha, título, resumen, link.
>    - `InsightCard.tsx` — variante de NewsCard para artículos editoriales.
>    - `CapabilityBlock.tsx` — bloque de la sección Capacidades.
>    - `Button.tsx` — primary/secondary con variantes de tamaño.
>    - `NodeIndicator.tsx` — dot coloreado con tooltip.
>    - `ContextualCTA.tsx` — componente reutilizable para las CTAs contextuales de validación de mercado (W16).
>
> 6. **SWR setup en `lib/swr.ts`:** configuración global con `refreshInterval: 60000`, `revalidateOnFocus: false`, `errorRetryCount: 3`. Hook `useMockOrApi<T>(mockPath, apiPath)` configurable vía `NEXT_PUBLIC_USE_MOCK`.
>
> 7. **SEO básico:** metadata por locale, favicon set completo, robots.txt permitiendo todo.
>
> 8. **Responsive completo mobile-first:** CTA "Solicitar demostración" en header → en mobile pasa a botón sticky bottom-right.
>
> Confirmar antes de generar:
> - Las 10 secciones listadas son las definitivas.
> - Estructura del DATA-CONTRACT.
> - Cuáles imágenes del hero usar (las del sensor en techo de Garupá).

## Objetivo

Landing completa visualmente, navegable, responsive, multilingüe, deployada. Una persona puede recorrerla y entender qué es YVY Edge, qué hace, a quién apunta y cómo contactarlos. Todo con mocks — nada está "vivo" todavía, pero la estructura final está cerrada.

## Tareas concretas

- [ ] Setup `next-intl` con 3 locales.
- [ ] Mensajes en `messages/{es,pt,en}.json`.
- [ ] Layout global + Header + Footer.
- [ ] Mocks completos en `/public/mock/` (network, stations, metrics, dashboard, news, insights).
- [ ] Las 10 secciones maquetadas con componentes propios.
- [ ] Componentes UI base reutilizables.
- [ ] Hook SWR con switch mock/api.
- [ ] Responsive: mobile, tablet, desktop.
- [ ] SEO básico.
- [ ] Badge "Demo · Red en despliegue".
- [ ] CTAs contextuales `ContextualCTA` en lugar de números truchos o blur (W16).
- [ ] Tests de smoke con Playwright (la home carga sin errores en los 3 locales).
- [ ] `docs/phases/PHASE-W1-skeleton.md` con resumen.

## Criterios de cierre

`https://yvyedge.com`, `https://yvyedge.com/pt`, `https://yvyedge.com/en` cargan sin errores. Lighthouse: Performance ≥ 85, Accessibility ≥ 95, Best Practices ≥ 95, SEO ≥ 95. Switcher de idioma funciona y persiste en URL. Form de contacto navega a `mailto:` (implementación completa en W5). Responsive sin overflow horizontal en mobile/tablet/desktop. Smoke test Playwright verde. Cualquier visitante puede entender qué es YVY Edge y a quién apunta sin contexto previo.

## Notas de implementación

`next-intl` con App Router v14 tiene un setup específico — usar la documentación oficial actualizada. Foto del hero: la imagen 4 (sensor con árboles y patio de fondo) es la mejor por composición; aplicar overlay azul `rgba(13, 22, 33, 0.65)` para legibilidad. Traducciones iniciales pueden ser auto-generadas para PT y EN; revisión nativa en W6. No conectar todavía con Core — todo es mock.

## Log de consultas

*(vacío)*

---

# Fase W2 — Mapa + Estado de Red en vivo

**Estado:** Pendiente
**Bloqueante de:** W3+
**Depende de Core:** C10 (views SQL), C11 (subdominio + endpoints network/stations).

## Prompt de inicio

> Estoy iniciando la Fase W2 del proyecto `web_yvyedge`. Las Fases W0 y W1 están cerradas. Ahora voy a integrar el mapa real con Leaflet y conectar los endpoints reales del Core para estado de red.
>
> Requisitos previos:
> - Core debe tener Fases C10 y C11 cerradas. `https://api.yvyedge.com/api/v1/public/network` y `/public/stations` respondiendo según el contrato.
>
> Necesito que generes:
>
> 1. **Componente `NodesMap.tsx`** con dynamic import (`ssr: false`):
>    - Centro inicial: Posadas (`-27.3621, -55.8954`), zoom 7.
>    - Tiles: OpenStreetMap.
>    - Bounds para encerrar Misiones más franja de Brasil/Paraguay para contexto.
>    - Markers customizados por estado: verde (online), gris (offline), dorado (sync_pending), traslúcido (pending — no instalado).
>    - Popup al click: nombre IEA, localidad, estado, último sync, y una pequeña muestra in vivo del último valor de Temperatura, Humedad y Presión. SIN granularidad temporal, SIN gráfico, SIN link a dashboard específico.
>    - Leyenda discreta en esquina inferior izquierda.
>
> 2. **Endpoint proxy `app/api/network/route.ts`:** GET handler que llama a `${YVY_CORE_URL}/api/v1/public/network`. Si `NEXT_PUBLIC_USE_MOCK=true`, devuelve `/public/mock/network.json`. Cache: `s-maxage=30, stale-while-revalidate=60`. Manejo de errores con fallback a último valor en cache + flag `stale: true`.
>
> 3. **Endpoint proxy `app/api/stations/route.ts`:** análogo.
>
> 4. **Hook `useNetworkStatus()` y `useStations()`** en `lib/hooks/`: SWR fetch con devolución `{ data, error, isLoading, isStale }`.
>
> 5. **Refactor de las secciones:**
>    - `CoverageMap.tsx`: reemplazar imagen estática por `<NodesMap />` con datos reales.
>    - `NetworkStatus.tsx`: leer KPIs reales de `useNetworkStatus()`. Mantener animación de contador.
>    - Si `isStale === true`: badge "Datos en cache · reconectando".
>    - Si todas las estaciones están en `pending` (caso real al inicio): no romper la UI, mostrar mensaje "Red en despliegue: X de 18 nodos activos".
>
> 6. **Coordenadas reales de las 18 IEAs** en `lib/stations-misiones.ts` (fallback si Core no responde y referencia visual). IEA Nº7 Garupá con coordenadas precisas; resto aproximadas según el mapa numerado.
>
> 7. **Tests:** mock del endpoint Core con MSW. Verificar que el mapa renderiza markers según estado. Test de error: si Core devuelve 500, la UI no rompe.

## Objetivo

Mapa de Misiones funcional con datos reales del Core. Estado de red en vivo. Es la pieza más vendedora para municipios, productores, seguros e investigación — el mapa con cobertura territorial real es lo que diferencia visualmente a YVY de cualquier landing meteorológica genérica.

## Tareas concretas

- [ ] Confirmar Core C10+C11 cerradas y `https://api.yvyedge.com` operativo.
- [ ] `NodesMap.tsx` con Leaflet + tiles OSM.
- [ ] Coordenadas 18 IEAs en `lib/stations-misiones.ts`.
- [ ] Proxy `/api/network` y `/api/stations`.
- [ ] Hooks `useNetworkStatus`, `useStations`.
- [ ] Refactor `CoverageMap` y `NetworkStatus` con datos reales.
- [ ] Manejo de error y fallback.
- [ ] Tests con MSW.
- [ ] `docs/phases/PHASE-W2-network.md`.

## Criterios de cierre

El mapa carga sin errores con tiles OSM. Markers reflejan estado real desde Core. Popup muestra T/H/P como muestra in vivo, sin granularidad ni historial. Si Core está caído, la UI sigue funcionando con badge "stale". KPIs coinciden con lo que devuelve Core. Mobile: mapa con altura razonable, touch interactions funcionan.

## Notas de implementación

Leaflet + Next.js: `dynamic(() => import('./Map'), { ssr: false })`. Iconos default rompen en producción — usar SVG inline o configurar `L.Icon.Default.mergeOptions`. CORS resuelto por el proxy `/api/*` (browser nunca habla directo con `api.yvyedge.com`).

## Log de consultas

*(vacío)*

---

# Fase W3 — Inteligencia Aplicada (gráficos provinciales)

**Estado:** Pendiente
**Bloqueante de:** W4+
**Depende de Core:** C12 (endpoints metrics + dashboard), C13 (variables derivadas).

## Prompt de inicio

> Estoy iniciando la Fase W3 del proyecto `web_yvyedge`. W0-W2 cerradas. Ahora implemento la sección de Inteligencia Aplicada con gráficos REALES a nivel promedio provincial. La granularidad por estación específica queda gated (decisión W15) y aparece como CTA contextual de captación.
>
> Requisitos previos:
> - Core debe tener C12 y C13 cerradas (endpoints `/public/metrics`, `/public/dashboard?station=network`, con VPD y dewpoint computados).
>
> Necesito que generes:
>
> 1. **Refactor de `AppliedIntelligence.tsx`:**
>    - Hook `useMetrics(period)` con SWR para KPIs provinciales.
>    - 4 MetricCards con datos reales agregados de la red: Temperatura promedio, Humedad promedio, Presión promedio, Viento promedio + dirección dominante.
>    - Cada card: valor + label + tendencia vs 24h previas (flecha + delta).
>    - Tooltip explicativo en cada métrica.
>
> 2. **Componente `ProvincialDashboard.tsx`** (sección dentro de Applied Intelligence):
>    - Layout: gráfico principal con selector de variable arriba (sin selector de estación; siempre `station=network`).
>    - Variables soportadas: Temperatura, Humedad, Presión, Viento (avg + gust), Punto de rocío, VPD.
>    - Selector de período: 24h / 7d / 30d.
>    - Gráfico Recharts según variable:
>      - Variables simples (T, H, P, dewpoint): LineChart con línea azul `#1E3A5F`.
>      - Viento: LineChart dual (avg sólido + gust punteado) con Area dorada entre las curvas.
>      - VPD: LineChart con ReferenceArea de zonas (riesgo fúngico < 0.4, óptimo 0.4-1.0, estrés > 1.0).
>    - Loading state con skeleton.
>    - Empty state: "Sin datos suficientes para el período seleccionado".
>    - **CTA contextual debajo del gráfico** (decisión W16): "¿Querés esto específicamente para tu zona, departamento o estación? Solicitanos una demostración" → scroll a contacto.
>
> 3. **Endpoints proxy:**
>    - `app/api/metrics/route.ts` → proxy de `/api/v1/public/metrics?period={period}`.
>    - `app/api/dashboard/route.ts` → proxy de `/api/v1/public/dashboard?station=network&variable={var}&period={period}`. Si la query trae otro valor de station, ignorar y forzar `network`.
>    - Cache: 30s edge cache.
>
> 4. **Lógica de umbrales y colores de trends en `lib/thresholds.ts`:**
>    - Temperatura: rangos normales según mes/estación de Misiones.
>    - Humedad: > 90% alerta fúngica.
>    - Presión: caída > 1 hPa/3h = alerta de cambio.
>    - VPD: 3 zonas definidas en C13.
>
> 5. **Tests:**
>    - Cada combinación de variable + período devuelve estructura correcta.
>    - El selector de variable cambia el gráfico correctamente.
>    - VPD muestra las 3 zonas con colores semánticos.
>    - Intentos de query con `station=<uuid>` se ignoran (fallback a network).
>
> Confirmar antes de generar:
> - El contrato exacto del endpoint `/dashboard` (estructura de `points` y `series`).
> - Si las variables derivadas (VPD, dewpoint) vienen ya computadas del Core (C13) o se computan en el frontend.

## Objetivo

Sección de Inteligencia Aplicada con gráficos reales a nivel red provincial, listos para mostrar a INET, ministerio y potenciales clientes. La granularidad específica funciona como gancho comercial vía CTA contextual de captación.

## Tareas concretas

- [ ] Cerrar contrato de `/dashboard` con Core.
- [ ] Hook `useMetrics(period)`.
- [ ] Hook `useProvincialDashboard(variable, period)`.
- [ ] Refactor `AppliedIntelligence` con datos reales.
- [ ] `ProvincialDashboard.tsx` completo con selectores + Recharts.
- [ ] 3 tipos de gráficos según variable: línea simple, dual, con zonas.
- [ ] Umbrales en `lib/thresholds.ts`.
- [ ] Proxies y cache. Forzar `station=network` en el proxy.
- [ ] CTAs contextuales `ContextualCTA` debajo de gráficos.
- [ ] Tests.
- [ ] `docs/phases/PHASE-W3-intelligence.md`.

## Criterios de cierre

Cambiar variable cambia el tipo de gráfico correctamente. VPD muestra las 3 zonas con colores semánticos. Datos provinciales reales coinciden con lo que devuelve Core. Cache de 30s funciona (verificable con DevTools). Intentos de inspeccionar otra estación específica fallan correctamente (proxy fuerza network). Mobile: gráfico responsive sin overflow.

## Notas de implementación

Recharts en SSR puede tener issues — dynamic import si es necesario. VPD zones con `ReferenceArea` stackeadas dentro del mismo `ResponsiveContainer`. No usar `fill="url(#gradient)"` en versiones < 2.10 de Recharts.

## Log de consultas

*(vacío)*

---

# Fase W4 — Insights + Novedades

**Estado:** Pendiente
**Bloqueante de:** W5
**Depende de Core:** C14 (sistema Novedades), C15 (sistema Insights + CLI).

## Prompt de inicio

> Fase W4 del proyecto `web_yvyedge`. W0-W3 cerradas. Implemento la sección de Insights + Novedades con contenido editorial real servido desde Core.
>
> Requisitos previos:
> - Core C14 y C15 cerradas. Endpoints `/api/v1/public/news` y `/api/v1/public/insights` operativos.
> - Al menos 2-3 novedades y 1-2 insights publicados vía CLI para tener contenido real para validar.
>
> Necesito que generes:
>
> 1. **Refactor de `NewsAndInsights.tsx`:**
>    - Dos sub-secciones: "Novedades" (eventos del proyecto, despliegues, alertas provinciales relevantes) e "Insights" (artículos editoriales más extensos).
>    - Hook `useNews(limit)` y `useInsights(limit)` con SWR.
>    - Cards con title, summary, publishedAt, locale (con fallback a ES si la traducción no está disponible — badge "Sólo en español"), link "Leer completo".
>    - Filtros opcionales por tag.
>    - Empty state coherente: "Próximamente — Estamos preparando contenido sobre la red".
>
> 2. **Rutas dinámicas para detalle:**
>    - `app/[locale]/novedades/[slug]/page.tsx` para detalle de una novedad.
>    - `app/[locale]/insights/[slug]/page.tsx` para detalle de un insight.
>    - Renderizado de markdown con `react-markdown` o `mdx`.
>    - Si el insight tiene `chartConfig`, renderizar el gráfico Recharts al pie del artículo.
>    - Si el insight tiene `attachmentUrl`, mostrar botón de descarga.
>    - Metadata SEO por artículo (title, description, OG image opcional).
>
> 3. **Endpoint proxies:**
>    - `app/api/news/route.ts` → proxy de `/api/v1/public/news?limit=N&tag=X`.
>    - `app/api/insights/route.ts` → proxy de `/api/v1/public/insights?limit=N&tag=X`.
>    - `app/api/insights/[slug]/route.ts` → proxy de `/api/v1/public/insights/{slug}`.
>    - `app/api/news/[slug]/route.ts` → análogo.
>    - Cache: 60s edge cache.
>
> 4. **i18n para fallbacks:** mensajes en `messages/{es,pt,en}.json` para "Sólo disponible en español", "Próximamente", etc.
>
> 5. **Tests:**
>    - Lista carga correctamente.
>    - Filtro por tag funciona.
>    - Detalle renderiza markdown sin errores.
>    - Fallback de idioma funciona.
>
> Confirmar antes de generar:
> - Estructura final de respuesta de `/public/news` y `/public/insights` (Apéndice A).
> - Cómo se sirven los attachments — confirmado: filesystem local del server bajo `https://api.yvyedge.com/assets/insights/<slug>/<filename>`.

## Objetivo

Sección de Novedades + Insights conectada al Core. Vos podés publicar vía CLI desde el server y aparece en la Web en minutos.

## Tareas concretas

- [ ] Hook `useNews` y `useInsights`.
- [ ] Refactor `NewsAndInsights.tsx`.
- [ ] Rutas dinámicas `/novedades/[slug]` y `/insights/[slug]`.
- [ ] Renderizado markdown + gráfico opcional + attachment opcional.
- [ ] Proxies con cache.
- [ ] Fallback de idioma con badge.
- [ ] i18n keys.
- [ ] Tests.
- [ ] `docs/phases/PHASE-W4-content.md`.

## Criterios de cierre

Novedades e insights se listan correctamente. Detalle de un insight renderiza markdown sin errores. Si el insight tiene chart, se muestra al pie. Si tiene attachment, el botón de descarga funciona. Fallback de idioma con badge "Sólo en español" funciona. Filtro por tag funciona. Cache HTTP se respeta.

## Notas de implementación

`react-markdown` con plugins de remark/rehype para sanitizar HTML. Para gráficos en insights, definir un schema JSON en el lado Core (`chartConfig`) que la Web sepa renderizar — empezar con tipos simples (line, bar) y extender si necesario. Attachment URL viene del Core ya armada — no concatenar host del lado Web.

## Log de consultas

*(vacío)*

---

# Fase W5 — Form de contacto + CTAs persistentes

**Estado:** Pendiente
**Bloqueante de:** W6
**No depende de Core.**

## Prompt de inicio

> Fase W5 del proyecto `web_yvyedge`. W0-W4 cerradas. Implemento el form de contacto simple según decisión W12 (mailto, sin backend), más los CTAs persistentes que validan mercado.
>
> Necesito:
>
> 1. **Componente `ContactForm.tsx`:**
>    - Una sola sección. Campos: Nombre, Email, Institución/Empresa, Tipo de interés (select: Municipio, Productor, Cooperativa, Empresa de seguros, Investigación, Otro), Mensaje.
>    - Validación cliente con Zod + react-hook-form.
>    - Submit: construye `mailto:yvyedge@gmail.com?subject={subject}&body={body}` URL-encoded.
>    - Mensaje de fallback si no hay cliente de mail: "Copiá la dirección yvyedge@gmail.com y escribinos a mano".
>
> 2. **CTAs persistentes:**
>    - Botón en header (desktop): "Solicitar demostración" → scroll suave a `#contacto`.
>    - Botón sticky bottom-right (mobile): mismo destino.
>    - `ContextualCTA` en cada sección donde aplique:
>      - Debajo de gráficos provinciales: "¿Querés esto específicamente para tu zona?".
>      - En Capacidades, cada bloque tiene su CTA contextual ("Solicitar demostración para municipios", etc.).
>      - Al final de `Education`: "¿Querés sumar una IEA a la red?".
>    - Cada CTA prefilla el campo "Tipo de interés" del form al hacer scroll, vía query param `?interest=municipio`.
>
> 3. **Tracking básico:** event al submit del form (sin backend, solo Plausible/Vercel Analytics tag). Configurar setup completo en W6.
>
> 4. **Tests:** validación de form, construcción correcta del mailto, prefill del tipo de interés vía query param.

## Objetivo

Capacidad de captura de interesados (municipios, productores, cooperativas, seguros, investigación) ya operativa, con tracking de origen de la conversión para validar mercado.

## Tareas concretas

- [ ] `ContactForm.tsx` con validación y selector de tipo de interés.
- [ ] Construcción correcta del mailto con todos los campos.
- [ ] CTAs en header + sticky mobile + contextuales en cada sección.
- [ ] Prefill de tipo de interés vía query param.
- [ ] Mensaje de fallback.
- [ ] Tests.
- [ ] `docs/phases/PHASE-W5-contact.md`.

## Criterios de cierre

Form valida correctamente. mailto abre cliente con todos los datos prellenados. CTAs visibles en todas las secciones relevantes. Query param `?interest=X` prefilla el form. Mobile: botón sticky no tapa contenido.

## Log de consultas

*(vacío)*

---

# Fase W6 — Production readiness + SEO + analytics

**Estado:** Pendiente
**Bloqueante de:** Launch público

## Prompt de inicio

> Fase W6 final del proyecto `web_yvyedge`. W0-W5 cerradas. Última fase antes del launch público.
>
> Necesito:
>
> 1. **SEO completo:** sitemap XML dinámico (`app/sitemap.ts`) con las 3 versiones de idioma + páginas de detalle de insights/novedades. robots.txt permitiendo crawling. Schema.org markup (Organization, Service). Open Graph + Twitter Card completos. hreflang tags. Canonical URLs.
>
> 2. **Analytics:** Plausible (recomendado, privacy-friendly, sin cookie banner necesario en EU) o Vercel Analytics. Tracking de eventos: page view, click en CTAs, submit de form, switch de idioma, click en CTA contextual con `interest` específico (para validación de mercado).
>
> 3. **Performance audit:** Lighthouse objetivo ≥ 90 en todas las categorías. Imágenes optimizadas con `next/image` (WebP/AVIF). Lazy loading de mapa y dashboard. Bundle analysis.
>
> 4. **Accessibility audit:** WCAG AA mínimo. Navegación por teclado funcional. Contraste correcto. aria-labels en iconos sin texto. Skip-to-content link.
>
> 5. **Error boundaries:** `error.tsx` y `not-found.tsx` con diseño coherente. Fallback UI si algún componente rompe.
>
> 6. **Loading states:** skeleton screens en todos los componentes con datos en vivo.
>
> 7. **Política de privacidad y términos de uso:** páginas `/privacy` y `/terms` en los 3 idiomas. Texto base generado y revisable.
>
> 8. **Status page interno:** página `/status` que muestre estado del proxy y del Core. Útil para troubleshooting durante demos.
>
> 9. **Documentación final:** `docs/guides/DEPLOY-GUIDE.md`, `docs/guides/BRAND-GUIDE.md`, `docs/guides/DATA-CONTRACT.md` sincronizado, README raíz actualizado.
>
> 10. **Revisión de traducciones PT y EN** por hablantes nativos. Reemplazar auto-traducciones por revisadas.
>
> 11. **Tag de release:** `v1.0.0` al cerrar W6, merge a `main`.

## Objetivo

Landing production-grade lista para presentación institucional al INET, al ministerio, a potenciales partners (cooperativas, seguros, productores) y al público general.

## Tareas concretas

- [ ] Sitemap, robots, schema.org.
- [ ] Analytics configurado con tracking de validación de mercado.
- [ ] Performance audit (Lighthouse 90+).
- [ ] Accessibility audit (WCAG AA).
- [ ] Error boundaries y fallbacks.
- [ ] Loading states completos.
- [ ] /privacy y /terms en 3 idiomas.
- [ ] Status page interno.
- [ ] Documentación completa.
- [ ] Revisión de traducciones PT y EN.
- [ ] Tag `v1.0.0`.
- [ ] `docs/phases/PHASE-W6-launch.md`.

## Criterios de cierre

Lighthouse Performance ≥ 90, Accessibility ≥ 95, Best Practices ≥ 95, SEO ≥ 95. WCAG AA validado con axe-core. Schema.org validado en validator.schema.org. Analytics registrando eventos correctamente. Documentación completa. Tag v1.0.0 creado.

## Log de consultas

*(vacío)*

---

# Track Core — Lectura pública

# Fase C10 — Capa de agregación SQL + views

**Estado:** Pendiente
**Bloqueante de:** Fase W2
**Continúa después de:** Fase 9.3 del plan principal Core.

## Prompt de inicio

> Estoy iniciando la Fase C10 del proyecto `yvy_server`. Las Fases 9.1-9.3 están cerradas. Ahora arranco el track de lectura pública para habilitar la Web `yvyedge.com`.
>
> Esta fase crea la base SQL — todavía no expone endpoints, solo las views/queries que C11+ van a consumir. Respeta la arquitectura hexagonal del server (`domain/application/infrastructure/presentation`).
>
> Necesito que generes:
>
> 1. **Migración Alembic** `00X_public_views.py`:
>
>    a. **Agregar campos a `public.station`** (migración aditiva si faltan):
>       - `code: text NOT NULL UNIQUE` (ej "IEA-07")
>       - `locality: text`
>       - `province: text DEFAULT 'Misiones'`
>       - `latitude: numeric(9,6)`
>       - `longitude: numeric(9,6)`
>       - `active: boolean DEFAULT true`
>
>    b. **`v_network_status`** — fila única con KPIs agregados:
>       - total_nodes (count de stations activas)
>       - active_nodes (count de stations con sync en últimos 15min)
>       - offline_nodes
>       - pending_nodes (active=false)
>       - total_records (count en bronze.sensor_raw + bronze.empirical_observation_raw)
>       - last_sync_seconds (EXTRACT EPOCH de now() - MAX(received_at_utc))
>       - localities_count (DISTINCT locality donde haya al menos un sync)
>       - uptime_30d (% buckets de 5min con al menos 1 sync de la red en los últimos 30 días — usar CTE con generate_series)
>
>    c. **`v_station_summary`** — fila por estación activa:
>       - station_uuid, code, name, locality, province, latitude, longitude
>       - status: enum derivado (online/sync_pending/offline/pending)
>       - last_sync_at
>       - lastValues: subobjeto con temp, humidity, pressure, wind_speed, wind_gust, wind_direction, timestamp — extraídos del último payload JSON
>
> 2. **Seed inicial:** UPSERT de las 18 estaciones de Misiones con coordenadas reales. Solo IEA Nº7 Garupá con `active=true`; resto `active=false`, se irán activando a medida que se desplieguen.
>
> 3. **Repository en infrastructure:** `PublicReadRepository` en `infrastructure/persistence/public_read_repository.py` con métodos `get_network_status()` y `get_station_summaries()`. Devuelve Value Objects, no ORM rows.
>
> 4. **Value Objects en dominio:** `NetworkStatus`, `StationSummary`, `LastValues` en `domain/public/`. Dataclasses frozen.
>
> 5. **Tests:** migración up/down sin errores. Views queryables con datos vacíos. Views queryables con datos sintéticos.
>
> Confirmar antes de generar:
> - Schema target (yo asumo `public`).
> - Si materializamos las views ahora (`MATERIALIZED VIEW`) o quedan como views regulares. Recomiendo regulares; materialización si C16 lo requiere.
> - Coordenadas exactas que ya tenés del mapa numerado de las 18 IEAs.

## Objetivo

Capa SQL de agregación lista. Las views responden en < 100ms con datos sintéticos. Repository disponible para consumo desde la capa application en C11.

## Tareas concretas

- [ ] Migración Alembic con campos adicionales en `station` y las dos views.
- [ ] Seed de las 18 estaciones con coordenadas.
- [ ] `PublicReadRepository` en infrastructure.
- [ ] Value Objects en `domain/public/`.
- [ ] Tests de migración + tests del repo con DB real.
- [ ] `docs/architecture/public_views.md` con queries documentadas.
- [ ] `docs/phases/PHASE-C10-aggregation.md`.

## Criterios de cierre

`alembic upgrade head` aplica sin errores. `alembic downgrade -1` revierte sin errores. `SELECT * FROM v_network_status` responde en < 100ms. `SELECT * FROM v_station_summary` responde en < 200ms con 18 estaciones. Tests verdes. PublicReadRepository devuelve VOs correctamente.

## Notas de implementación

El cálculo de `uptime_30d` es no trivial. Aproximación: % de buckets de 5min de los últimos 30 días con al menos 1 sync de cualquier estación. Implementar como CTE con `generate_series('2026-05-08'::timestamp, '2026-06-08'::timestamp, '5 minutes'::interval)`. Coordenadas pueden ajustarse con UPDATE simple después sin migración.

## Log de consultas

*(vacío)*

---

# Fase C11 — Subdominio api.yvyedge.com + endpoints /public/network y /public/stations

**Estado:** Pendiente
**Bloqueante de:** Fase W2

## Prompt de inicio

> Fase C11 del proyecto `yvy_server`. C10 cerrada (views SQL y repository listos). Ahora expongo los primeros endpoints públicos vía un subdominio nuevo `api.yvyedge.com`.
>
> Necesito que generes:
>
> 1. **DNS:** crear A record `api.yvyedge.com` → IP fija del server (la misma que tiene `edge.yvyedge.com`).
>
> 2. **Certificado Let's Encrypt** para `api.yvyedge.com` agregando el dominio al `certbot certonly` existente:
>    ```bash
>    sudo certbot certonly --webroot -w /var/www/certbot \
>      -d edge.yvyedge.com -d api.yvyedge.com \
>      --email yvyedge@gmail.com --agree-tos
>    ```
>
> 3. **nginx config**: nuevo `server` block para `api.yvyedge.com` que sirve los endpoints `/api/v1/public/*` y los assets de insights bajo `/assets/insights/*`. El bloque de `edge.yvyedge.com` queda intacto sirviendo `/api/v1/ingest/*` y `/api/v1/healthz`. Reglas concretas:
>    - `api.yvyedge.com/api/v1/public/*` → proxy_pass al FastAPI.
>    - `api.yvyedge.com/assets/insights/*` → serve estático desde `/opt/yvy-server/insights-assets/`.
>    - Headers CORS configurados con whitelist de origins (ver paso 7).
>
> 4. **Nuevo router** `src/yvy_server/presentation/api/routers/public.py` con prefijo `/api/v1/public`. Sin auth. Headers de cache: `Cache-Control: public, max-age=30, stale-while-revalidate=60`.
>
> 5. **Endpoint `GET /api/v1/public/network`:**
>    - Llama al `PublicReadRepository.get_network_status()`.
>    - Respuesta según Apéndice A.
>
> 6. **Endpoint `GET /api/v1/public/stations`:**
>    - Llama a `PublicReadRepository.get_station_summaries()`.
>    - Respuesta según Apéndice A.
>
> 7. **Endpoint `GET /api/v1/public/stations/{stationId}`:**
>    - Detalle de una estación. 404 si no existe.
>
> 8. **Schemas Pydantic** en `presentation/api/schemas_public.py`. Use case `GetPublicNetworkStatus` y `GetPublicStations` en `application/use_cases/`.
>
> 9. **CORS:** habilitar whitelist de origins:
>    ```
>    https://yvyedge.com
>    https://*.vercel.app
>    http://localhost:3000
>    ```
>    Variable de entorno `YVY_PUBLIC_CORS_ORIGINS`.
>
> 10. **Tests de integración** con DB de test poblada con seed (18 estaciones, Garupá con datos).
>
> Confirmar antes de generar:
> - Lista exacta de origins permitidos en CORS.
> - Si el FastAPI sirve los `/assets/insights/*` directamente o solo nginx. Recomiendo solo nginx (más eficiente, no toca Python).

## Objetivo

Web puede consumir el estado de red real del Core a través de endpoints públicos cacheables, sin auth, con CORS configurado, servidos desde el subdominio `api.yvyedge.com`.

## Tareas concretas

- [ ] DNS: A record `api.yvyedge.com`.
- [ ] Certificado Let's Encrypt extendido.
- [ ] nginx config con server block nuevo.
- [ ] Router `public` con prefix y cache headers.
- [ ] Endpoint `/public/network`.
- [ ] Endpoint `/public/stations`.
- [ ] Endpoint `/public/stations/{id}`.
- [ ] Schemas Pydantic y use cases en application layer.
- [ ] CORS configurado.
- [ ] Tests de integración.
- [ ] `docs/phases/PHASE-C11-public-endpoints.md`.

## Criterios de cierre

`curl https://api.yvyedge.com/api/v1/public/network` responde JSON válido en < 200ms. `curl https://api.yvyedge.com/api/v1/public/stations` responde con las 18 estaciones. CORS preflight desde `https://yvyedge.com` responde OK. Cache headers presentes. Tests verdes. Certificado SSL válido. `edge.yvyedge.com` sigue funcionando intacto.

## Log de consultas

*(vacío)*

---

# Fase C12 — Endpoints /public/metrics y /public/dashboard (provincial)

**Estado:** Pendiente
**Bloqueante de:** Fase W3

## Prompt de inicio

> Fase C12 del proyecto `yvy_server`. C10 y C11 cerradas. Ahora expongo los endpoints de métricas agregadas provinciales y series temporales.
>
> Importante: el endpoint `/public/dashboard` SOLO acepta `station=network` en V1 (decisión W15). Cualquier UUID de estación específica se ignora o rechaza — la granularidad por estación queda gated para acceso autenticado futuro.
>
> Necesito que generes:
>
> 1. **Views adicionales o funciones SQL en Postgres:**
>    - `v_metrics_24h`, `v_metrics_7d`, `v_metrics_30d` — promedios y agregados de la red por período.
>    - O alternativamente: función `get_network_metrics(period interval)` parametrizable.
>    - Función `get_network_timeseries(variable text, period interval)` que devuelve los puntos decimados según el período.
>
> 2. **Endpoint `GET /api/v1/public/metrics?period=24h|7d|30d`:**
>    - Respuesta según Apéndice A.
>
> 3. **Endpoint `GET /api/v1/public/dashboard?station=network&variable={var}&period=24h|7d|30d`:**
>    - Si `station` ausente o distinto de `network`: fuerza `network` (no error, fallback transparente). Esto sirve para defensa en profundidad — el proxy de la Web ya lo fuerza, pero el Core también.
>    - Variables soportadas: `temp | humidity | pressure | wind | dewpoint | vpd` (las derivadas se computan en C13).
>    - Decimación SQL: 24h → buckets 5min (~288 puntos); 7d → buckets 30min (~336 puntos); 30d → buckets 2h (~360 puntos).
>    - Respuesta según Apéndice A.
>    - Para variable `wind`: serie dual con `name: "avg"` y `name: "gust"`.
>
> 4. **Tests:** cada combinación variable + período devuelve estructura correcta. Decimación produce el número esperado de puntos. Si station no es 'network', se fuerza network silenciosamente.
>
> Confirmar antes de generar:
> - Si las variables derivadas se incluyen en C12 (computando inline) o se difieren a C13. Recomendado: incluir solo las nativas en C12 (T, H, P, wind) y agregar derivadas en C13.

## Objetivo

Series temporales provinciales disponibles vía API público, con decimación apropiada, listas para Recharts. Granularidad específica gated.

## Tareas concretas

- [ ] Views o funciones SQL para métricas y series.
- [ ] Endpoint `/public/metrics`.
- [ ] Endpoint `/public/dashboard` con fallback a network.
- [ ] Decimación SQL por período.
- [ ] Schemas Pydantic y use cases.
- [ ] Tests.
- [ ] `docs/phases/PHASE-C12-metrics-dashboard.md`.

## Criterios de cierre

`/public/metrics?period=24h` responde en < 300ms. `/public/dashboard?station=network&variable=temp&period=7d` responde en < 500ms. Decimación correcta. Tests verdes.

## Log de consultas

*(vacío)*

---

# Fase C13 — Variables derivadas (VPD, dewpoint)

**Estado:** Pendiente
**Bloqueante de:** Fase W3 con variables completas

## Prompt de inicio

> Fase C13 del proyecto `yvy_server`. C12 cerrada. Agrego cómputo de variables derivadas para que el dashboard provincial pueda incluirlas.
>
> Necesito que generes:
>
> 1. **Funciones SQL en Postgres:**
>    - `dewpoint(temp numeric, hum numeric) returns numeric` (fórmula de Magnus).
>    - `vpd(temp numeric, hum numeric) returns numeric` (kPa).
>    - `vpd_zone(vpd numeric) returns text` ('fungal_risk' | 'optimal' | 'water_stress').
>
> 2. **Extender `v_station_summary`** para incluir las derivadas computadas sobre `lastValues`:
>    - `lastValues.dewpoint`
>    - `lastValues.vpd`
>    - `lastValues.vpdZone`
>
> 3. **Extender `/public/dashboard`** para aceptar variables derivadas: `variable=vpd` y `variable=dewpoint`.
>
> 4. **Documentar umbrales VPD** en `docs/architecture/derived_variables.md`:
>    - < 0.4 kPa → riesgo fúngico
>    - 0.4-1.0 → óptimo
>    - > 1.0 → estrés hídrico
>
> 5. **Tests:** cálculos correctos contra valores de referencia (T=20°C, H=87% → VPD ≈ 0.30 kPa). Endpoint devuelve series con derivadas correctamente.
>
> Confirmar antes de generar:
> - Cómputo SQL-side vs aplicación-side. Recomiendo SQL-side (más eficiente y centralizado).

## Objetivo

VPD y dewpoint disponibles como variables de primera clase en el API público. Alimentan la sección de Inteligencia Aplicada de la Web.

## Tareas concretas

- [ ] Funciones SQL `dewpoint`, `vpd`, `vpd_zone`.
- [ ] Extender `v_station_summary`.
- [ ] Extender `/public/dashboard`.
- [ ] Doc de umbrales y fórmulas.
- [ ] Tests con valores de referencia.
- [ ] `docs/phases/PHASE-C13-derived.md`.

## Criterios de cierre

VPD calculado SQL-side coincide con cálculo Python a 4 decimales. Endpoint `/public/dashboard?variable=vpd` devuelve serie correcta. VPD zones reportadas correctamente en `v_station_summary`.

## Log de consultas

*(vacío)*

---

# Fase C14 — Sistema de Novedades + CLI publish-news

**Estado:** Pendiente
**Bloqueante de:** Fase W4

## Prompt de inicio

> Fase C14 del proyecto `yvy_server`. C10-C13 cerradas. Ahora implemento el sistema de Novedades editorial (no detector automático de eventos — eso se difiere según decisión C5/C7).
>
> Necesito que generes:
>
> 1. **Tabla `public.news`:**
>    ```sql
>    CREATE TABLE public.news (
>      news_id uuid PRIMARY KEY,
>      slug text NOT NULL UNIQUE,
>      title_es text NOT NULL,
>      title_pt text,
>      title_en text,
>      summary_es text NOT NULL,
>      summary_pt text,
>      summary_en text,
>      body_md text NOT NULL,
>      tags text[] DEFAULT '{}',
>      published_at timestamptz NOT NULL,
>      status text NOT NULL CHECK (status IN ('draft','published','scheduled','archived')),
>      attachment_path text,
>      created_at timestamptz NOT NULL DEFAULT now(),
>      updated_at timestamptz NOT NULL DEFAULT now()
>    );
>    CREATE INDEX idx_news_published ON public.news (published_at DESC) WHERE status='published';
>    CREATE INDEX idx_news_tags ON public.news USING GIN (tags);
>    ```
>
> 2. **CLI `yvy-server publish-news`** en `cli.py`:
>    ```bash
>    yvy-server publish-news \
>      --slug "despliegue-iea-bonpland" \
>      --title-es "Nuevo nodo: IEA Bonpland" \
>      --summary-es "Se instaló el nodo en la IEA Nº4 de Bonpland..." \
>      --body-file ./content/novedades/bonpland.md \
>      --tags "despliegue,bonpland" \
>      --published-at "2026-06-08T15:00:00Z"
>    ```
>    Argumentos según Apéndice C. Validación de slug único, parsing básico de markdown, INSERT en `public.news`. Stdout JSON con `{news_id, slug, url}`. Stderr audit log.
>
> 3. **CLI `yvy-server list-news`** con filtros `--status`, `--tag`, `--since`, `--limit`. Output tabular.
>
> 4. **Endpoint `GET /api/v1/public/news?limit=N&tag=X`:** lista novedades publicadas. Respuesta según Apéndice A.
>
> 5. **Endpoint `GET /api/v1/public/news/{slug}`:** detalle con body markdown completo. 404 si no existe o no está publicado.
>
> 6. **Repository, Value Objects, Use case** siguiendo arquitectura hexagonal.
>
> 7. **Tests:** CLI con DB real. Endpoint con DB de test poblada. Slug duplicado falla correctamente.
>
> Confirmar antes de generar:
> - Si `published_at` futuro queda en estado `scheduled` automáticamente o requiere `--status scheduled` explícito.
> - Si querés un comando `update-news` ya en C14 o se difiere.

## Objetivo

Sistema editorial de Novedades funcionando. Vos publicás desde la CLI del server y aparece en la Web vía endpoint público.

## Tareas concretas

- [ ] Migración Alembic con tabla `public.news`.
- [ ] CLI `publish-news` con validación.
- [ ] CLI `list-news`.
- [ ] Endpoint `/public/news` y `/public/news/{slug}`.
- [ ] Repository + VOs + Use cases.
- [ ] Tests.
- [ ] `docs/phases/PHASE-C14-news.md`.

## Criterios de cierre

CLI crea una novedad correctamente. Slug duplicado falla con error claro. Endpoint lista novedades publicadas correctamente. Filtro por tag funciona. Detalle por slug devuelve markdown completo. `published_at` futuro queda en `scheduled`.

## Log de consultas

*(vacío)*

---

# Fase C15 — Sistema de Insights + CLI publish-insight + assets

**Estado:** Pendiente
**Bloqueante de:** Fase W4

## Prompt de inicio

> Fase C15 del proyecto `yvy_server`. C14 cerrada. Implemento el sistema de Insights editorial (artículos más extensos con gráficos opcionales y attachments), siguiendo el mismo patrón que Novedades.
>
> Necesito que generes:
>
> 1. **Tabla `public.insight`:**
>    ```sql
>    CREATE TABLE public.insight (
>      insight_id uuid PRIMARY KEY,
>      slug text NOT NULL UNIQUE,
>      title_es text NOT NULL,
>      title_pt text,
>      title_en text,
>      summary_es text NOT NULL,
>      summary_pt text,
>      summary_en text,
>      body_md text NOT NULL,
>      chart_config jsonb,
>      attachment_path text,
>      tags text[] DEFAULT '{}',
>      published_at timestamptz NOT NULL,
>      status text NOT NULL CHECK (status IN ('draft','published','scheduled','archived')),
>      created_at timestamptz NOT NULL DEFAULT now(),
>      updated_at timestamptz NOT NULL DEFAULT now()
>    );
>    ```
>
> 2. **CLI `yvy-server publish-insight`** según mini manual (Apéndice C). Soporta `--chart-file` (valida JSON contra schema) y `--attachment-file` (sube el archivo al filesystem local).
>
> 3. **Manejo de attachments locales:**
>    - Directorio: `/opt/yvy-server/insights-assets/<slug>/<filename>`.
>    - Permisos: `yvyadmin:yvyadmin`, 644.
>    - URL pública: `https://api.yvyedge.com/assets/insights/<slug>/<filename>` (servido por nginx, configurado en C11).
>    - El CLI copia el archivo al directorio, computa hash SHA256, guarda el path relativo en `attachment_path`.
>
> 4. **Schema JSON de chart_config** en `domain/insights/chart_schema.py`:
>    - Soporta: `line`, `bar`, `area`, `composed`.
>    - Estructura: `{type, data: [], xAxis: {dataKey}, series: [{dataKey, name, color, type}]}`.
>    - Validación con Pydantic.
>
> 5. **CLI `yvy-server list-insights`** análogo a list-news.
>
> 6. **Endpoint `GET /api/v1/public/insights?limit=N&tag=X`** y `GET /api/v1/public/insights/{slug}`: respuesta según Apéndice A.
>
> 7. **Repository, Value Objects, Use case** según arquitectura hexagonal.
>
> 8. **Tests:** CLI con attachments reales. Endpoint con chart_config válido. Acceso a attachment vía URL pública funciona.
>
> Confirmar antes de generar:
> - Si chart_config soporta solo Recharts-compatible o también otros formatos.
> - Si querés rate limiting en attachments (probablemente sí — un PDF de 50MB descargándose en bucle puede saturar nginx).

## Objetivo

Sistema editorial de Insights funcionando con CLI propio. Soporte de gráficos opcionales (chart_config JSON) y descargas opcionales (attachments locales servidos por nginx).

## Tareas concretas

- [ ] Migración Alembic con tabla `public.insight`.
- [ ] CLI `publish-insight` con validación de chart_config y manejo de attachment.
- [ ] CLI `list-insights`.
- [ ] Directorio `/opt/yvy-server/insights-assets/` con permisos correctos.
- [ ] Schema JSON de chart_config + validación Pydantic.
- [ ] Endpoint `/public/insights` y `/public/insights/{slug}`.
- [ ] Repository + VOs + Use cases.
- [ ] Tests.
- [ ] `docs/phases/PHASE-C15-insights.md`.

## Criterios de cierre

CLI publica un insight con chart_config válido y attachment PDF correctamente. Endpoint sirve el insight con su chart_config y attachment_url. Acceso a `https://api.yvyedge.com/assets/insights/<slug>/<file>` devuelve el archivo. Tests verdes.

## Log de consultas

*(vacío)*

---

# Fase C16 — Cache layer en endpoints públicos

**Estado:** Pendiente
**Bloqueante de:** Launch (recomendado pero no obligatorio)

## Prompt de inicio

> Fase C16 del proyecto `yvy_server`. C10-C15 cerradas. Última fase del track de lectura pública: cache layer para reducir latencia y carga sobre Postgres.
>
> Necesito que generes:
>
> 1. **Decisión: in-memory (cachetools) vs Redis.** Para la carga proyectada de la Web V1 sobre 18 nodos, in-memory es suficiente. Recomendación: in-memory en C16. Migrar a Redis si fase futura lo requiere.
>
> 2. **Decorador `@public_cache(ttl)` reutilizable** en `presentation/api/cache.py`:
>    - TTL configurable por endpoint:
>      - `/public/network` → 30s
>      - `/public/stations` → 30s
>      - `/public/metrics` → 60s
>      - `/public/dashboard` → 60s (cache keyed por variable+period)
>      - `/public/news` → 60s
>      - `/public/insights` → 60s
>      - `/public/news/{slug}` → 300s
>      - `/public/insights/{slug}` → 300s
>
> 3. **Métricas de cache:** logs structured con `extra={cache_key, hit, age}`.
>
> 4. **Endpoint admin `/api/v1/admin/cache_stats`** con bearer token: hits/misses, keys actuales, tamaño en memoria.
>
> 5. **Invalidación manual:** CLI `yvy-server cache-clear [--prefix X]` para invalidar cache (ej después de publicar una novedad importante).
>
> 6. **Tests:** cache hit reduce latencia 10x. TTL respetado. Invalidación manual funciona.

## Objetivo

Endpoints públicos respondiendo en < 50ms en cache hit. Postgres protegido de carga repetida.

## Tareas concretas

- [ ] Decorador `@public_cache(ttl)`.
- [ ] Aplicación a los 8 endpoints públicos.
- [ ] Endpoint admin para cache stats.
- [ ] CLI cache-clear.
- [ ] Tests.
- [ ] `docs/phases/PHASE-C16-cache.md`.

## Criterios de cierre

Latencia de cache hit < 50ms (vs 200-500ms sin cache). Cache stats reportables. Invalidación manual funciona. Tests verdes.

## Log de consultas

*(vacío)*

---

# Apéndice A — Contrato de datos (DATA-CONTRACT.md)

Este es el contrato que la Web define y el Core implementa. Se copia a `web_yvyedge/docs/guides/DATA-CONTRACT.md` en Fase W0 y se mantiene como fuente de verdad.

## Versionado

`SCHEMA_VERSION = "1.0"` incluido en cada respuesta como header `X-Schema-Version`. Cambios incompatibles incrementan la versión.

## Endpoints

### GET https://api.yvyedge.com/api/v1/public/network

Estado agregado de la red.

```json
{
  "totalNodes": 18,
  "activeNodes": 1,
  "offlineNodes": 0,
  "pendingNodes": 17,
  "totalRecords": 1999,
  "localitiesCount": 1,
  "lastSyncSeconds": 48,
  "uptime30d": 0.997,
  "generatedAt": "2026-06-08T17:30:00Z"
}
```

Cache: `public, max-age=30, stale-while-revalidate=60`.

### GET /api/v1/public/stations

Listado de estaciones con su último valor (T/H/P solamente — sin granularidad histórica).

```json
{
  "stations": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "code": "IEA-07",
      "name": "IEA Nº7 Garupá",
      "locality": "Garupá",
      "province": "Misiones",
      "coordinates": {"lat": -27.4869, "lng": -55.8276},
      "status": "online",
      "lastSyncAt": "2026-06-08T17:29:12Z",
      "lastValues": {
        "temp": 17.5,
        "humidity": 96,
        "pressure": 1004.1,
        "windSpeed": 1.4,
        "windGust": 1.7,
        "windDirection": 157.5,
        "dewpoint": 16.9,
        "vpd": 0.08,
        "vpdZone": "fungal_risk",
        "timestamp": "2026-06-08T17:29:12Z"
      }
    }
  ],
  "generatedAt": "2026-06-08T17:30:00Z"
}
```

Status enum: `online | sync_pending | offline | pending`. `online` < 15min, `sync_pending` 15min-2h, `offline` > 2h, `pending` nunca sincronizó.

### GET /api/v1/public/stations/{stationId}

Detalle de una estación. Mismo shape que un item de `/stations`. 404 si no existe.

### GET /api/v1/public/metrics?period={24h|7d|30d}

Métricas agregadas provinciales para el período.

```json
{
  "period": "24h",
  "network": {
    "avgTemp": 17.0,
    "avgHumidity": 87,
    "avgPressure": 1004,
    "avgWindSpeed": 1.8,
    "dominantWindDirection": "SSE",
    "totalRainfall": 34.2,
    "avgVpd": 0.28,
    "avgDewpoint": 14.76
  },
  "trends": {
    "tempVsPrevious": 1.2,
    "pressureDelta3h": -2.0
  },
  "generatedAt": "2026-06-08T17:30:00Z"
}
```

### GET /api/v1/public/dashboard?station=network&variable={var}&period={24h|7d|30d}

Series temporales provinciales. **`station` solo acepta `network`** en V1 — cualquier otro valor se ignora silenciosamente y se devuelve red completa.

Variables soportadas: `temp | humidity | pressure | wind | vpd | dewpoint`.

```json
{
  "station": "network",
  "variable": "temp",
  "period": "7d",
  "unit": "°C",
  "series": [
    {
      "name": "value",
      "points": [
        {"t": "2026-06-01T00:00:00Z", "v": 17.5},
        {"t": "2026-06-01T00:30:00Z", "v": 17.4}
      ]
    }
  ],
  "generatedAt": "2026-06-08T17:30:00Z"
}
```

Para variable `wind`: series dual con `name: "avg"` y `name: "gust"`.

### GET /api/v1/public/news?limit={N}&tag={X}

Lista de novedades publicadas.

```json
{
  "news": [
    {
      "id": "uuid",
      "slug": "despliegue-iea-bonpland",
      "title": "Nuevo nodo: IEA Bonpland",
      "summary": "Se instaló el nodo en la IEA Nº4 de Bonpland...",
      "tags": ["despliegue","bonpland"],
      "publishedAt": "2026-06-08T15:00:00Z",
      "locale": "es",
      "availableLocales": ["es"]
    }
  ],
  "total": 12,
  "generatedAt": "2026-06-08T17:30:00Z"
}
```

`title` y `summary` se devuelven en el idioma solicitado vía `Accept-Language` o fallback a ES. `availableLocales` indica idiomas disponibles para que la Web muestre badge "Sólo en español" si aplica.

### GET /api/v1/public/news/{slug}

Detalle de una novedad con body markdown completo.

```json
{
  "id": "uuid",
  "slug": "despliegue-iea-bonpland",
  "title": "Nuevo nodo: IEA Bonpland",
  "summary": "...",
  "body": "# Markdown completo...\n\nTexto del artículo.",
  "tags": ["despliegue","bonpland"],
  "publishedAt": "2026-06-08T15:00:00Z",
  "attachmentUrl": null,
  "locale": "es",
  "availableLocales": ["es"]
}
```

### GET /api/v1/public/insights?limit={N}&tag={X}

Lista de insights editoriales.

```json
{
  "insights": [
    {
      "id": "uuid",
      "slug": "riesgo-fungico-mayo-2026",
      "title": "Análisis de riesgo fúngico — Mayo 2026",
      "summary": "127 días con VPD bajo en la red provincial",
      "tags": ["agroclima","riesgo-fungico"],
      "publishedAt": "2026-06-08T15:00:00Z",
      "hasChart": true,
      "hasAttachment": false,
      "locale": "es",
      "availableLocales": ["es"]
    }
  ],
  "total": 8,
  "generatedAt": "2026-06-08T17:30:00Z"
}
```

### GET /api/v1/public/insights/{slug}

Detalle de un insight con body, chart opcional, attachment opcional.

```json
{
  "id": "uuid",
  "slug": "riesgo-fungico-mayo-2026",
  "title": "Análisis de riesgo fúngico — Mayo 2026",
  "summary": "127 días con VPD bajo...",
  "body": "# Markdown completo del análisis...",
  "chartConfig": {
    "type": "line",
    "data": [],
    "xAxis": {"dataKey": "date"},
    "series": [
      {"dataKey": "vpd", "name": "VPD provincial", "color": "#C9A227", "type": "line"}
    ]
  },
  "attachmentUrl": null,
  "tags": ["agroclima","riesgo-fungico"],
  "publishedAt": "2026-06-08T15:00:00Z",
  "locale": "es",
  "availableLocales": ["es"]
}
```

## Manejo de errores

Respuesta 5xx: Web reintenta 3 veces con backoff exponencial. Si todos fallan, muestra último valor en cache con badge "stale".

Respuesta 4xx: Web logea el error pero no reintenta. Empty state apropiado.

Headers comunes:
- `X-Schema-Version: 1.0`
- `Cache-Control: public, max-age=N, stale-while-revalidate=N`
- `Access-Control-Allow-Origin: https://yvyedge.com` (whitelist)

---

# Apéndice B — Variables de entorno

## web_yvyedge

```bash
NEXT_PUBLIC_SITE_URL=https://yvyedge.com
YVY_CORE_URL=https://api.yvyedge.com
YVY_CORE_API_VERSION=v1
NEXT_PUBLIC_USE_MOCK=false
NEXT_PUBLIC_PLAUSIBLE_DOMAIN=yvyedge.com
NEXT_PUBLIC_CONTACT_EMAIL=yvyedge@gmail.com
```

## yvy_server (adiciones para track público)

```bash
YVY_PUBLIC_CORS_ORIGINS=https://yvyedge.com,https://*.vercel.app,http://localhost:3000
YVY_PUBLIC_CACHE_TTL_DEFAULT=30
YVY_PUBLIC_CACHE_TTL_DASHBOARD=60
YVY_PUBLIC_CACHE_TTL_CONTENT=60
YVY_PUBLIC_CACHE_TTL_DETAIL=300
YVY_INSIGHTS_ASSETS_DIR=/opt/yvy-server/insights-assets
```

---

# Apéndice C — Mini manual del CLI de Novedades e Insights

El CLI extiende `yvy-server` con cuatro subcomandos: `publish-news`, `list-news`, `publish-insight`, `list-insights`. Vive en `src/yvy_server/cli.py` con el mismo patrón del actual `create-station`.

## Publicar una novedad

```bash
yvy-server publish-news \
  --slug "despliegue-iea-bonpland" \
  --title-es "Nuevo nodo: IEA Nº4 Bonpland" \
  --title-pt "Novo nó: IEA Nº4 Bonpland" \
  --title-en "New node: IEA Nº4 Bonpland" \
  --summary-es "Se instaló el nodo en la IEA Nº4 de Bonpland completando la cobertura del centro de la provincia" \
  --body-file ./content/novedades/bonpland.md \
  --tags "despliegue,bonpland,cobertura" \
  --published-at "2026-06-08T15:00:00Z"
```

**Argumentos requeridos:** `--slug`, `--title-es`, `--summary-es`, `--body-file`.

**Argumentos opcionales:** `--title-pt`, `--title-en`, `--summary-pt`, `--summary-en`, `--tags` (CSV), `--published-at` (default `now()`), `--status` (`draft|published|scheduled|archived`, default `published`), `--attachment-file`.

Comportamiento: valida markdown, INSERT en `public.news`. Si slug duplicado, error. Stdout JSON con `{news_id, slug, url}`. Stderr audit log.

## Publicar un insight

```bash
yvy-server publish-insight \
  --slug "riesgo-fungico-mayo-2026" \
  --title-es "Análisis de riesgo fúngico — Mayo 2026" \
  --title-pt "Análise de risco fúngico — Maio 2026" \
  --title-en "Fungal risk analysis — May 2026" \
  --summary-es "127 días con VPD bajo en la red provincial" \
  --body-file ./content/insights/riesgo-fungico-mayo.md \
  --chart-file ./content/charts/vpd-distribution.json \
  --attachment-file ./content/attachments/informe-mayo-2026.pdf \
  --tags "agroclima,riesgo-fungico,mayo-2026" \
  --published-at "2026-06-08T15:00:00Z"
```

**Argumentos requeridos:** `--slug`, `--title-es`, `--summary-es`, `--body-file`.

**Argumentos opcionales:** mismos que `publish-news` más `--chart-file` (JSON con configuración Recharts) y `--attachment-file` (PDF/imagen que se sube al filesystem local del server).

**Comportamiento de attachment:**
- Se copia a `/opt/yvy-server/insights-assets/<slug>/<filename>`.
- Permisos `yvyadmin:yvyadmin` 644.
- Se computa SHA256 y se guarda en `attachment_path`.
- URL pública resultante: `https://api.yvyedge.com/assets/insights/<slug>/<filename>`.

**Comportamiento de chart_config:** valida estructura JSON contra schema definido en `domain/insights/chart_schema.py`. Tipos soportados: `line`, `bar`, `area`, `composed`.

## Listar

```bash
yvy-server list-news --status published --limit 20
yvy-server list-news --tag despliegue
yvy-server list-insights --since 2026-05-01
```

Output tabular: `slug | title_es | status | published_at | tags`.

## Workflow operativo recomendado

Mantener un repo o carpeta `content/` versionada con los markdowns y JSONs de gráficos. Estructura sugerida:

```
content/
├── novedades/
│   ├── despliegue-iea-bonpland.md
│   └── ...
├── insights/
│   ├── riesgo-fungico-mayo-2026.md
│   └── ...
├── charts/
│   ├── vpd-distribution.json
│   └── ...
└── attachments/
    ├── informe-mayo-2026.pdf
    └── ...
```

Cuando querés publicar: `yvy-server publish-insight --body-file content/insights/X.md --chart-file content/charts/Y.json`. El CLI persiste en Postgres; la Web los consume vía endpoint público con cache. Si necesitás invalidar el cache después de publicar: `yvy-server cache-clear --prefix /public/insights` (Fase C16).

---

# Notas finales

## Sobre cambios de plan

Este documento es vivo. Si durante la implementación se detecta que una fase requiere dividirse, fusionarse o reordenarse, está bien. Documentar el cambio acá con fecha, actualizar dependencias cruzadas Web↔Core, comunicar el cambio en el resumen de la fase activa.

## Sobre el log de consultas

Antes de cerrar cada fase, revisar las "Consultas abiertas" de fases anteriores. Algunas se resuelven con la implementación; otras necesitan decisión explícita.

## Cómo continuar entre sesiones

Al cerrar una sesión: marcar la tarea actual completada en la fase activa; actualizar "Notas de implementación" con detalles relevantes para retomar; agregar consultas pendientes al log; hacer commit del plan junto con el código.

Al abrir una nueva sesión: identificar la fase activa (Web o Core); revisar el peer review con Claude en chat antes de pasar a Claude Code; cerrar decisiones abiertas si las hay; copiar el "Prompt de inicio" actualizado a Claude Code local; si la fase ya está parcialmente avanzada, especificar qué tareas están hechas.

---

*Documento v2.0 generado el 08-Jun-2026 · Mantener actualizado.*
