# Akasha Ingestion — Agent Guide

Self-hosted satellite ingestion, metadata catalog, object lake, processing, and serving backend.
Currently **Phase 1: core platform foundation** (FastAPI + Celery + Postgres/PostGIS/pgSTAC + MinIO).
See [README.md](README.md) and [docs/akasha-ingestion-plan.md](docs/akasha-ingestion-plan.md) for the
what/why, and [docs/architecture-technical-stack.md](docs/architecture-technical-stack.md) for the full stack.

## Required working branch

- Always perform repository work on the `development` branch. Before editing, verify the current
  branch and switch to `development` if necessary without discarding unrelated local changes.
- Do not commit implementation work directly to `main`. Promote validated `development` changes
  to `main` through the repository's normal pull-request and deployment workflow.

## Multi-root workspace context

This repository is commonly opened in the saved VS Code workspace
[`../akasha-workspace.code-workspace`](../akasha-workspace.code-workspace) alongside
[`../akasha-project`](../akasha-project), the Akasha product application.

When an agent is asked to compare, reuse, or align patterns across Akasha projects:

- First read this file and [`../akasha-project/AGENTS.md`](../akasha-project/AGENTS.md); each repo
  has different ownership boundaries and runtime assumptions.
- Treat this repo as the **standalone ingestion/catalog/processing backend** reference: FastAPI,
  Celery, Postgres/PostGIS/pgSTAC, MinIO, provider adapters, object storage, processing, jobs, and
  operational runbooks.
- Treat `../akasha-project` as the **application/product** reference: BFF + frontend + farm-management
  workflows, UI patterns, field/season/operation concepts, and ResourceSat product integration.
- Do not copy code blindly between roots. Prefer copying concepts and adapting them to this repo's
  conventions: `AKASHA_` settings, `APIResponse[T]`, manual app-state dependency wiring,
  duck-typed paired implementations, raw SQL runtime queries, and in-memory/external backend parity.
- Keep edits scoped to the requested root unless the user explicitly asks for a cross-repository
  change. If touching both roots, validate each root with its own commands and mention both in the
  final summary.

## Current self-hosted deployment topology (provider-whitelisted staging)

As of the UI↔pipeline integration work, Akasha is moving to a **two-VM split**:

- **`akasha-staging`** — this is the provider-whitelisted VM. Bhoonidhi/ISRO provider access is
  available here, so this standalone ingestion platform must run here (API, workers, Postgres/pgSTAC,
  MinIO, Redis, TiTiler, scheduler/dispatchers). Current observed size: Azure `Standard_D4s_v4`
  (4 vCPU, ~16 GiB RAM) with a 256 GiB OS disk and a 512 GiB `/srv/akasha` data disk. This is
  acceptable for bounded MVP ingestion/backfill/composite jobs, but scale up before concurrent heavy
  processing or larger AOIs.
- **`akasha-control`** — Coolify control/public-app VM. The product app (`../akasha-project`) should
  move there and call this ingestion service server-to-server. Current observed size: Azure
  `Standard_D4s_v4` (4 vCPU, ~16 GiB RAM) with a 64 GiB OS disk and 256 GiB `/data`; acceptable for
  Coolify + the product app MVP when bulk raster data stays on `akasha-staging`.

Connectivity rules for this split:

- Ingestion remains a private/server-to-server service. The browser must never call this API, MinIO,
  Postgres, pgSTAC, or TiTiler directly.
- The product app BFF on `akasha-control` calls this API on `akasha-staging` using `INGESTION_API_URL`
  and `INGESTION_API_KEY`. Prefer private networking/VNet peering/WireGuard or an IP-allowlisted
  HTTPS endpoint.
- `AKASHA_PUBLIC_BASE_URL` in this repo must be set to the exact URL prefix used by the app BFF as
  `INGESTION_API_URL`. The app BFF allowlists signed ingestion URLs by prefix before proxying stats,
  tiles, or field-clipped overlays, so the scheme/host/port must match exactly and should not include
  a trailing slash.
- Keep all provider downloads, raw rasters, derived COGs, composites, scratch files, and validation
  data on `/srv/akasha` on `akasha-staging`; never put bulk raster data under `/`, `/tmp`,
  `/var/tmp`, `/var/lib/docker`, or `/data/coolify`.

## Product-app integration contract

The product app consumes this pipeline through the API envelope only; preserve these contracts when
changing analytics code:

- `POST /api/v1/analytics/field-index` receives the app field polygon (`geometry`, `crs=EPSG:4326`),
  selects a precomputed derived index COG, computes field statistics, stores a row in
  `akasha.field_queries`, and returns `FieldIndexAvailableResponse`/`FieldIndexUnavailableResponse`.
- For the field analytics map, the app uses the signed `overlayUrl`, not full-scene XYZ tiles. The
  overlay route `GET /api/v1/analytics/field-index/{query_id}/overlay.png` must render a
  **field-clipped PNG** from the stored query geometry: transparent outside the polygon, with
  `X-Akasha-Overlay-Corners` for the MapLibre image source.
- Signed `statsUrl`/`overlayUrl` values must use `AKASHA_PUBLIC_BASE_URL` as their prefix and must
  carry valid HMAC query parameters (`op`, `exp`, `kid`, `sig`). These signed routes are the only
  analytics routes that may be consumed without `X-API-Key`; all unsigned API routes still require
  the API key header.
- `tileUrl` and `/tiles/{layer_id}/{z}/{x}/{y}.png` may remain for future/regional display or tests,
  but they must not be required for the field-clipped NDVI heatmap path.
- Runtime queries are raw SQL. If a route needs stored geometry, retrieve it explicitly (for example,
  `ST_AsGeoJSON(field_geometry)`) rather than stubbing geometry to `{}`.
- Do not add browser-facing contracts that expose this ingestion API, MinIO, Postgres/pgSTAC,
  TiTiler, object keys, `s3://` URLs, provider hrefs, or signed provider URLs. The product app BFF is
  the only supported consumer for field analytics in the two-VM topology.

## Build, test, lint

Python 3.11+. Editable install with dev extras; a `.venv` at the repo root is expected.

```bash
python -m pip install -e ".[dev]"   # install
python -m pytest                     # tests — in-memory, no DB or services required
ruff check .                         # lint — line-length 100, rules E/F/I/UP/B/SIM (B008 ignored)
```

Run the API locally (requires API key hashes — see Security):

```bash
uvicorn akasha.api.app:app --reload
```

Full stack (Postgres, Redis, MinIO, TiTiler, workers, observability):

```bash
docker compose -f deploy/docker-compose.yml -f deploy/compose.dev.yml --profile tools up
```

CI runs install → lint → tests → compose validation → migration + seed dry-run → image builds; see
[.github/workflows/ci.yml](.github/workflows/ci.yml). Windows/PowerShell command variants (`.\.venv\Scripts\python`,
`$env:VAR = "..."`) are in [README.md](README.md).

## Architecture

Layered FastAPI + Celery app under [src/akasha/](src/akasha/):

| Module | Role |
|--------|------|
| [config.py](src/akasha/config.py) | `Settings` (pydantic-settings, `AKASHA_` env prefix, cached via `get_settings()`) |
| [runtime.py](src/akasha/runtime.py) | Factory/DI wiring; switches `RuntimeBackend.MEMORY` vs `EXTERNAL` |
| [api/app.py](src/akasha/api/app.py) | `create_app()` factory; routes, auth, exception handlers |
| [services/ingestion.py](src/akasha/services/ingestion.py) | Ingestion orchestration (`MockIngestionService`) |
| [jobs/](src/akasha/jobs/) | Celery app + tasks; job store ([store.py](src/akasha/jobs/store.py) in-memory, [sql_store.py](src/akasha/jobs/sql_store.py) Postgres); [idempotency.py](src/akasha/jobs/idempotency.py) |
| [providers/mock.py](src/akasha/providers/mock.py) | Data-source adapters |
| [storage/object_store.py](src/akasha/storage/object_store.py) | Object lake (`InMemoryObjectStore` / `MinIOObjectStore`) |
| [catalog/](src/akasha/catalog/) | Satellite source registry + DB seeder |
| [db/](src/akasha/db/) | SQLAlchemy engine + `DeclarativeBase` (schema `akasha`) |
| [security.py](src/akasha/security.py) / [logging.py](src/akasha/logging.py) / [schemas.py](src/akasha/schemas.py) | API-key auth, log redaction, Pydantic models |

Detailed design spec: [docs/phase-1-core-platform-foundation-implementation-plan.md](docs/phase-1-core-platform-foundation-implementation-plan.md).

## Conventions (project-specific)

- **Two runtime backends.** Everything switches on `RuntimeBackend`: `MEMORY` (tests/dev — in-process stores +
  Celery `task_always_eager`) vs `EXTERNAL` (Postgres + MinIO + Redis). Wire new components through the factory
  functions in [runtime.py](src/akasha/runtime.py).
- **Duck-typed interfaces, no ABCs.** Paired implementations (e.g. `InMemoryJobStore` / `PostgresJobStore`) share
  method signatures but no common base class. Match the existing signatures exactly when adding an implementation.
- **Raw SQL, not ORM.** `DeclarativeBase` exists only for Alembic metadata; runtime queries use `sqlalchemy.text()`
  and read results via `row.column_name`.
- **Dependency injection is manual.** `create_app()` composes stores/services onto `app.state.*`; route handlers read
  `request.app.state.*`. Tests override by passing `job_store` / `object_store` kwargs — do not add a DI framework.
- **`from __future__ import annotations`** at the top of every module.
- **Module-level singletons** `app = create_app()` and `celery_app = create_celery_app()` are import targets for
  uvicorn/Celery — keep them, and keep the trailing task import in the Celery module for autodiscovery.
- **Naming:** `{Name}IngestionService`, `{Backend}JobStore`, `{Name}Provider`; DB constraints prefixed
  `pk_` / `uq_` / `fk_` / `ix_` / `ck_`.
- **API responses** are always wrapped in the `APIResponse[T]` envelope (`success` / `data` / `error`) from
  [schemas.py](src/akasha/schemas.py).
- **Object layout:** `raw/{provider}/{source_id}/{product_id}/original.mock` with a `checksum.sha256` sidecar — don't
  change without updating retrieval logic.
- **Idempotency:** sync jobs are deduped by a deterministic SHA-256 key ([idempotency.py](src/akasha/jobs/idempotency.py));
  failed jobs may be re-submitted with the same key.

## Security invariants

- Never log or commit plaintext secrets. Sensitive settings use `SecretStr`; a `RedactionFilter`
  ([logging.py](src/akasha/logging.py)) scrubs logs. Secret workflow is SOPS + age —
  see [docs/phase-1/secret-management.md](docs/phase-1/secret-management.md).
- API keys are stored **hashed** (SHA-256) in `AKASHA_API_KEY_HASHES` as `name:hash` pairs, verified with
  `hmac.compare_digest` via the `X-API-Key` header. All routes except `/health` require it. Generate a hash with
  `python -c "from akasha.security import hash_api_key; print(hash_api_key('...'))"`.
- Respect schema ownership: the `pgstac` schema belongs to pgSTAC; app tables live in `akasha`.
  See [docs/phase-1/schema-notes.md](docs/phase-1/schema-notes.md).

## Database & migrations

Postgres 16 + PostGIS 3.4 + pgSTAC v0.7.10. **Order matters:** apply the pgSTAC schema first, then
`alembic upgrade head`, then `python -m akasha.catalog.seed_db`. Migrations live in
[migrations/versions/](migrations/versions/). Operational runbooks:
[deployment](docs/phase-1/deployment-runbook.md), [backup/restore](docs/phase-1/backup-restore-runbook.md),
[smoke tests](docs/phase-1/operations-smoke-test-checklist.md).
