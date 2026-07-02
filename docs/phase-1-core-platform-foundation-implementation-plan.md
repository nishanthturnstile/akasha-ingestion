# Phase 1 Core Platform Foundation Implementation Plan

## 1. Purpose

Phase 1 builds the self-hosted Akasha Ingestion platform foundation before any satellite-specific vertical slice is implemented. The goal is to make the runtime, schemas, storage contracts, job orchestration, API shell, secrets, observability, backups, and deployment flow reliable enough for Phase 2 Sentinel-2 and Phase 3 ResourceSat work.

This plan was synthesized from:

- `docs/akasha-ingestion-plan.md`
- `docs/architecture-technical-stack.md`
- `docs/implementation-roadmap.md`
- `docs/phase-0/*`
- Independent review feedback from Claude Opus 4.8 and GPT-5.5

The model review feedback is incorporated in this final plan, especially around Phase 0 handoff evidence, mock-provider validation, schema scope control, pgSTAC ownership, storage and port checks, secret runtime flow, job durability, and bounded restore criteria.

## 2. Phase 1 scope

### In scope

- Python/FastAPI API foundation.
- Celery worker and scheduler foundation.
- Docker Compose runtime for Azure dev and future on-prem parity.
- Ansible bootstrap and VM setup automation based on Phase 0 VM notes.
- PostgreSQL/PostGIS plus pgSTAC catalog foundation.
- Alembic-managed Akasha operational schema.
- MinIO object lake zones and private bucket policy baseline.
- Provider-agnostic source registry, scene, order, asset, job, audit, and execution-policy contracts.
- Mock-provider ingestion path that validates Phase 1 contracts without starting real provider work.
- MVP API-key authentication foundation.
- Internal TiTiler or TiTiler-PgSTAC smoke integration.
- Structured logs, metrics, dashboards, alerts, and worker visibility.
- PostgreSQL, MinIO/config/secrets backup approach and restore drill.
- CI/image/release baseline with pinned runtime versions.
- Phase 1 runbooks and handoff documentation.

### Out of scope

- Real Sentinel-2 search/download/processing.
- Real Bhoonidhi/ResourceSat search/order/download/processing.
- Landsat, Earthdata, SAR, commercial, or vendor adapters.
- NDVI or other real index generation.
- ResourceSat atmospheric correction or cloud masking.
- Public production exposure.
- End-user UI.
- Production high availability.

## 3. Entry gate and Phase 0 handoff

The user has stated that Phase 0 is complete. The repository documents still show Phase 0 provider validation, sample downloads, product characteristics, ResourceSat atmospheric-correction feasibility, and storage/compute sizing as pending or blocked. Treat Phase 0 completion as an operator assertion, but do not start Phase 1 execution until the handoff evidence below is reconciled in the documentation or explicitly accepted as a known gap.

### Required handoff evidence

| Area | Required evidence before Phase 1 execution |
| --- | --- |
| Phase 0 status | `docs/phase-0/phase0-status.md` updated, or a documented override explaining which Phase 0 outputs remain unavailable. |
| AOI inputs | Bangalore 60 km AOI, bbox envelope, demo window, clear-season window, and three sample fields remain authoritative. |
| Provider status | Credential and provider validation status captured without storing secrets in the repo. |
| VM access | `akasha-staging` SSH, Docker, Docker Compose, and `/srv/akasha` data layout confirmed. |
| Storage | Decision made to either expand storage/add scratch disk or accept the current `/srv/akasha` capacity as Phase 1-only. |
| Scratch | Dedicated `/scratch/akasha` mount added or `/srv/akasha/scratch` explicitly accepted for Phase 1 tests. |
| Ports and ingress | Existing public listeners on `80`, `443`, `8080`, and `8888` audited; Caddy/Traefik port ownership decided. |
| Azure network | Azure NSG/firewall rules captured; only intended public ingress remains open. |
| Secrets | SOPS age key custody and runtime decrypt flow decided. |
| Backup target | Backup repository target selected for PostgreSQL and config/secrets; MinIO backup approach selected for the bounded Phase 1 dataset. |
| CI and registry | CI platform and image registry decision recorded. |
| pgSTAC | pgSTAC adopted unless a hard blocker is documented before schema freeze. |

## 4. Core decisions

| Decision | Phase 1 default |
| --- | --- |
| Runtime | Single Linux VM using Docker Compose. |
| Dev/prod parity | Same base Compose topology and images; environment overlays only. |
| Language | Python 3.11+ for API, workers, and scheduler. |
| API framework | FastAPI. |
| Queue | Celery with Redis for MVP. |
| Durable job truth | PostgreSQL is the authoritative job ledger; Celery messages are execution triggers. |
| Spatial database | PostgreSQL + PostGIS. |
| STAC catalog | Adopt pgSTAC; pin its version and manage it separately from Alembic app tables. |
| Object storage | MinIO with private lake-zone prefixes. |
| Ingress | Caddy by default; Traefik only if Docker-native dynamic routing is required. |
| Secrets | SOPS + age for MVP; database stores only `secret_ref`. |
| Backups | pgBackRest for PostgreSQL; restic/replication or equivalent for MinIO/config backups. |
| Observability | Prometheus, Grafana, Loki, Alertmanager, Flower, node exporter, cAdvisor. |
| VM automation | Ansible bootstrap converted from Phase 0 manual setup notes. |
| Provider validation in Phase 1 | Mock/stub provider only; live provider validation moves to provider phases. |

## 5. Target Phase 1 platform shape

```text
Public/admin edge
  Caddy or Traefik
    api

Internal Compose network
  api
  scheduler
  worker-search
  worker-download
  worker-process
  worker-heavy
  worker-stats
  redis
  postgres-postgis-pgstac
  minio
  titiler
  prometheus
  grafana
  loki
  alertmanager
  flower
  pgbackrest
```

All services except the approved API edge must remain private, internal-only, or protected by admin-only access. MinIO, Postgres, Redis, workers, Prometheus, Loki, Flower, and TiTiler must not be publicly exposed during Phase 1.

## 6. Workstreams and backlog

### 1.0 Phase 0 handoff and foundation decisions

Deliverables:

- Phase 0 status reconciliation or explicit override note.
- VM access, storage, scratch, ports, NSG, and ingress ownership checklist.
- Decisions for pgSTAC, CI/image registry, Caddy vs Traefik, backup target, SOPS age key custody, and dashboard scope.
- Phase 1-only storage ceiling if the current 503 GB `/srv/akasha` disk remains unchanged.

Acceptance:

- No unresolved port conflict blocks Compose deployment.
- Public ingress policy is documented.
- Phase 1 storage/scratch constraints are explicit.
- Execution can proceed without assuming unavailable provider products.

### 1.1 Repository and application scaffold

Deliverables:

- Python project layout.
- FastAPI app shell.
- Celery worker app shell.
- Scheduler package/module shell.
- Shared settings/config module.
- Basic test framework.
- Lint/type-check commands if selected.
- Module boundaries aligned with the architecture document.

Acceptance:

- API container starts.
- Worker container starts.
- Basic tests run locally and in CI.
- Configuration loads without secrets being present in source control.

### 1.2 Docker Compose and Ansible foundation

Deliverables:

- `deploy/docker-compose.yml` base graph.
- Dev/prod overlay pattern.
- Service health checks.
- Compose private networks.
- Persistent volume mapping under `/srv/akasha`.
- Docker log rotation.
- Caddy default ingress config.
- Ansible inventory and bootstrap roles for base packages, Docker, users, firewall, mounts, node exporter, and deploy directories.

Acceptance:

- Compose stack starts on the dev environment.
- Only the intended edge ports are reachable publicly.
- Internal services are reachable only inside the Compose/admin network.
- Host setup can be reproduced from Ansible.

### 1.3 Configuration and secrets

Deliverables:

- Environment-specific config schema.
- SOPS + age encrypted secret workflow.
- Runtime decrypt/loading process.
- `secret_ref` convention for provider and API credentials.
- Log redaction rules for provider credentials, authorization headers, signed URLs, and tokens.
- API key storage approach using hashed keys, owner/name metadata, rotation hooks, and redacted logging.

Acceptance:

- Application resolves secret references without storing plaintext secrets in the database.
- Logs do not print secrets or signed URLs.
- Secret resolution failure is explicit and visible.
- API keys can be rotated without schema changes.

### 1.4 Database, PostGIS, pgSTAC, and migrations

Deliverables:

- PostgreSQL + PostGIS container.
- pgSTAC initialization with pinned version.
- Separate schema ownership:
  - `pgstac` schema managed by pgSTAC init/migration tooling.
  - `akasha` schema managed by Alembic.
- Alembic migration baseline.
- Core Phase 1 operational tables:
  - `satellite_sources`
  - `source_credentials`
  - `provider_execution_policies`
  - `aoi_registry`
  - `provider_scenes`
  - `provider_orders`
  - `scene_assets`
  - `processing_jobs`
  - `raster_outputs`
  - `tile_layers`
  - `audit_logs`
- JSONB extension columns for provider-specific metadata that Phase 0 samples did not empirically confirm.
- Seed process for source registry, execution policies, Bangalore AOI, and sample fields.
- Required spatial and operational indexes.

Reserved for Phase 2+ unless needed by a concrete Phase 1 smoke test:

- `field_queries`
- `field_time_series_queries`
- `progressive_ndvi_summaries`
- `visualization_profiles`
- `threshold_profiles`

Acceptance:

- Empty database migrates successfully.
- pgSTAC initializes before app migrations that reference STAC concepts.
- Seed registry loads from configuration/catalog inputs.
- Core uniqueness, idempotency, spatial indexes, and job-state constraints exist.
- Schema remains extensible for real provider metadata discovered later.

### 1.5 MinIO lake and storage abstraction

Deliverables:

- Private MinIO bucket or bucket-prefix model.
- Lake zones:
  - `raw/`
  - `extracted/`
  - `ard/`
  - `indices/`
  - `qa/`
  - `analytics/`
  - `reports/`
  - `mosaics/`
  - `tmp/`
- Object path convention.
- Checksum and lineage metadata convention.
- Storage service abstraction.
- Raw lifecycle cleanup disabled by default.
- Versioning/object-lock decision for important Phase 1 objects.

Acceptance:

- API/worker can write and read test objects.
- Raw objects are private.
- Mock raw package write records checksum and lineage in the database.
- Cleanup cannot delete raw objects unless explicitly enabled by configuration and audit policy.

### 1.6 Mock provider, job queue, and scheduler foundation

Deliverables:

- Mock provider adapter that returns deterministic fixture scenes/assets.
- Synthetic fixture package for raw-lake and job orchestration tests.
- Celery app and Redis broker.
- Queue definitions:
  - `search`
  - `download`
  - `preprocess`
  - `heavy-cpu`
  - `cog`
  - `stats`
  - `maintenance`
- Provider execution-policy loader.
- Rate-limit, concurrency, retry/backoff, and backpressure foundations.
- DB-backed job idempotency keys.
- Scheduler singleton/advisory-lock behavior.
- Stuck-job reconciliation and safe requeue behavior.

Acceptance:

- Mock sync creates a durable job row.
- Job enqueues, executes, retries, and updates DB state.
- Mock raw package writes to MinIO with checksum/lineage.
- Provider policy tests prove throttling/concurrency behavior without live provider credentials.
- Duplicate sync requests do not create duplicate durable jobs.

## 7. Sync idempotency contract

Use a shared idempotency key across API, scheduler, and workers:

```text
hash(source_id + aoi_id + date_start + date_end + job_type + request_params_version + processing_profile_version)
```

The database must enforce uniqueness for active or completed jobs where duplicate execution would create duplicate provider orders, duplicate downloads, or conflicting lineage.

## 8. API foundation

Initial endpoints:

```text
GET  /health
GET  /api/v1/sources
POST /api/v1/ingestion/sync
GET  /api/v1/jobs
GET  /api/v1/jobs/{jobId}
```

Deliverables:

- Versioned route structure.
- Consistent response envelope.
- Consistent error schema.
- MVP API key auth.
- Request ID / trace ID propagation.
- Idempotent `POST /api/v1/ingestion/sync`.
- Opaque job/layer identifiers.
- No MinIO internal paths in client responses.

Acceptance:

- Health endpoint reports API, DB, Redis, MinIO, and worker/scheduler readiness where appropriate.
- Source endpoint returns seeded catalogue entries and exposure states.
- Sync endpoint starts a mock-provider job only.
- Job endpoints reflect DB state, not just Celery state.
- Unauthorized requests are rejected.

## 9. TiTiler integration

Deliverables:

- Internal TiTiler or TiTiler-PgSTAC service.
- Tiny static COG fixture or generated COG fixture loaded into MinIO.
- Internal smoke request for metadata/tile response.
- API contract that returns opaque layer IDs or signed URLs, never storage paths.

Acceptance:

- TiTiler is reachable internally.
- Smoke fixture returns valid metadata and tile response.
- Public clients cannot directly reach MinIO or raw object paths.
- Phase 2 can replace the fixture with Sentinel-2-derived COGs without changing the serving contract.

## 10. Observability and operations

Deliverables:

- Structured JSON logs.
- Prometheus metrics.
- Grafana dashboards.
- Loki log ingestion.
- Alertmanager baseline alerts.
- Flower for Celery visibility.
- node exporter and cAdvisor wired into Prometheus.
- Host node exporter Docker scrape path resolved from the Phase 0 localhost-only setup.

Minimum dashboards:

- System overview.
- API health.
- Queue and worker health.
- Provider/mock ingestion.
- Storage and scratch.
- Database health.
- Backup/restore health.

Minimum alerts:

- API down.
- TiTiler down.
- Postgres down.
- MinIO down.
- Redis down.
- Queue backlog above configured threshold.
- Worker failures above configured threshold.
- `/srv/akasha` disk above configured threshold.
- Scratch disk above configured threshold.
- Backup failed.
- Provider auth failures when live providers are enabled later.
- Product exposure attempted before validation.

Acceptance:

- Logs, metrics, dashboards, and alerts are visible during stack bring-up, not only after the stack is complete.
- Disk and scratch usage are visible.
- Backup status is visible.
- Secret redaction is verified with synthetic inputs.

## 11. Backup and restore foundation

Deliverables:

- pgBackRest configuration for PostgreSQL.
- MinIO backup/replication/restic decision for Phase 1 bounded data.
- Config, Compose, Ansible, dashboard, alert, and encrypted secret backup process.
- Restore runbook.
- Restore drill using a bounded dataset:
  - PostgreSQL metadata and pgSTAC state.
  - One mock raw object.
  - One TiTiler smoke COG fixture.
  - Encrypted config/secrets.
  - Service restart and health validation.

Acceptance:

- Restore drill completes on dev/staging.
- Restored API can read restored metadata.
- Restored MinIO object checksums match.
- TiTiler smoke fixture works after restore.
- Backup failure alert fires in a controlled test.
- Production RPO/RTO values remain stakeholder decisions and are not claimed by Phase 1.

## 12. CI, image build, and release baseline

Deliverables:

- CI workflow for tests and lint/type checks if selected.
- Migration validation against an empty database.
- pgSTAC initialization validation.
- API and worker image builds.
- Pinned Python, GDAL, PROJ, and base service versions.
- Versioned image tags.
- Image registry decision and documentation.
- Deployment smoke-test script/checklist.

Acceptance:

- CI builds API and worker images reproducibly.
- CI validates pgSTAC initialization and Alembic migrations.
- CI runs basic app tests.
- Dev deployment uses versioned image tags instead of ad hoc local builds.

## 13. Sequencing

1. Complete Phase 0 handoff reconciliation and foundation decisions.
2. Add repository scaffold, config module, test harness, and minimal CI.
3. Build Compose/Ansible foundation with early logs and metrics.
4. Bring up Postgres/PostGIS/pgSTAC and Alembic core schema.
5. Configure MinIO lake zones and storage abstraction.
6. Implement mock-provider raw-lake validation path.
7. Implement Celery queues, DB job ledger, scheduler lock, execution-policy tests, and stuck-job reconciliation.
8. Implement API endpoints, API key auth, idempotent sync, and job visibility.
9. Wire internal TiTiler smoke fixture and opaque layer contract.
10. Complete dashboards, alerts, redaction checks, and public-port verification.
11. Configure backups and perform bounded restore drill.
12. Finalize image registry, release flow, Ansible docs, runbooks, and Phase 1 exit evidence.

## 14. Phase 1 exit gate

Phase 1 is complete only when:

1. Docker Compose platform runs on the Azure dev VM or approved equivalent.
2. Public ingress exposes only approved edge routes.
3. Postgres/PostGIS/pgSTAC initializes cleanly.
4. Alembic migrations run from an empty database.
5. Source registry and Bangalore AOI/sample fields seed successfully.
6. MinIO private lake zones are available.
7. Mock raw package writes preserve checksum and lineage metadata.
8. Celery mock jobs execute, retry, and update DB state.
9. Provider execution policies throttle or limit mock jobs in tests.
10. Scheduler duplicate protection prevents duplicate durable jobs.
11. API health/source/sync/job endpoints work with MVP auth.
12. TiTiler is reachable internally and serves the smoke COG fixture.
13. Logs redact credentials, tokens, authorization headers, and signed URLs.
14. Dashboards and alerts cover service health, queue depth, disk/scratch, failures, and backups.
15. PostgreSQL, MinIO fixture data, config/secrets, and service health restore successfully in a bounded drill.
16. CI builds pinned API/worker images and validates migrations.
17. Phase 1 runbooks and handoff docs are complete.

## 15. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Phase 0 docs conflict with operator assertion | Make handoff reconciliation Step 1 and record any explicit override before execution. |
| Missing provider samples cause schema churn | Keep Phase 1 core schema provider-agnostic and use JSONB extension fields for provider-specific metadata. |
| Current VM storage is too small for future backfill | Treat current storage as Phase 1-only unless expanded; set disk alerts and defer real backfills to later phases. |
| No separate scratch disk | Add `/scratch/akasha` or explicitly cap Phase 1 workloads to small fixtures. |
| Port conflicts or public exposure | Audit existing listeners, capture NSG, and verify only approved edge ports are public. |
| pgSTAC migration ownership confusion | Separate pgSTAC-managed schema from Alembic-managed `akasha` schema and validate both in CI. |
| Redis broker durability | Keep DB as authoritative job ledger and reconcile stuck jobs. |
| Secrets leakage | Use SOPS+age, `secret_ref`, redaction tests, and no plaintext secrets in DB/repo/logs. |
| TiTiler acceptance depends on Phase 2 assets | Use a tiny static/generated COG fixture in Phase 1. |
| Restore gate is vague | Use bounded restore drill with explicit metadata, object, config, and service-health checks. |

## 16. Phase 1 documentation deliverables

- VM setup runbook.
- Deployment runbook.
- Schema and migration notes.
- pgSTAC ownership notes.
- Provider execution-policy notes.
- Secret management and rotation notes.
- Backup and restore runbook.
- Operations smoke-test checklist.
- Public-port and ingress verification checklist.
- Phase 1 exit evidence checklist.
