---
goal: ResourceSat-2A Bhoonidhi ingestion pipeline migration into standalone Akasha Ingestion
version: 1.2
date_created: 2026-07-08
last_updated: 2026-07-09
owner: Akasha Engineering
tags: data, ingestion, resourcesat, bhoonidhi, orchestration, migration, geospatial, fastapi, celery, postgis, pgstac
---

# Introduction

This implementation plan defines Phase 3: move ResourceSat-2A Bhoonidhi/NRSC ingestion and processing from the product application repository into the standalone `akasha-ingestion` platform. The goal is to make `akasha-ingestion` the unified orchestration layer for satellite ingestion, processing, cataloging, readiness, and field analytics across Sentinel-2, ResourceSat, and future satellite sources.

This plan covers all three ResourceSat-2A sources in one migration slice:

- `resourcesat-2a-liss3-boa`
- `resourcesat-2a-liss4-mx70-l2`
- `resourcesat-2a-awifs-boa`

The product application already contains ResourceSat/Bhoonidhi behavior under `akasha-em-git`, but Phase 3 must be implemented as a clean standalone ingestion implementation inside `akasha-ingestion`. The current product-app ResourceSat code, docs, and tests are reference material only. Do not import product-app ingestion modules directly into `akasha-ingestion`.

The migration is a development cutover with one target path: standalone ingestion. Do not implement a long-lived app-native alternate ResourceSat path. The correct safety mechanism is strict validation before cutover: if standalone ResourceSat ingestion does not pass acceptance, do not switch the product app yet; fix the ingestion pipeline and rerun validation.

> **Review note (v1.1, 2026-07-09):** This plan was hardened after a multi-model, code-grounded review (Opus 4.8 + GPT 5.5 + direct verification against `akasha-ingestion/src`). Key corrections: real Celery queue names (`heavy-cpu`/`preprocess`/`cog`/`stats`, not `process`/`heavy`); add the missing `ndwi_green_nir` index formula to the engine; fail-closed scratch/data-root preflight (default `/tmp/akasha` violates OPS-002); `FieldIndexRequest` needs `sourceId` + `NDWI_GREEN_NIR`; existing seed rows advertise wrong indices; readiness summary field-name contract (`processed_count`/`failed_count`); relax the `SyncRequest` provider-route guard; scope SEC-003 to browser-visible JSON; `scheduler/locks.py` already exists (extend, not create); and gate the destructive app-native removal (TASK-105) behind recorded live staging acceptance.
> **Phase 1 review refinement (v1.2, 2026-07-09):** Clarifies TASK-007 acceptance inheritance/evidence requirements and TASK-025 Phase 4 profile-module scope.

## 1. Requirements & Constraints

- **REQ-001**: Implement ResourceSat-2A ingestion in `akasha-ingestion` for `resourcesat-2a-liss3-boa`, `resourcesat-2a-liss4-mx70-l2`, and `resourcesat-2a-awifs-boa`.
- **REQ-002**: Use Bhoonidhi/NRSC as the only Phase 3 provider path. Do not implement Bhuvan, CDSE, USGS, Earthdata, or any non-Bhoonidhi ResourceSat provider in this phase.
- **REQ-003**: Implement Phase 3 as a clean rewrite in `akasha-ingestion`; use `akasha-em-git` ResourceSat modules and tests only as behavioral references.
- **REQ-004**: After staging acceptance, switch the product app ResourceSat path to ingestion-backed ResourceSat immediately. Do not build a product-app native ResourceSat alternate mode.
- **REQ-005**: Keep the browser isolated from ingestion internals. Browser requests must call only the product app origin. The product app BFF calls `akasha-ingestion` server-to-server with `INGESTION_API_URL` and `INGESTION_API_KEY`.
- **REQ-006**: Reuse existing standalone ingestion primitives wherever appropriate: `provider_scenes`, `scene_assets`, `raster_outputs`, `tile_layers`, `field_queries`, `backfill_runs`, `processing_job_stages`, signed field-index routes, readiness, and pgSTAC registration.
- **REQ-007**: Produce app-consumable field analytics for ResourceSat through the existing standalone ingestion `field-index` contract: statistics, signed stats URL, signed overlay URL, signed point URL, quality, resolution, and provenance.
- **REQ-008**: Support source-aware readiness for ResourceSat sources using the existing `/api/v1/analytics/readiness?sourceId=...&aoiId=...` shape.
- **REQ-009**: Generate ResourceSat derived index COGs in `akasha-ingestion` so Sentinel-2 and ResourceSat share the same analytics path through `RasterOutputRecord` and `AnalyticsService`.
- **REQ-010**: Preserve raw/source data retention by default. Raw ResourceSat downloads and generated source/derived COGs must not be deleted unless a future explicit lifecycle policy is implemented and enabled.
- **REQ-011**: Implement idempotent, resumable backfill/sync jobs for ResourceSat. Duplicate searches, downloads, scene preparation, composites, and index outputs must not create duplicate durable rows or duplicate final objects.
- **REQ-012**: Implement bounded job execution. Every live ResourceSat run must accept per-run caps for search results, downloads, date range, and resource-heavy processing.
- **REQ-013**: Implement dry-run planning for ResourceSat that performs no provider download, no raster processing, no object upload, and no pgSTAC mutation.
- **REQ-014**: Implement live provider calls only on approved staging runtime. `akasha-control`, product app containers, browser clients, and local laptops must not run Bhoonidhi provider downloads.
- **REQ-015**: Maintain a strict separation between source assets, prepared analytic/mask assets, derived index outputs, and field query artifacts.
- **REQ-016**: Do not collapse `akasha-ingestion` into the product app. `akasha-ingestion` remains a standalone FastAPI + Celery + Postgres/PostGIS/pgSTAC + MinIO ingestion platform.
- **REQ-017**: Keep ResourceSat source names and provider collection names deterministic:
  - `resourcesat-2a-liss3-boa` → `ResourceSat-2A_LISS3_BOA`
  - `resourcesat-2a-liss4-mx70-l2` → `ResourceSat-2A_LISS4-MX70_L2`
  - `resourcesat-2a-awifs-boa` → `ResourceSat-2A_AWIFS_BOA`
- **REQ-018**: ResourceSat readiness and field-index responses must explicitly include native/processing/display resolution metadata so users do not infer false precision.
- **REQ-019**: LISS-4 narrow-swath behavior must be explicit. Do not represent LISS-4 as full-AOI coverage when it only covers part of the AOI.
- **REQ-020**: AWiFS coarse/regional behavior must be explicit. Do not present AWiFS 56 m pixels as field-scale precision without quality warnings.
- **REQ-021**: Remove or disable product-app-native ResourceSat processing after ingestion-backed ResourceSat passes acceptance. The final product app ResourceSat path must be BFF-to-ingestion, not app-local raster processing.
- **SEC-001**: Never commit or log Bhoonidhi usernames, passwords, tokens, signed provider URLs, MinIO credentials, API keys, or raw provider download URLs.
- **SEC-002**: Store Bhoonidhi credentials in `Settings` as `SecretStr` values sourced from environment variables or secret management. Do not store plaintext credentials in Postgres tables or docs.
- **SEC-003**: The no-leak rule scopes to **browser-visible product-app JSON/headers**: those responses must never expose `s3://` URLs, MinIO object keys, raw ZIP paths, internal IPs, internal hostnames, pgSTAC hrefs, TiTiler internal URLs, Bhoonidhi URLs, query signatures, or API keys. Server-to-server ingestion `field-index` responses legitimately carry signed same-origin URLs (`statsUrl`, `overlayUrl`, `pointUrl`, `tileUrl`) per REQ-007; the product app BFF is responsible for consuming them server-side and never forwarding raw signed/internal values to the browser. Never log any of the above regardless of surface.
- **SEC-004**: Keep `akasha-ingestion` private/server-to-server. Do not make ingestion, MinIO, Postgres, pgSTAC, TiTiler, or Bhoonidhi endpoints browser-facing.
- **SEC-005**: Redact provider request/response artifacts before writing observability summaries or job event logs.
- **SEC-006**: Live ResourceSat execution must require an approved-runtime signal on `akasha-staging`; local development and product app hosts may run dry-run or mocked tests only.
- **GEO-001**: LISS-3 and AWiFS analytic band order is exactly `[BAND2 Green, BAND3 Red, BAND4 NIR, BAND5 SWIR1]`.
- **GEO-002**: LISS-4 analytic band order is exactly `[BAND2 Green, BAND3 Red, BAND4 NIR]`.
- **GEO-003**: ResourceSat reflectance correction is `corrected = dn * 0.0001 + 0.0`. Do not apply Sentinel-2 `-0.1` offset.
- **GEO-004**: ResourceSat has no SCL. Use Akasha threshold mask v1 classes: `0=nodata`, `1=valid`, `2=cloud`, `3=shadow`, `4=water`.
- **GEO-005**: Valid ResourceSat mask classes for analytics are `{1, 4}`.
- **GEO-006**: Resample categorical masks with nearest-neighbor only.
- **GEO-007**: Resample continuous reflectance bands with bilinear or cubic interpolation only.
- **GEO-008**: ResourceSat sources must never advertise or generate `NDRE` or `RECI`.
- **GEO-009**: LISS-4 must never advertise or generate `NDMI` or `NDBI` because it has no SWIR1 band.
- **GEO-010**: ResourceSat display uses false-colour composite `NIR,RED,GREEN`. Do not use Sentinel true-colour assumptions.
- **GEO-011**: Keep analytic COG and mask COG as separate assets.
- **GEO-012**: Store mask method/version on every prepared scene, composite, derived output, and field-index response provenance.
- **GEO-013**: The canonical index identifier for the green/NIR water index is `ndwi_green_nir` (never generic `ndwi`). This exact id must be used consistently across seed data, visualization/threshold profiles, the index engine, `raster_outputs`, readiness index coverage, and field-index responses.
- **GEO-014**: ResourceSat supported-index sets are exactly: LISS-3/AWiFS = `{ndvi, msavi, ndmi, ndwi_green_nir}`; LISS-4 = `{ndvi, msavi, ndwi_green_nir}`. `savi`, `gndvi`, generic `ndwi`, `ndbi`, `ndre`, and `reci` are never ResourceSat indices. `ndbi` is intentionally unsupported for LISS-3/AWiFS even though SWIR1 exists (no validated built-up profile in Phase 3).
- **OPS-001**: All bulk downloads, raw products, scratch files, COGs, composites, and validation artifacts must stay on `akasha-staging` under `/srv/akasha` or the configured `AKASHA_` ingestion data root.
- **OPS-002**: Do not write ResourceSat bulk data to `/`, `/tmp`, `/var/tmp`, `/var/lib/docker`, `/data/coolify`, or `akasha-control`.
- **OPS-003**: Use bounded defaults for live runs: one source/AOI lock at a time, small initial `max_downloads`, and worker queue isolation.
- **OPS-004**: ResourceSat backfills must not starve field analytics, Sentinel-2 preload, or routine maintenance tasks.
- **OPS-005**: All failures must be categorized in `backfill_runs.summary_json` and job/stage metadata.
- **OPS-006**: `akasha-ingestion` deploys through the existing client mirror and staging deployment flow. Do not create a separate deployment mechanism for ResourceSat.
- **OPS-007**: Scratch/data-root safety must be **fail-closed at startup**. The default `Settings.scratch_dir` is `/tmp/akasha`, which violates OPS-002; live ResourceSat runs must refuse to start unless the resolved scratch, raw, work, and output roots resolve under an approved data root (`/srv/akasha` or a configured `AKASHA_` root) and are not on `/`, `/tmp`, `/var/tmp`, `/var/lib/docker`, or `/data/coolify`. Staging must explicitly set `AKASHA_SCRATCH_DIR` (and any work dir) under `/srv/akasha`.
- **DB-001**: Use Alembic for schema changes under `akasha-ingestion/migrations/versions/`. Do not add raw SQL migration files outside Alembic.
- **DB-002**: Runtime queries use raw SQL through SQLAlchemy `text()` in repository classes, matching existing `akasha-ingestion` conventions.
- **DB-003**: New indexes must be justified by query patterns. Avoid unnecessary wide indexes.
- **DB-004**: If schema changes add constraints to existing populated tables, use safe, staged constraints where required.
- **DB-005**: Do not duplicate pgSTAC-owned catalog metadata into app tables except for operational lookup fields already represented in `provider_scenes`, `scene_assets`, `raster_outputs`, and `tile_layers`.
- **CON-001**: `akasha-ingestion` uses manual app-state dependency wiring. Do not introduce a dependency injection framework.
- **CON-002**: `akasha-ingestion` supports `RuntimeBackend.MEMORY` and `RuntimeBackend.EXTERNAL`. New ResourceSat components must support memory tests and external runtime behavior.
- **CON-003**: Paired in-memory and database implementations must share method signatures without introducing abstract base classes.
- **CON-004**: New Python modules must start with `from __future__ import annotations`.
- **CON-005**: Heavy geospatial dependencies must be imported lazily where possible so lightweight imports remain healthy.
- **CON-006**: Do not run live Bhoonidhi provider tests by default. Live tests require explicit environment opt-in.
- **PAT-001**: Reuse `Sentinel2IngestionService` **primitives only** — job creation, idempotency keys, stage store, scene/asset/output registration, and backfill summaries. Do not assume its pipeline shape: `Sentinel2IngestionService` records a single `search` stage and mirrors hosted STAC assets, and the earthsearch provider contract models hosted STAC assets (`NormalizedStacItem`/`NormalizedAsset`), not authenticated ZIP downloads. ResourceSat is a **new multi-stage pipeline** (search → ZIP download → extract → scene prepare → composite → index → catalog) whose provider/scene contract must be defined explicitly (see TASK-064a).
- **PAT-002**: Follow existing `APIResponse[T]` envelope for all FastAPI routes.
- **PAT-003**: Follow existing `ObjectStore` path style for raw/source/derived objects and checksum sidecars.
- **PAT-004**: Follow existing signed URL verification for stats, overlay, tile, and point routes.
- **PAT-005**: Follow existing `pytest` style: deterministic fixtures, no live provider calls by default, behavior-focused tests.
- **CUT-001**: Do not implement a product-app native ResourceSat alternate path. The migration is complete only when ResourceSat product app requests are served through standalone ingestion.
- **CUT-002**: If acceptance fails, stop before product app cutover, fix standalone ingestion, and rerun validation. Do not mask incomplete migration by routing ResourceSat back to app-native processing.

## 2. Implementation Steps

### Implementation Phase 1 — Contract freeze and plan artifact

- GOAL-001: Create an executable ResourceSat Phase 3 contract and verify prerequisites before code changes.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Confirm `akasha-ingestion` branch state with `git status --short` and ensure no uncommitted code changes exist before implementation. | ⬜ | — |
| TASK-002 | Confirm Sentinel-2 Phase 2 regression state by running `python -m pytest -q` and `ruff check .` from `akasha-ingestion`. Record results in the implementation PR notes. | ⬜ | — |
| TASK-003 | Confirm the current staged Sentinel-2 readiness evidence remains usable as the ResourceSat cross-validation baseline. Record processed Sentinel-2 dates and AOI footprint in `docs/phase-2-sentinel-2-vertical-slice-implementation-plan.md` or a Phase 3 validation note. | ⬜ | — |
| TASK-004 | Freeze ResourceSat source IDs and Bhoonidhi collection IDs in this plan and in `src/akasha/processing/resourcesat.py`. | ⬜ | — |
| TASK-005 | Freeze app cutover contract: product app BFF endpoints remain stable, but ResourceSat data must come from `akasha-ingestion` after acceptance. | ⬜ | — |
| TASK-006 | Define AOI mapping in docs: ingestion AOI `bangalore_60km_geodesic_aoi` and operational/app AOI label `bangalore-60km` must be mapped explicitly. | ⬜ | — |
| TASK-007 | Add a Phase 3 acceptance checklist under `docs/phase-3-resourcesat-bhoonidhi-acceptance.md` (define it **before** implementation starts) that inherits from and cross-references the `docs/implementation-roadmap.md` Phase 3 exit gate and `docs/akasha-ingestion-plan.md` acceptance criteria. Use **measurable** pass thresholds, not just "verify": minimum usable-pixel %, minimum AOI coverage % per source (LISS-3 high, LISS-4 partial allowed, AWiFS regional), maximum acquisition-age/freshness, required indices present per source, Sentinel-2 cross-validation tolerance, deterministic source/date selection, raw checksum/retention evidence, idempotency/no duplicate durable rows or final objects, COG validation, mask/version provenance, native/processing/display resolution metadata, fail-closed scratch/data-root preflight evidence, and the leakage-audit assertions. TASK-116–120 must be checked against these inherited criteria and numeric thresholds. | ⬜ | — |

### Implementation Phase 2 — Settings, source seed data, and provider routes

- GOAL-002: Make ResourceSat/Bhoonidhi first-class in standalone ingestion configuration and seed data without running provider calls.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-008 | Extend `src/akasha/config.py::Settings` with Bhoonidhi settings: `bhoonidhi_api_base`, `bhoonidhi_user_id`, `bhoonidhi_password`, `bhoonidhi_search_rps`, `bhoonidhi_timeout_seconds`, `bhoonidhi_download_chunk_bytes`, `bhoonidhi_max_downloads_per_run`, and `bhoonidhi_approved_runtime_required`. Use `SecretStr` for password/token-like values. | ⬜ | — |
| TASK-009 | Extend `src/akasha/config.py::Settings` with ResourceSat profile settings: `resourcesat_profile_version`, `resourcesat_liss3_profile_version`, `resourcesat_liss4_profile_version`, `resourcesat_awifs_profile_version`, `resourcesat_backfill_date_window_days`, and per-source preload schedule settings. | ⬜ | — |
| TASK-010 | Add field validators in `src/akasha/config.py` for optional integers and strings that may be empty in env files. Reuse `normalize_optional_int` pattern where applicable. | ⬜ | — |
| TASK-010a | Implement the OPS-007 fail-closed scratch/data-root validator: add a settings-level check (or startup preflight) that resolves the scratch/raw/work/output roots and refuses live (non-dry-run) ResourceSat execution when any resolves onto `/`, `/tmp`, `/var/tmp`, `/var/lib/docker`, or `/data/coolify`, or outside the approved `/srv/akasha`/`AKASHA_` data root. Note the default `scratch_dir=/tmp/akasha` must be overridden on staging. Add a memory-mode test for approved and rejected roots. | ⬜ | — |
| TASK-011 | Update `src/akasha/catalog/seed.py::SEED_SOURCES` for all three ResourceSat sources (rows already exist). **Replace the incorrect `supported_indices`**: current values are LISS-4 `["ndvi","msavi","savi","ndwi","gndvi"]` and LISS-3/AWiFS `["ndvi","msavi","savi","ndwi","ndmi","ndbi"]`; drop `savi`/`gndvi`/`ndbi`, rename `ndwi`→`ndwi_green_nir`, and set the GEO-014 sets (LISS-3/AWiFS `["ndvi","msavi","ndmi","ndwi_green_nir"]`, LISS-4 `["ndvi","msavi","ndwi_green_nir"]`). Also set accurate `schedule_state`, `product_exposure`, `provider_adapter`, `instrument_mode`, `analysis_level`. Add a regression test asserting the removed indices no longer appear. | ⬜ | — |
| TASK-012 | Update `src/akasha/catalog/seed_db.py::SOURCE_METADATA` with ResourceSat validation profile metadata, processing profile version, license profile, provider metadata, mask method, and pgSTAC collection names. | ⬜ | — |
| TASK-013 | Update `src/akasha/catalog/seed_db.py::PROVIDER_ROUTES` with three Bhoonidhi routes using `provider_adapter='bhoonidhi'`, `access_mode='authenticated_download'`, and `execution_policy_ref='bhoonidhi-default'`. | ⬜ | — |
| TASK-014 | Update `src/akasha/catalog/seed_db.py::seed_execution_policies` so `bhoonidhi-default` is enabled only in explicitly configured environments and has retry, staging, concurrency, checksum, and redaction policy metadata. | ⬜ | — |
| TASK-015 | Add ResourceSat visualization and threshold profiles to `src/akasha/catalog/seed_db.py::VISUALIZATION_PROFILES` and `THRESHOLD_PROFILES` for `ndvi`, `msavi`, `ndmi`, `ndwi_green_nir`, and source-specific supported subsets. | ⬜ | — |
| TASK-016 | Add tests in `tests/test_phase3_resourcesat_foundation.py` proving ResourceSat seed sources, provider routes, execution policy, processing profiles, and threshold profiles load in memory mode without provider calls. | ⬜ | — |

### Implementation Phase 3 — Bhoonidhi provider adapter

- GOAL-003: Implement a tested Bhoonidhi provider adapter with auth, search, pagination, download, retries, and redaction.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-017 | Create `src/akasha/providers/bhoonidhi.py` with `BhoonidhiClient`, `TokenSession`, `BhoonidhiError`, `BhoonidhiAuthError`, and `BhoonidhiDownloadUnavailable`. | ⬜ | — |
| TASK-018 | Implement `BhoonidhiClient.token()` to reuse fresh access tokens, refresh when possible, logout stale sessions, and use password grant when needed. | ⬜ | — |
| TASK-019 | Implement `BhoonidhiClient.search()` using `POST /data/search`, `collections`, `datetime`, `intersects`, CQL2 `Online=Y`, `limit`, `sortby`, pagination through `next` links, and `bhoonidhi_search_rps` throttling. | ⬜ | — |
| TASK-020 | Implement `BhoonidhiClient.download_product()` using streaming writes, `.part` temporary files, chunk size from settings, idempotent existing-file behavior, and token reset for `401`. Match the product-app reference retry policy: retry downloads on `412`, `500`, `502`, `503`, `504` (reserve `429` for search throttling, or document explicitly if Bhoonidhi rate-limits downloads with `429`). Reap orphaned `.part` files on retry/restart under a documented retention rule. | ⬜ | — |
| TASK-021 | Implement source-to-collection mapping function `source_collection(source_id: str) -> str` for the three ResourceSat sources only. Unknown sources must raise `ValueError`. | ⬜ | — |
| TASK-022 | Implement normalized candidate helpers that produce source ID, collection, provider product ID, acquisition datetime, bbox, AOI overlap, online status, provider properties, and redacted provider metadata. | ⬜ | — |
| TASK-023 | Implement error parsing and redaction helpers so provider errors written to job metadata never include credentials, tokens, or signed provider URLs. | ⬜ | — |
| TASK-024 | Add `tests/test_bhoonidhi_provider.py` covering missing credentials, password token, refresh token, logout, search pagination, no-results `404`, retryable statuses, online candidate filtering, download unavailable, existing download reuse, and redaction. | ⬜ | — |
| TASK-024a | Implement download-integrity checks in `BhoonidhiClient.download_product()`: verify the streamed byte count against any provider-declared content length, verify a provider-supplied checksum when Bhoonidhi exposes one, compute and record an internal SHA-256 when the provider does not expose a checksum, and fail the `raw_download` stage with category `download_failed` on any provider-size or provider-checksum mismatch. Add tests for size mismatch, mocked provider checksum mismatch, and internal SHA-256 recording when no provider checksum is present. | ⬜ | — |

### Implementation Phase 4 — ResourceSat source profiles, masks, and index matrix

- GOAL-004: Encode ResourceSat scientific invariants in standalone ingestion before processing raw data.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-025 | Extend `src/akasha/processing/resourcesat.py` (created in Phase 1 only as a thin source/collection constants module) with the immutable `ResourceSatProfile` dataclass and Phase 4 profile constants for the three supported sources. | ⬜ | — |
| TASK-026 | In `resourcesat.py`, define `LISS3_PROFILE` with collection `ResourceSat-2A_LISS3_BOA`, band roles `GREEN,RED,NIR,SWIR1`, band order `BAND2,BAND3,BAND4,BAND5`, pinned nominal native resolution `23.5` m with an explicit tolerance (reference uses ±2 m), an optional configured processing resolution override via `resourcesat_liss3_processing_resolution_m` (do not inline "or 24.0"), and supported indices `ndvi,msavi,ndmi,ndwi_green_nir`. | ⬜ | — |
| TASK-027 | In `resourcesat.py`, define `LISS4_PROFILE` with collection `ResourceSat-2A_LISS4-MX70_L2`, band roles `GREEN,RED,NIR`, band order `BAND2,BAND3,BAND4`, pinned nominal native resolution `5.8` m with an explicit tolerance, an optional configured processing resolution override via `resourcesat_liss4_processing_resolution_m` (do not inline "or 5.0"), and supported indices `ndvi,msavi,ndwi_green_nir`. | ⬜ | — |
| TASK-028 | In `resourcesat.py`, define `AWIFS_PROFILE` with collection `ResourceSat-2A_AWIFS_BOA`, band roles `GREEN,RED,NIR,SWIR1`, band order `BAND2,BAND3,BAND4,BAND5`, nominal resolution `56.0`, and supported indices `ndvi,msavi,ndmi,ndwi_green_nir`. | ⬜ | — |
| TASK-029 | Implement `reflectance_from_dn(values, valid_mask)` in `resourcesat.py` using scale `0.0001` and offset `0.0`; invalid pixels return `np.nan`. | ⬜ | — |
| TASK-030 | Implement `resourcesat_valid_mask(mask_values)` in `resourcesat.py` to keep only classes `{1, 4}`. | ⬜ | — |
| TASK-031 | Implement ResourceSat mask class constants with class labels and descriptions for nodata, valid, cloud, shadow, and water. | ⬜ | — |
| TASK-032 | Update `src/akasha/processing/indices.py` so ResourceSat source profiles drive band lookup and unsupported-index rejection. Do not hard-code ResourceSat band positions outside the profile module. **`calculate_index` currently supports only `{ndvi,ndmi,ndbi,ndre,msavi,reci}` and raises `ValueError` otherwise, so `ndwi_green_nir` must be added**: implement the normalized-difference branch `ndwi_green_nir = (GREEN - NIR) / (GREEN + NIR)` (with the same divide-by-zero masking as the other normalized indices) and add a matching `output_profile("ndwi_green_nir")` scaling/nodata/clip entry in `processing/sentinel2.py` (or a shared profile registry). | ⬜ | — |
| TASK-033 | Add tests in `tests/test_resourcesat_profiles.py` for profile lookup, collection mapping, band order, supported index matrix, unsupported `ndre`/`reci`, LISS-4 unsupported `ndmi`/`ndbi`, and mask class constants. | ⬜ | — |
| TASK-034 | Add tests in `tests/test_resourcesat_indices.py` for ResourceSat reflectance conversion, NDVI, MSAVI, NDMI, NDWI_GREEN_NIR, divide-by-zero masking, and source-specific unsupported index errors. | ⬜ | — |

### Implementation Phase 5 — Raw package retention and scene preparation

- GOAL-005: Convert downloaded Bhoonidhi ResourceSat products into validated scene-level analytic and mask COG assets.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-035 | Extend `src/akasha/storage/object_store.py::InMemoryObjectStore` and `MinIOObjectStore` with `put_raw_file(provider, source_id, product_id, file_path, ...)` and deterministic ResourceSat raw object paths `raw/bhoonidhi/{source_id}/{product_id}/original.zip`. Follow the existing `put_raw_package`/`put_file` conventions (PAT-003); reuse `put_raw_package` if its path shape is acceptable rather than adding a parallel method without justification. | ⬜ | — |
| TASK-036 | Extend `object_store.py` with methods for prepared ResourceSat scene COG file upload: `put_prepared_cog_file(provider, source_id, product_id, asset_key, file_path, checksum_sha256, metadata)`, mirroring the existing `put_source_cog_file` signature/return shape (`tuple[str, str]`) and checksum-sidecar behavior; document the distinct `prepared/...` path prefix vs the existing `raw/.../source-cogs/...`. | ⬜ | — |
| TASK-037 | Create `src/akasha/processing/resourcesat_prepare.py` with dataclasses `SelectedResourceSatProduct`, `ResourceSatBandMetadata`, and `PreparedResourceSatScene`. | ⬜ | — |
| TASK-038 | Implement product ZIP extraction in `resourcesat_prepare.py` using a configured scratch directory under `Settings.scratch_dir`. Reject extraction paths that escape the scratch directory. | ⬜ | — |
| TASK-039 | Implement band metadata parsing in `resourcesat_prepare.py` for path, row, date, valid range, background values, reflectance scale, and reflectance offset. | ⬜ | — |
| TASK-040 | Implement band file discovery in `resourcesat_prepare.py` for LISS-3, LISS-4, and AWiFS expected bands. Missing required bands must fail the scene preparation stage. | ⬜ | — |
| TASK-041 | Implement analytic COG writing in `resourcesat_prepare.py` using band order from `ResourceSatProfile`, `uint16`, nodata `0`, tiled COG layout, band descriptions, and ResourceSat metadata tags. | ⬜ | — |
| TASK-042 | Implement Akasha threshold mask v1 generation in `resourcesat_prepare.py` for four-band LISS-3/AWiFS and three-band LISS-4. Store mask COG as single-band `uint8`, nodata `0`, and nearest-neighbor overviews. | ⬜ | — |
| TASK-043 | Implement prepare manifest creation in `resourcesat_prepare.py` with source ID, collection, product ID, acquisition datetime, path/row where available, bbox/geometry, analytic output metadata, mask output metadata, mask classes, mask method, band role mapping, and `akasha:metrics_provisional=true`. | ⬜ | — |
| TASK-044 | Keep `resourcesat_prepare.py` pure: it must return `PreparedResourceSatScene` plus a manifest only. Defer `ProviderSceneRecord`/`SceneAssetRecord` persistence to `ResourceSatIngestionService` in Phase 8 after the Phase 7 ResourceSat provider/scene contract (TASK-064a) is defined; do not register rows directly from processing code. | ⬜ | — |
| TASK-045 | Add tests in `tests/test_resourcesat_prepare.py` for metadata parsing, safe extraction, band discovery, LISS-3 four-band output, LISS-4 three-band output, AWiFS output, mask generation, manifest content, checksum creation, and missing-band failure. | ⬜ | — |

### Implementation Phase 6 — ResourceSat compositing and validation

- GOAL-006: Build deterministic AOI composites and source-specific validation for ResourceSat products.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-046 | Create `src/akasha/processing/resourcesat_composite.py` with `CompositeGrid`, `AlignedResourceSatScene`, `ResourceSatCompositeBuildResult`, and `ResourceSatCompositeVerifyResult`. | ⬜ | — |
| TASK-047 | Implement AOI-to-grid logic using AOI geometry from `AoiRepository`, CRS override from AOI metadata when present, and UTM-zone selection when no override exists. | ⬜ | — |
| TASK-048 | Implement scene alignment to the composite grid using bilinear resampling for analytic reflectance DN and nearest-neighbor resampling for mask classes. | ⬜ | — |
| TASK-049 | Implement most-recent-valid-pixel compositing: sort scenes by acquisition datetime, prefer valid mask classes `{1,4}`, retain the first covered pixel only when no valid pixel exists, and set no-coverage pixels to mask class `0`. | ⬜ | — |
| TASK-050 | Implement LISS-3 composite defaults: high AOI coverage threshold, nominal processing resolution, four-band analytic output, and composite output kind `resource_sat_composite`. | ⬜ | — |
| TASK-051 | Implement LISS-4 composite/scene semantics: preserve three-band output, allow partial AOI coverage, record coverage warnings, and never generate SWIR-dependent indices. | ⬜ | — |
| TASK-052 | Implement AWiFS composite semantics: use 56 m resolution, regional/coarse warnings, and a lower configured coverage threshold. | ⬜ | — |
| TASK-053 | Implement composite COG writing with separate `analytic.tif` and `mask.tif`, overviews, checksums, and manifest metadata. | ⬜ | — |
| TASK-054 | Implement `verify_resource_sat_composite()` to check source ID, AOI ID, composite marker, contributing scenes, mask method, metrics provisional flag, class labels, analytic/mask shape alignment, CRS, resolution tolerance, overviews, mask classes, and coverage threshold. | ⬜ | — |
| TASK-055 | Add `tests/test_resourcesat_composite.py` covering grid snapping, AOI CRS override, most-recent-valid-pixel rule, LISS-4 three-band acceptance, AWiFS resolution, low coverage rejection, wrong AOI rejection, missing provenance rejection, and manifest-to-STAC buildability. | ⬜ | — |

### Implementation Phase 7 — Derived index outputs, pgSTAC, and tile layers

- GOAL-007: Register ResourceSat outputs in the same catalog and analytics structures used by Sentinel-2.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-056 | Add ResourceSat derived collection constants in `src/akasha/catalog/pgstac_repository.py`: `akasha-resourcesat-2a-liss3-boa-derived-v1`, `akasha-resourcesat-2a-liss4-mx70-l2-derived-v1`, and `akasha-resourcesat-2a-awifs-boa-derived-v1`. | ⬜ | — |
| TASK-057 | Add `build_resourcesat_derived_item(scene, outputs, bbox, geometry)` in `pgstac_repository.py` or a new helper module. Include STAC extensions `eo`, `raster`, `projection`, and `classification`. | ⬜ | — |
| TASK-057a | Make catalog registration source-aware: `pgstac_repository._collection()` currently hard-codes the Sentinel-2 description/metadata for every collection id, and `upsert_item_json` auto-synthesizes a minimal collection. Add per-source collection metadata (id, title, description, license, band/eo summaries, classification classes) so ResourceSat derived collections are registered with ResourceSat metadata, and register the collection explicitly (title/license/extensions) rather than relying solely on implicit synthesis. Add a test asserting the ResourceSat collection is not registered with Sentinel-2 metadata. | ⬜ | — |
| TASK-058 | Implement ResourceSat derived index COG generation in `resourcesat_ingestion.py` using `processing.indices.calculate_index`, ResourceSat reflectance conversion, ResourceSat valid mask, and profile-supported index list. | ⬜ | — |
| TASK-059 | Write derived index COGs from disk through a file-based `ObjectStore.put_derived_cog_file(...)` (see TASK-059a) using object paths `indices/bhoonidhi/{source_id}/{scene_or_composite_id}/{index}.cog.tif`. Do not use the existing in-memory `put_derived_cog(payload: bytes)` for ResourceSat outputs — composites/index COGs can be large and must not be held in memory. Index name in the path must be the canonical `ndwi_green_nir` (GEO-013), not generic `ndwi`. | ⬜ | — |
| TASK-059a | Add file-based `ObjectStore.put_derived_cog_file(provider, source_id, stac_item_id, index_name, file_path, checksum_sha256, metadata)` to `InMemoryObjectStore` and `MinIOObjectStore`, mirroring `put_source_cog_file` (streamed upload from disk + checksum sidecar), returning `tuple[str, str]`. | ⬜ | — |
| TASK-060 | Insert or update `RasterOutputRecord` rows for every supported ResourceSat derived index with formula version, processing profile version, dtype, scale factor, nodata, min/max, native/processing/display resolution, CRS, cloud mask version, and pgSTAC metadata. | ⬜ | — |
| TASK-061 | Insert or update `TileLayerRecord` rows for every ResourceSat derived output with private visibility and index/source metadata. | ⬜ | — |
| TASK-062 | Ensure unsupported ResourceSat indices are not generated, not registered in `raster_outputs`, not exposed in readiness, and not returned from field-index. | ⬜ | — |
| TASK-063 | Add `tests/test_resourcesat_pgstac.py` for derived item shape, collection ID, assets, classification classes, internal hrefs, source metadata, and pgSTAC upsert calls. | ⬜ | — |
| TASK-064 | Add `tests/test_resourcesat_raster_outputs.py` proving ResourceSat raster output rows and tile layers are deterministic and exclude unsupported indices. | ⬜ | — |
| TASK-064a | Define the ResourceSat provider/scene contract explicitly (the contract PAT-001 now defers to): specify how a Bhoonidhi ZIP candidate — which has **no hosted asset hrefs**, unlike the earthsearch `NormalizedStacItem`/`NormalizedAsset` model — maps to `ProviderSceneRecord` and `SceneAssetRecord` (raw ZIP asset, prepared analytic asset, prepared mask asset), and document the multi-stage stage-store choreography (search → download → prepare → composite → index → register). Do not assume the single-stage Sentinel-2 asset-mirroring shape. | ⬜ | — |

#### Phase 7 ResourceSat provider/scene contract

ResourceSat/Bhoonidhi registration is not a Sentinel-2 STAC asset mirror. Bhoonidhi search returns
download candidates and ZIP packages, not stable hosted COG asset hrefs. The durable catalog contract
for Phase 8 orchestration is:

1. **Prepared source scene row** — each downloaded ZIP that passes preparation maps to one
   `ProviderSceneRecord` with `provider_adapter="bhoonidhi"`, `source_id`, `provider_product_id`,
   `acquisition_at`, `scene_geometry`, `aoi_id`, `raw_object_path`, `native_crs`,
   `native_resolution`, `status="prepared"`, and a deterministic `logical_scene_key` built from
   source, Bhoonidhi collection, product ID, and acquisition datetime.
2. **Composite pseudo-scene row** — each accepted AOI composite maps to one synthetic
   `ProviderSceneRecord` before index registration. Its `provider_product_id` is
   `{source_id}:composite:{aoi_id}:{composite_date}`; `logical_scene_key` uses the same value plus
   collection and composite datetime. This non-null scene ID is required for `raster_outputs`
   idempotency, tile-layer joins, and `pgstac_item_id` resolution.
3. **Scene assets** — prepared scenes register `raw_zip`, `analytic`, and `mask` assets; composites
   register `analytic` and `mask` assets. Bhoonidhi assets use internal object paths (or local paths
   before upload), `mirror_status="not_required"`, and never store provider signed URLs as public
   asset hrefs.
4. **Derived indices** — Phase 7 generation reads analytic COG + mask COG, applies ResourceSat
   reflectance (`scale=0.0001`, `offset=0.0`), keeps mask classes `{1,4}`, generates only
   `ResourceSatProfile.supported_indices`, writes index COGs to disk, and uploads via
   `put_derived_cog_file(...)` to
   `indices/bhoonidhi/{source_id}/{scene_or_composite_id}/{index}.cog.tif`.
5. **Registration order** — search → download raw ZIP → prepare scene COGs → register prepared scene
   and assets → composite → register composite pseudo-scene and assets → generate derived indices →
   upsert `RasterOutputRecord` + private `TileLayerRecord` rows → upsert the source-aware pgSTAC
   derived item and persist `provider_scenes.pgstac_item_id`.

### Implementation Phase 8 — ResourceSat orchestration service and Celery tasks

- GOAL-008: Execute ResourceSat search, download, prepare, composite, index generation, and catalog registration through durable jobs.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-065 | Create `src/akasha/services/resourcesat_ingestion.py` with class `ResourceSatIngestionService`. Constructor dependencies must match existing factory style: job store, stage store, backfill repository, settings, AOI repository, scene repository, asset repository, raster repository, object store, pgSTAC repository, tile layer repository, provider, and optional scheduler lock service. | ⬜ | — |
| TASK-066 | Add `ResourceSatBackfillSummary` dataclass with searched, selected, downloaded, prepared, composited, processed, skipped, failed, deferred, retryable, terminal, raw bytes, derived bytes, product IDs, composite IDs, and failed item details. Its `to_metadata()`/serialization written into `job.result_metadata["backfill_summary"]` **must emit `processed_count` and `failed_count` keys**, because readiness `_is_output_producing_full_pipeline` reads exactly those keys to decide job success; a `processed`/`failed` naming divergence would make ResourceSat readiness silently never become AVAILABLE (see RISK-013). | ⬜ | — |
| TASK-067 | Implement `ResourceSatIngestionService.start_backfill(request: SyncRequest) -> Job` using ResourceSat idempotency keys and eager/non-eager dispatch matching `Sentinel2IngestionService.start_backfill`. | ⬜ | — |
| TASK-068 | Implement `ResourceSatIngestionService.execute_backfill(job_id: str, mode: str)` with durable stages: `provider_search`, `raw_download`, `prepare_scene`, `scene_validation`, `composite`, `composite_validation`, `index_generation`, `pgstac_registration`, `readiness_refresh`, and `cleanup`. | ⬜ | — |
| TASK-069 | Implement mode handling: `metadata_only`, `download_only`, `prepare_only`, `composite_only`, and `full_pipeline`. Each mode must update `backfill_runs.summary_json` with skipped downstream stage counts. | ⬜ | — |
| TASK-070 | Implement per-product failure categorization: `provider_auth`, `provider_rate_limit`, `no_results`, `download_unavailable`, `download_failed`, `invalid_product`, `prepare_failed`, `low_coverage`, `composite_failed`, `index_failed`, `pgstac_failed`, `object_store_failed`, and `unknown`. | ⬜ | — |
| TASK-071 | Add ResourceSat idempotency helpers in `src/akasha/jobs/idempotency.py`: `compute_resourcesat_backfill_idempotency_key`, `compute_resourcesat_download_idempotency_key`, `compute_resourcesat_prepare_idempotency_key`, `compute_resourcesat_composite_idempotency_key`, and `compute_resourcesat_index_output_idempotency_key`. | ⬜ | — |
| TASK-072 | Extend `src/akasha/schemas.py::SyncRequest` so `job_type` accepts `resourcesat_backfill`, validates source IDs, validates `provider_route` values, validates mode values, and rejects non-ResourceSat sources for ResourceSat jobs. **Relax the existing guard** that raises `"provider_route is only supported for sentinel2_backfill"` so ResourceSat provider routes are permitted; keep it rejecting `provider_route` only for `mock_sync`. | ⬜ | — |
| TASK-073 | Wire `ResourceSatIngestionService` into `src/akasha/runtime.py` with factory helpers where needed. | ⬜ | — |
| TASK-074 | Wire `ResourceSatIngestionService` into `src/akasha/api/app.py::create_app` and update `/api/v1/ingestion/sync` dispatch for `resourcesat_backfill`. | ⬜ | — |
| TASK-075 | Create `src/akasha/jobs/resourcesat_tasks.py` with a Celery entrypoint `backfill(job_id, mode)` plus **separate chained per-stage tasks for heavy work** (e.g. `prepare_scene`, `build_composite`, `generate_indices`) so CPU/I/O-heavy ResourceSat processing can be routed to the `heavy-cpu`/`cog`/`stats` queues instead of running inline on the search worker (OPS-004, RISK-007). Add optional scheduled preload tasks for each ResourceSat source. | ⬜ | — |
| TASK-076 | Update `src/akasha/jobs/celery_app.py` imports, `task_routes`, and beat schedule for the new `akasha.jobs.resourcesat_tasks.*` tasks using the **actual deployed queue names**: `search`, `download`, `maintenance`, and the processing queues `preprocess`/`cog`/`stats` (worker-process) and `heavy-cpu` (worker-heavy). Do **not** use `process` or `heavy` — those queues do not exist. Route provider search to `search`, downloads to `download`, prepare/composite/index heavy tasks to `heavy-cpu` (and/or `cog`/`stats`), and schedule checks to `maintenance`. Add a test asserting every routed queue has a consuming worker in `deploy/*.yml`. | ⬜ | — |
| TASK-077 | Add tests in `tests/test_resourcesat_ingestion_service.py` for eager execution, idempotent job creation, stage recording, metadata-only mode, full-pipeline mode with mocked provider/processor, partial failure summaries, and unsupported source rejection. | ⬜ | — |
| TASK-078 | Add tests in `tests/test_resourcesat_tasks.py` proving Celery task registration, queue route configuration, and task dispatch behavior. | ⬜ | — |
| TASK-079 | Extend `tests/test_api.py` for `/api/v1/ingestion/sync` ResourceSat request validation and response envelope behavior. | ⬜ | — |

### Implementation Phase 9 — Source-aware readiness and field analytics

- GOAL-009: Make ResourceSat queryable through the same readiness and field-index API contracts used by the product app.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-080 | Refactor `src/akasha/services/readiness.py::ReadinessService` to be source-aware, not Sentinel-only. The coupling is deeper than source/AOI gating: parametrize `source_id`/`aoi_id`/`provider_route` from a per-source readiness policy (not `sentinel2_preload_*`), generalize the `job.job_type == "sentinel2_backfill"` filter, make the `index_name`/`indexCoverage` keys come from the source's supported-index set (currently hard-coded to `"ndvi"`/`{"NDVI": ...}`), and parametrize the reason-message strings (currently hard-code "Sentinel-2 preload policy"). Keep the shared job-success contract (`processed_count`/`failed_count`, see TASK-066) intact. | ✅ | 2026-07-09 |
| TASK-081 | Add ResourceSat readiness policy configuration to `Settings`, including source ID, AOI ID, freshness window, required index coverage, and freshness reference rules. | ✅ | 2026-07-09 |
| TASK-082 | Implement ResourceSat readiness date discovery from `provider_scenes` and `raster_outputs`. Readiness must include available dates only when required ResourceSat index outputs exist. | ✅ | 2026-07-09 |
| TASK-083 | Implement deterministic ResourceSat readiness reason codes: `SOURCE_MISMATCH`, `AOI_MISMATCH`, `NO_SUCCESSFUL_RESOURCE_SAT_JOB`, `NO_RESOURCE_SAT_OUTPUTS`, `MISSING_INDEX_COVERAGE`, `RESOURCE_SAT_STALE`, `LOW_COVERAGE`, and `SOURCE_NOT_ENABLED`. | ✅ | 2026-07-09 |
| TASK-084 | Update `src/akasha/services/analytics.py::AnalyticsService.field_index` to accept ResourceSat source IDs and enforce ResourceSat source/index compatibility. This requires **contract changes**: extend `schemas.py::FieldIndexRequest` with a backward-compatible `sourceId` default of `sentinel-2-l2a` and add `NDWI_GREEN_NIR` to its `index` literal (currently `[NDVI,MSAVI,NDMI,NDBI,NDRE,RECI]`); reject source-incompatible indices (e.g. NDMI/NDBI for LISS-4, NDRE/RECI/NDBI for any ResourceSat source); remove the hard-coded `source_id="sentinel-2-l2a"` defaults and `searchedSources=["sentinel-2-l2a"]`. Product-app BFF emission of `sourceId` remains Phase 11 cutover scope because it is cross-repository. | ✅ | 2026-07-09 |
| TASK-085 | Update `AnalyticsService.field_index` selection logic so ResourceSat candidate ranking uses requested date proximity, usable pixel percentage, coverage percentage, cloud percentage, and source-specific resolution. | ✅ | 2026-07-09 |
| TASK-086 | Add quality warnings in field-index responses for LISS-4 partial coverage and AWiFS coarse resolution. These warnings must be in `FieldIndexQuality.warnings`. | ✅ | 2026-07-09 |
| TASK-087 | Ensure `AnalyticsService.overlay_for_query` and `point_for_query` work for ResourceSat derived COGs without source-specific branches except profile lookup. | ✅ | 2026-07-09 |
| TASK-088 | Add `tests/test_resourcesat_readiness_api.py` for fresh, stale, missing output, low coverage, missing index, source mismatch, AOI mismatch, and unsupported source cases. | ✅ | 2026-07-09 |
| TASK-089 | Add `tests/test_resourcesat_analytics_api.py` for LISS-3 available response, LISS-4 warning response, AWiFS coarse warning response, unsupported index rejection, unavailable response, and no internal path leakage. | ✅ | 2026-07-09 |
| TASK-090 | Add `tests/test_resourcesat_field_overlay.py` and `tests/test_resourcesat_field_point.py` for signed overlay and point routes with ResourceSat derived outputs. | ✅ | 2026-07-09 |

### Implementation Phase 10 — Unified source scheduler and approved-runtime orchestration

- GOAL-010: Provide one scheduler/orchestration layer for ResourceSat and future satellite sources in standalone ingestion.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-091 | Create `src/akasha/scheduler/source_registry.py` with typed source state rows for Phase 3 ResourceSat sources. Include lifecycle state, schedule state, capabilities, product exposure, commercial state, AOI scope, validation state, readiness reasons, validation profile, cadence class, host pool, owner, default AOIs, max downloads, min coverage, and composite window days. | ✅ | 2026-07-09 |
| TASK-092 | Create `src/akasha/scheduler/planner.py` with `plan_due_sources()` that makes no provider calls and returns deterministic due decisions for source/AOI pairs. | ✅ | 2026-07-09 |
| TASK-093 | Extend the existing `src/akasha/scheduler/locks.py` (it already exists with `advisory_lock_key()` / `try_advisory_lock_sql()`) with per-source/AOI lock helpers built on top of those advisory-lock primitives, suitable for Postgres/external runtime and in-memory tests. Reuse the existing helpers; do not duplicate them. | ✅ | 2026-07-09 |
| TASK-094 | Create `src/akasha/scheduler/orchestrator.py` with `run_source_job()` that gates disabled/manual/commercial/out-of-AOI sources, enforces approved runtime, records job/stage metadata, and dispatches to `ResourceSatIngestionService`. | ✅ | 2026-07-09 |
| TASK-095 | Implement dry-run output for ResourceSat scheduler runs. Dry-run must record planned stages and thresholds but must not call Bhoonidhi, write raw objects, process rasters, upload COGs, or mutate pgSTAC. | ✅ | 2026-07-09 |
| TASK-096 | Implement approved-runtime gate using a setting or environment variable such as `AKASHA_BHOONIDHI_APPROVED_RUNTIME=true`. Non-dry-run ResourceSat jobs must fail closed without this signal. | ✅ | 2026-07-09 |
| TASK-096a | Implement a disk-space preflight in the orchestrator/`run_source_job()` (and/or the `raw_download` stage) that checks free headroom for the raw ZIP, scratch/extract, prepared scene COGs, composite, and MinIO staging before any live capped run, and fails closed (category `deferred`/`terminal`) with a redacted reason when headroom is insufficient. Reuse existing headroom settings where applicable (e.g. `source_mirror_required_headroom_bytes`). | ✅ | 2026-07-09 |
| TASK-097 | Implement failure classification in scheduler/orchestrator consistent with `ResourceSatBackfillSummary` categories. | ✅ | 2026-07-09 |
| TASK-098 | Add scheduler observability artifacts or API response metadata that exposes redacted source/AOI/window/status/counts without raw paths or secrets. | ✅ | 2026-07-09 |
| TASK-099 | Add tests in `tests/test_resourcesat_scheduler.py` for due planning, dry-run no-provider-calls, approved-runtime gate, lock blocking, success ledger update, disabled source gate, and failure classification. | ✅ | 2026-07-09 |

### Implementation Phase 11 — Product app ResourceSat ingestion cutover

- GOAL-011: Change the product app so ResourceSat data is served through standalone ingestion after Phase 3 acceptance.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-100 | Update `akasha-em-git/apps/api/app/config.py` to represent ResourceSat ingestion-backed source configuration. Do not add a product-app native ResourceSat alternate-routing flag. | ✅ | 2026-07-09 |
| TASK-101 | Update `akasha-em-git/apps/api/app/ingestion_client.py` so readiness, field-index, signed JSON fetch, and overlay fetch are source-generic, not Sentinel-only. | ✅ | 2026-07-09 |
| TASK-102 | Update `akasha-em-git/apps/api/app/routers/product_router.py` so `resourcesat-2a-liss3-boa`, `resourcesat-2a-liss4-mx70-l2`, and `resourcesat-2a-awifs-boa` dates are resolved from standalone ingestion readiness after cutover. | ✅ | 2026-07-09 |
| TASK-103 | Update `product_router.py` source listing so ResourceSat sources that are ingestion-ready are marked `pipelineBacked=true` and expose source-specific supported indices/resolution metadata. | ✅ | 2026-07-09 |
| TASK-104 | Update `akasha-em-git/apps/api/app/routers/analytics_router.py` so ResourceSat statistics, overlay, trend, and point calls use standalone ingestion field-index responses. | ✅ | 2026-07-09 |
| TASK-105 | Remove or disable app-native ResourceSat raster processing paths **only after recorded live staging acceptance** (TASK-116–118 passed and evidence recorded in TASK-122), not merely after ingestion-backed unit tests pass — unit tests are not live acceptance (CUT-002, RISK-012). Do not leave product code that silently routes ResourceSat to native app COGs. | ⬜ | — |
| TASK-106 | Verify `akasha-em-git/apps/frontend/src/pages/MapPage.tsx` treats ResourceSat `pipelineBacked` sources generically for overlay and point behavior. Update only if tests expose source-specific Sentinel assumptions. | ✅ | 2026-07-09 |
| TASK-107 | Extend `akasha-em-git/apps/api/tests/test_pipeline_ingestion_bridge.py` with ResourceSat LISS-3, LISS-4, and AWiFS cases for config, sources, dates, stats, trend, overlay, point, and no app-native routing. | ✅ | 2026-07-09 |
| TASK-108 | Extend `akasha-em-git/apps/api/tests/test_product_sources.py` and `test_field_analytics.py` for ResourceSat ingestion-backed source metadata and unsupported index handling. | ✅ | 2026-07-09 |
| TASK-109 | Extend `akasha-em-git/apps/frontend/src/pages/MapPage.test.tsx` for ResourceSat pipeline-backed source selection, date loading, overlay loading indicator, and hover point lookup. | ✅ | 2026-07-09 |

### Implementation Phase 12 — Deployment and staging validation

- GOAL-012: Deploy ResourceSat Phase 3 to staging and validate end-to-end before declaring cutover complete.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-110 | Update `deploy/docker-compose.yml` and `deploy/compose.staging.yml` so API, scheduler, and workers receive ResourceSat settings, scratch/data mounts, GDAL cache settings, Bhoonidhi secrets, and approved-runtime settings. | ⬜ | — |
| TASK-111 | Update `.github/workflows/ci.yml` if new ResourceSat tests, dependencies, or migration checks require additional setup. | ⬜ | — |
| TASK-112 | Update `.github/workflows/deploy-staging.yml` if new worker queues or services must be pulled/recreated for ResourceSat tasks. | ⬜ | — |
| TASK-113 | Confirm staging data root keeps raw downloads, work files, prepared COGs, composites, and validation outputs under `/srv/akasha` or configured `AKASHA_` ingestion root. | ⬜ | — |
| TASK-114 | Deploy `akasha-ingestion` Phase 3 to staging with Bhoonidhi credentials configured only on the staging ingestion host. | ⬜ | — |
| TASK-115 | Run dry-run plan for all three ResourceSat sources and confirm no provider download, no raster processing, no object upload, and no pgSTAC mutation occurred. | ⬜ | — |
| TASK-116 | Run a capped live LISS-3 job with low `max_downloads`, then verify raw object, prepared scene COGs, composite, derived index COGs, pgSTAC item, readiness, stats, overlay, and point. | ⬜ | — |
| TASK-117 | Run a capped live LISS-4 job with low `max_downloads`, then verify partial/narrow-swath coverage warnings and supported-index-only outputs. | ⬜ | — |
| TASK-118 | Run a capped live AWiFS job with low `max_downloads`, then verify coarse/regional warnings and supported-index-only outputs. | ⬜ | — |
| TASK-119 | Deploy product app cutover changes after standalone ResourceSat readiness is available for at least LISS-3 and validation has been recorded for LISS-4/AWiFS behavior. | ⬜ | — |
| TASK-120 | Validate product app source listing, ResourceSat dates, ResourceSat field stats, ResourceSat field-clipped overlay, ResourceSat point lookup, and ResourceSat trend through the app domain only. | ⬜ | — |
| TASK-121 | Inspect API response bodies and headers for leakage. Assert no `tileUrl`, `statsUrl`, `overlayUrl`, `pointUrl`, `layerId`, `sig`, `kid`, `exp`, `s3://`, `minio`, `10.10.`, Bhoonidhi URL, or API key appears in browser-visible JSON. | ⬜ | — |
| TASK-122 | Record final staging acceptance evidence in `docs/phase-3-resourcesat-bhoonidhi-acceptance.md`: source, AOI, date window, product IDs, job IDs, output dates, coverage, resolution, validation fields, and test results. | ⬜ | — |

## 3. Alternatives

- **ALT-001**: Import `akasha-em-git/services/ingestion/akasha_ingest` directly into `akasha-ingestion`. Rejected because it couples the standalone ingestion platform to the product app repository and violates repo ownership boundaries.
- **ALT-002**: Keep ResourceSat native in the product app and add only Sentinel-2 to standalone ingestion. Rejected because the requested architecture is a unified orchestration layer for all satellite types.
- **ALT-003**: Implement only LISS-3 in Phase 3 and defer LISS-4/AWiFS. Rejected by scope decision; Phase 3 must include LISS-3, LISS-4, and AWiFS together.
- **ALT-004**: Build a product-app native alternate route during cutover. Rejected by scope decision; this is development migration activity, and successful validation before cutover is the required safety mechanism.
- **ALT-005**: Implement Bhuvan along with Bhoonidhi. Rejected by scope decision; Phase 3 targets Bhoonidhi/NRSC only.
- **ALT-006**: Generate only analytic/mask assets and compute indices on-demand for every field request. Rejected for this phase because existing standalone ingestion analytics expects indexed `RasterOutputRecord` rows and signed overlay/point routes against derived index COGs.
- **ALT-007**: Expose ingestion signed URLs directly to the product frontend. Rejected because browser traffic must only call the product app origin and must not see ingestion internals.
- **ALT-008**: Deliver all three sources as one undifferentiated build. **Partially accepted as a sequencing recommendation**: keep the Phase 3 scope (LISS-3 + LISS-4 + AWiFS) but sequence delivery as an internal 3a/3b split — 3a proves the LISS-3 vertical end-to-end on staging (provider → prepare → composite → indices → readiness → field-index → cutover), and 3b adds LISS-4/AWiFS plus the unified scheduler (Phase 10), gated on 3a staging acceptance. This de-risks the 122-task scope without reintroducing an app-native path (ALT-003/ALT-004 remain rejected).

## 4. Dependencies

- **DEP-001**: Existing `akasha-ingestion` Phase 2 foundation: FastAPI app, Celery, Redis, Postgres/PostGIS, pgSTAC, MinIO object store, stage store, backfill repository, raster output repository, tile layer repository, and signed field-index routes.
- **DEP-002**: Existing Sentinel-2 field-index implementation in `src/akasha/services/sentinel2_ingestion.py` and `src/akasha/services/analytics.py` as implementation pattern.
- **DEP-003**: Bhoonidhi credentials configured on `akasha-staging` only.
- **DEP-004**: Provider-whitelisted staging egress for Bhoonidhi/NRSC access.
- **DEP-005**: Raster dependencies available in API/worker images: `rasterio`, GDAL, PROJ, `rio-cogeo`, `numpy`, `shapely`, and `pyproj` where required.
- **DEP-006**: MinIO bucket configured by `Settings.minio_bucket` and reachable from workers.
- **DEP-007**: Postgres/PostGIS/pgSTAC migrations applied before ResourceSat live run.
- **DEP-008**: Product app BFF configured with `INGESTION_API_URL` and `INGESTION_API_KEY` pointing to standalone ingestion.
- **DEP-009**: Existing app ResourceSat behavior in `akasha-em-git` used as reference material:
  - `services/ingestion/akasha_ingest/bhoonidhi.py`
  - `services/ingestion/akasha_ingest/resourcesat_pipeline.py`
  - `services/ingestion/akasha_ingest/composite.py`
  - `services/ingestion/akasha_ingest/source_registry.py`
  - `services/ingestion/akasha_ingest/validation_profiles.py`
  - `tests/test_resourcesat_composite.py`
  - `tests/test_prepare_resourcesat_liss3_boa_cogs.py`
  - `tests/test_phase7_bhoonidhi_scheduler.py`
- **DEP-010**: Staging storage capacity for raw ZIPs, prepared scene COGs, composites, derived index COGs, and validation artifacts.
- **DEP-011**: Existing GitHub Actions mirror/deploy flow for `nishanthturnstile/akasha-ingestion` to the client staging deployment.

## 5. Files

- **FILE-001**: `docs/impl-plan/data-resourcesat-bhoonidhi-ingestion-pipeline-1.md` — this implementation plan.
- **FILE-002**: `docs/phase-3-resourcesat-bhoonidhi-acceptance.md` — staging acceptance record to be created during implementation.
- **FILE-003**: `src/akasha/config.py` — Bhoonidhi, ResourceSat, scheduler, scratch, and approved-runtime settings.
- **FILE-004**: `src/akasha/schemas.py` — `SyncRequest` extension for `resourcesat_backfill`; possible source-aware readiness/analytics model refinements.
- **FILE-005**: `src/akasha/api/app.py` — ResourceSat service wiring and sync dispatch.
- **FILE-006**: `src/akasha/runtime.py` — ResourceSat factory wiring.
- **FILE-007**: `src/akasha/providers/bhoonidhi.py` — new Bhoonidhi provider adapter.
- **FILE-008**: `src/akasha/providers/contracts.py` — provider contract extensions if shared DTOs are required.
- **FILE-009**: `src/akasha/processing/resourcesat.py` — ResourceSat profiles, masks, reflectance, and source/index matrix.
- **FILE-010**: `src/akasha/processing/resourcesat_prepare.py` — ResourceSat ZIP/product-to-scene preparation.
- **FILE-011**: `src/akasha/processing/resourcesat_composite.py` — ResourceSat scene alignment, compositing, and verification.
- **FILE-012**: `src/akasha/processing/indices.py` — ResourceSat source-aware index support and unsupported-index rejection.
- **FILE-013**: `src/akasha/storage/object_store.py` — raw ZIP and prepared COG object methods.
- **FILE-014**: `src/akasha/catalog/seed.py` — ResourceSat source seed state.
- **FILE-015**: `src/akasha/catalog/seed_db.py` — ResourceSat provider routes, execution policies, profiles, and AOI/source metadata.
- **FILE-016**: `src/akasha/catalog/pgstac_repository.py` — ResourceSat STAC item builder and collection constants.
- **FILE-017**: `src/akasha/catalog/asset_repository.py` — reuse or extend scene asset metadata persistence if needed.
- **FILE-018**: `src/akasha/catalog/scene_repository.py` — reuse or extend provider scene query methods if needed.
- **FILE-019**: `src/akasha/catalog/raster_repository.py` — reuse or extend ResourceSat derived output queries if needed.
- **FILE-020**: `src/akasha/services/resourcesat_ingestion.py` — new ResourceSat orchestration service.
- **FILE-021**: `src/akasha/services/readiness.py` — refactor from Sentinel-only to source-aware readiness.
- **FILE-022**: `src/akasha/services/analytics.py` — ResourceSat field-index selection, quality, overlay, point, and unsupported-index handling.
- **FILE-023**: `src/akasha/jobs/resourcesat_tasks.py` — ResourceSat Celery tasks.
- **FILE-024**: `src/akasha/jobs/celery_app.py` — ResourceSat task routes and schedule entries.
- **FILE-025**: `src/akasha/jobs/idempotency.py` — ResourceSat idempotency keys.
- **FILE-026**: `src/akasha/scheduler/source_registry.py` — ResourceSat source-state registry for unified orchestration.
- **FILE-027**: `src/akasha/scheduler/planner.py` — due-source planning.
- **FILE-028**: `src/akasha/scheduler/locks.py` — source/AOI locks.
- **FILE-029**: `src/akasha/scheduler/orchestrator.py` — approved-runtime source job execution.
- **FILE-030**: `migrations/versions/0003_resourcesat_bhoonidhi_phase3.py` — create only if schema changes beyond seed/profile data are required. If created, it must set `down_revision = "0002_phase2_s2_slice"` (the revision id, not the filename). Audit early whether GEO-012 mask-method/version provenance fits existing `provider_scenes`/`scene_assets`/`raster_outputs`/`backfill_runs` columns; if new columns are required, this migration is mandatory under DB-001.
- **FILE-031**: `deploy/docker-compose.yml` — queue/env/mount updates.
- **FILE-032**: `deploy/compose.staging.yml` — staging-specific ResourceSat settings and mounts.
- **FILE-033**: `.github/workflows/ci.yml` — ResourceSat test/dependency/migration CI updates if needed.
- **FILE-034**: `.github/workflows/deploy-staging.yml` — worker queue/service deployment updates if needed.
- **FILE-035**: `akasha-em-git/apps/api/app/config.py` — product app ResourceSat ingestion-backed configuration after cutover.
- **FILE-036**: `akasha-em-git/apps/api/app/ingestion_client.py` — source-generic ingestion client behavior.
- **FILE-037**: `akasha-em-git/apps/api/app/routers/product_router.py` — ResourceSat source/date readiness from ingestion.
- **FILE-038**: `akasha-em-git/apps/api/app/routers/analytics_router.py` — ResourceSat stats/overlay/trend/point through ingestion.
- **FILE-039**: `akasha-em-git/apps/frontend/src/pages/MapPage.tsx` — verify source-generic pipeline-backed UI behavior.

## 6. Testing

- **TEST-001**: Run `python -m pytest tests/test_phase3_resourcesat_foundation.py -q` to validate ResourceSat seed settings, provider routes, execution policy, and profile seeds.
- **TEST-002**: Run `python -m pytest tests/test_bhoonidhi_provider.py -q` to validate provider auth, search, pagination, download, retry, and redaction behavior.
- **TEST-003**: Run `python -m pytest tests/test_resourcesat_profiles.py tests/test_resourcesat_indices.py -q` to validate source profiles, band order, mask rules, and source/index compatibility.
- **TEST-004**: Run `python -m pytest tests/test_resourcesat_prepare.py -q` to validate ResourceSat product preparation and manifest generation.
- **TEST-005**: Run `python -m pytest tests/test_resourcesat_composite.py -q` to validate composite behavior, coverage checks, source-specific resolutions, and provenance.
- **TEST-006**: Run `python -m pytest tests/test_resourcesat_pgstac.py tests/test_resourcesat_raster_outputs.py -q` to validate STAC, raster output, and tile layer registration.
- **TEST-007**: Run `python -m pytest tests/test_resourcesat_ingestion_service.py tests/test_resourcesat_tasks.py -q` to validate ResourceSat orchestration and Celery task behavior.
- **TEST-008**: Run `python -m pytest tests/test_resourcesat_readiness_api.py tests/test_resourcesat_analytics_api.py -q` to validate readiness and field-index API behavior.
- **TEST-009**: Run `python -m pytest tests/test_resourcesat_field_overlay.py tests/test_resourcesat_field_point.py -q` to validate signed overlay and point routes for ResourceSat.
- **TEST-010**: Run `python -m pytest tests/test_resourcesat_scheduler.py -q` to validate due planning, dry-run, approved-runtime, locks, and failure classification.
- **TEST-011**: Run the full standalone ingestion suite with `python -m pytest -q` from `akasha-ingestion`.
- **TEST-012**: Run standalone ingestion lint with `ruff check .` from `akasha-ingestion`.
- **TEST-013**: Run app backend tests after cutover with `python -m pytest tests/test_pipeline_ingestion_bridge.py tests/test_field_analytics.py tests/test_product_sources.py -q` from `akasha-em-git/apps/api`.
- **TEST-014**: Run app frontend ResourceSat pipeline-backed tests with `yarn test src/pages/MapPage.test.tsx src/components/map/FieldOverlayLoadingIndicator.test.tsx` from `akasha-em-git/apps/frontend`.
- **TEST-015**: Execute staging dry-run for each ResourceSat source and verify no provider download, no object upload, no raster processing, and no pgSTAC mutation occurred.
- **TEST-016**: Execute capped live staging LISS-3 run and verify job status, stage rows, raw object, prepared scene COGs, composite, derived index outputs, pgSTAC item, readiness, field-index stats, overlay, and point.
- **TEST-017**: Execute capped live staging LISS-4 run and verify job status, partial/narrow-swath quality warnings, supported index outputs only, readiness, and field-index behavior.
- **TEST-018**: Execute capped live staging AWiFS run and verify job status, coarse/regional quality warnings, supported index outputs only, readiness, and field-index behavior.
- **TEST-019**: Execute product app staging cutover smoke: source list, dates, field stats, overlay, point lookup, and trend for ResourceSat through the app domain only.
- **TEST-020**: Execute leakage audit on browser-visible JSON and headers. Assert absence of `tileUrl`, `statsUrl`, `overlayUrl`, `pointUrl`, `layerId`, `sig`, `kid`, `exp`, `s3://`, MinIO identifiers, internal IPs, Bhoonidhi URLs, and API keys.

## 7. Risks & Assumptions

- **RISK-001**: Bhoonidhi authentication may fail because of active-session limits, token expiry, credential rotation, or provider-side changes. Mitigation: implement token refresh, logout hygiene, explicit failure category `provider_auth`, and no secret logging.
- **RISK-002**: Bhoonidhi search/download may return transient `412`, `429`, or `5xx` responses. Mitigation: implement bounded retries, exponential backoff, provider-specific failure categories, and per-run caps.
- **RISK-003**: Raw ResourceSat products may differ from currently observed product layouts. Mitigation: make preparation fail closed on missing required bands/metadata and record `invalid_product` with product ID and redacted detail.
- **RISK-004**: ResourceSat mask v1 is provisional. Mitigation: store mask method/version on every output and response so future mask versions can coexist.
- **RISK-005**: LISS-4 narrow swath may not cover the AOI. Mitigation: record coverage percentages and quality warnings; do not present LISS-4 as full-AOI coverage.
- **RISK-006**: AWiFS resolution is coarse for field-level analytics. Mitigation: record coarse/regional warnings in readiness and field-index quality responses.
- **RISK-007**: ResourceSat processing can create high disk I/O and CPU pressure. Mitigation: bounded live runs, one source/AOI lock, queue separation, scratch/data root enforcement, and staging acceptance with small `max_downloads` first.
- **RISK-008**: ResourceSat app cutover can expose hidden Sentinel assumptions in frontend or BFF code. Mitigation: source-generic app bridge tests for all three ResourceSat sources before cutover.
- **RISK-009**: pgSTAC item generation can leak internal hrefs through app-visible responses. Mitigation: keep STAC hrefs internal and add leakage tests against app-visible JSON/headers.
- **RISK-010**: Schema changes may introduce table locks or invalid constraints. Mitigation: reuse existing Phase 2 tables where possible and use safe Alembic patterns when new constraints/indexes are required.
- **RISK-011**: AOI naming mismatch can break readiness or app requests. Mitigation: explicitly map ingestion AOI and app/operator AOI identifiers in config and tests.
- **RISK-012**: Incomplete migration before cutover would make ResourceSat unavailable in the development environment. Mitigation: do not cut over until Phase 3 staging acceptance passes; fix standalone ingestion if acceptance fails.
- **RISK-013**: Readiness may silently never report `AVAILABLE` for ResourceSat if `ResourceSatBackfillSummary` field names diverge from the `processed_count`/`failed_count` keys that `readiness._is_output_producing_full_pipeline` reads. Mitigation: a shared summary→metadata contract (TASK-066) plus a readiness test that drives a completed ResourceSat job to `AVAILABLE`.
- **RISK-014**: Misconfigured Celery routing (non-existent queue names such as `process`/`heavy`) would silently strand ResourceSat tasks with no consuming worker. Mitigation: use the real deployed queue names, add a test asserting every routed queue has a worker in `deploy/*.yml`, and smoke-check queue consumption on staging.
- **RISK-015**: The default `scratch_dir=/tmp/akasha` can exhaust the OS disk and has previously wedged SSH/the Azure VM Agent under raster I/O pressure. Mitigation: OPS-007 fail-closed data-root preflight (TASK-010a) plus explicit `/srv/akasha` mounts on staging (TASK-110/113).
- **ASSUMPTION-001**: Bhoonidhi credentials and staging egress remain available on `akasha-staging`.
- **ASSUMPTION-002**: Existing `akasha-ingestion` Phase 2 tables are sufficient for most Phase 3 state; new schema is added only when existing tables cannot represent required ResourceSat state.
- **ASSUMPTION-003**: Existing product app field analytics endpoints can remain stable while their backend implementation changes from app-native ResourceSat to ingestion-backed ResourceSat.
- **ASSUMPTION-004**: Existing ResourceSat app implementation and tests represent valid behavior references, but implementation must be rewritten inside `akasha-ingestion`.
- **ASSUMPTION-005**: Operators accept that this is a development migration and not a high-availability production failover exercise.

## 8. Related Specifications / Further Reading

- `AGENTS.md`
- `README.md`
- `docs/akasha-ingestion-plan.md`
- `docs/architecture-technical-stack.md`
- `docs/implementation-roadmap.md`
- `docs/phase-2-sentinel-2-vertical-slice-implementation-plan.md`
- `docs/phase-0/provider-access-validation.md`
- `docs/reference/satellite-catalog.md`
- `src/akasha/services/sentinel2_ingestion.py`
- `src/akasha/services/analytics.py`
- `src/akasha/services/readiness.py`
- `src/akasha/catalog/seed_db.py`
- `akasha-em-git/AGENTS.md`
- `akasha-em-git/docs/data-ingestion-and-satellite-rules.md`
- `akasha-em-git/docs/staging-ingestion-developer-guide.md`
- `akasha-em-git/docs/satellite-ingestion-orchestration-and-scheduler.md`
- `akasha-em-git/services/ingestion/akasha_ingest/bhoonidhi.py`
- `akasha-em-git/services/ingestion/akasha_ingest/resourcesat_pipeline.py`
- `akasha-em-git/services/ingestion/akasha_ingest/composite.py`
- `akasha-em-git/services/ingestion/akasha_ingest/source_registry.py`
- `akasha-em-git/services/ingestion/akasha_ingest/validation_profiles.py`
- `akasha-em-git/tests/test_resourcesat_composite.py`
- `akasha-em-git/tests/test_prepare_resourcesat_liss3_boa_cogs.py`
- `akasha-em-git/tests/test_phase7_bhoonidhi_scheduler.py`
