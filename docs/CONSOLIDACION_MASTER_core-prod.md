# Consolidación `core-prod-actual` → `master` (YVY Core)

**Fecha:** 2026-07-01 · **Ejecutado local, pendiente push a `origin/master`.**

Registro del proceso de consolidar el código **estable de producción**
(`core-prod-actual`) sobre `master`, dejando `master` como fuente de verdad
antes de landear `plan_fases_api`.

---

## 1. Punto de partida (diagnóstico)

Ramas divergentes desde `46dcee9`:

| Rama | Aporte propio |
|---|---|
| `core-prod-actual` (prod estable) | Dockerfile de prod (poetry export + pip) · fix `edge.py` (500 en `/edge/updates`) |
| `master` | CI GitHub Actions · imagen postgres **PostGIS+pgvector** (feat/m0 infra) · merges |
| `plan_fases_api` (a landear luego) | **C10 + C11 + M0** (migración de bronze) + fixes CI + docs |

Hallazgos clave:
- Los 2 commits de prod tocan **solo** `edge.py`+`CHANGELOG` y `Dockerfile`. **No** tocan
  `docker-compose`/`postgres/Dockerfile`/`ci.yml` → consolidar **no** arrastra el
  postgres plano ni recorta el CI. master **conserva** extensiones + CI.
- La migración M0 (`003`) de `plan_fases_api` **hard-requiere `pgvector`** → master
  DEBE mantener la imagen de postgres con extensiones (sustrato de M0). Se descartó
  "alinear master a postgres plano".
- `plan_fases_api` trae `edge.py` con el **bug** `filename` → al landear hay que
  **re-aplicar** el fix.

## 2. Decisiones

1. **Método:** cherry-pick selectivo de los 2 commits de prod (no merge completo).
2. **Poetry — Opción A:** poetry queda **solo en el stage builder** para *exportar*
   dependencias; se elimina todo `poetry install`. Runtime sin poetry.
3. **CI y postgres de master:** se conservan (no se adopta el recorte de prod).

## 3. Acciones ejecutadas (local, sobre `master` = `origin/master`)

```
git stash            # working tree tenía cambios redundantes con plan_fases_api
git checkout master && git reset --hard origin/master
git cherry-pick 736e707   # fix edge.py (filename -> package_filename) + CHANGELOG
git cherry-pick 5badbeb   # Dockerfile prod (poetry export + pip)
# + edición Dockerfile (Opción A)
git commit            # chore(docker): quitar poetry install del build
```

Commits resultantes sobre `origin/master`:
- `aabee2b` fix(edge): filename → package_filename
- `68fbe0e` chore(build): Dockerfile prod (poetry export + pip)
- `9613720` chore(docker): Opción A — quitar `poetry install`

**Dockerfile final (builder):** deps vía `poetry export --only main` → `pip install -r`;
paquete raíz vía `pip install --no-deps .` **después** de `embed_version.py` (para que
`_version.py` quede embebido; si no, la versión caía a `"unknown"`). Cero `poetry install`.

## 4. Estado / pendientes

- **Tests:** el cambio Python es solo el rename de 2 claves de log en `edge.py`
  (ya probado en prod). El resto lo valida el **CI de master** al pushear.
- **Ruff pre-existente:** `master` tiene 2 errores **B904** en `edge.py` (líneas
  104, 162 — `raise ... from`), **previos a esta consolidación**. Los arreglan los
  commits `fix(ci)` de `plan_fases_api`. Decidir: dejarlos (se corrigen al landear
  plan_fases_api) o corregir ahora para dejar el CI en verde.
- **Working tree stasheado:** redundante con `plan_fases_api` (ya en `origin`); se
  puede descartar tras confirmar.

## 5. Próximo paso
Push de `master` y luego el rollout de `plan_fases_api` en 3 fases (M0 → C10 → C11).
Ver `PLAN_APLICACION_SERVIDOR.md`.
