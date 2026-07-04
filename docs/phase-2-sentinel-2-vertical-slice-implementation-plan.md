# Phase 2 Sentinel-2 Vertical Slice Implementation Plan

Generated: 2026-07-02

## 1. Purpose

Phase 2 proves the first real end-to-end optical ingestion, processing, cataloging, serving, and
field analytics pipeline for Akasha Ingestion using **Element84 Earth Search STAC and AWS-hosted
Sentinel-2 L2A Cloud Optimized GeoTIFFs (COGs)** as the primary provider path.

This document replaces the earlier CDSE-first Phase 2 plan. CDSE/Copernicus is no longer a Phase 2
entry blocker. Official provider APIs remain useful as future or source-specific fallback routes,
but Phase 2 implementation starts with Earth Search STAC metadata and AWS-hosted COG assets.

## 2. Phase 2 goal

Build the Sentinel-2 L2A vertical slice:

```text
Earth Search STAC search
  -> STAC item and asset normalization
  -> source STAC manifest storage
  -> AOI-complete source COG mirroring into MinIO
  -> SCL mask and source-aware reflectance conversion
  -> vegetation-index calculation
  -> Akasha-derived index COG generation
  -> Postgres + pgSTAC catalog registration
  -> TiTiler-PgSTAC private serving
  -> field-index API with signed tile/stat URLs, statistics, quality, and provenance
```

Phase 2 is complete when at least one real field polygon can return a Sentinel-2 index tile and
statistics end to end, and the six-month Bangalore Sentinel-2 backfill is operational through the
Element84/AWS COG path.

## 3. Entry gate and current blocker state

### 3.1 Entry gate

Phase 2 can start when:

1. Phase 1 foundation is available.
2. Bangalore AOI and sample fields exist.
3. Earth Search public STAC search is reachable from the development/runtime environment.
4. Workers can read HTTPS COGs and write mirrored source COGs plus derived COGs to MinIO.
5. Operators approve object-store headroom for AOI-complete source COG mirroring.

### 3.2 Current reality

Current Phase 0 and planning status:

- Bangalore AOI and sample fields exist in `docs/phase-0/bangalore-aoi.geojson`.
- Earth Search v1 is reachable at `https://earth-search.aws.element84.com/v1`.
- Earth Search collection `sentinel-2-l2a` returns COG-backed Sentinel-2 L2A items for the
  Bangalore AOI/window without CDSE credentials.
- Earth Search collection `landsat-c2-l2` exists for Landsat Collection 2 Level-2 fallback and
  continuity work.
- Sentinel-1 GRD is available in Earth Search as `sentinel-1-grd`, but it is SAR data and is not an
  NDVI/NDRE/RECI fallback.
- CDSE credentials are no longer required for the Phase 2 primary path.

The implementation must therefore split:

- **offline acceptance**: mocked Earth Search STAC responses, synthetic STAC items, synthetic COGs,
  and local MinIO/pgSTAC/TiTiler tests;
- **live acceptance**: opt-in Earth Search STAC search and HTTPS COG read tests over a safe AOI/date
  window;
- **optional fallback acceptance**: requester-pays Landsat or official-provider checks only when
  the operator explicitly enables those routes.

## 4. Source documents and due diligence

This plan is derived from:

- `docs/akasha-ingestion-plan.md`
- `docs/architecture-technical-stack.md`
- `docs/implementation-roadmap.md`
- `docs/phase-1-core-platform-foundation-implementation-plan.md`
- `docs/phase-0/provider-access-validation.md`
- `docs/phase-0/phase0-status.md`
- Current Phase 1 code under `src/akasha/`
- Earth Search v1 documentation and live STAC samples
- AWS Open Data Registry pages for Sentinel-2 L2A COGs and USGS Landsat

Primary public references:

- Earth Search API: `https://earth-search.aws.element84.com/v1`
- Earth Search project: `https://github.com/Element84/earth-search`
- Sentinel-2 L2A COG AWS Registry: `https://registry.opendata.aws/sentinel-2-l2a-cogs/`
- USGS Landsat AWS Registry: `https://registry.opendata.aws/usgs-landsat/`

Due diligence findings:

- Earth Search v1 indexes datasets hosted on AWS Open Data and exposes STAC API search.
- Earth Search does not provide a public production SLA; Akasha must cache source STAC metadata and
  mirror accepted source COG assets for reproducibility.
- `sentinel-2-l2a` returns assets such as `blue`, `green`, `red`, `nir`, `nir08`, `rededge1`,
  `swir16`, `swir22`, `scl`, `granule_metadata`, and `tileinfo_metadata`.
- Sentinel-2 COG assets expose `raster:bands.scale`, `raster:bands.offset`, and nodata metadata.
  The implementation must use per-asset STAC metadata instead of hard-coded CDSE SAFE formulas.
- `landsat-c2-l2` covers Landsat 4/5/7/8/9 Collection 2 Level-2 products. Landsat 8/9 are current
  continuity/fallback sources; Landsat 5/7 are historical-only for Akasha's current product.
- Landsat does not have red-edge bands, so it cannot provide NDRE or RECI.
- Landsat assets can involve `s3://` requester-pays access. Requester-pays must be opt-in and
  explicitly configured before Landsat fallback live tests run.

## 5. Current implementation baseline

### 5.1 Application baseline

The codebase currently provides a Phase 1 foundation:

- FastAPI app factory in `src/akasha/api/app.py`
- manual dependency wiring through `src/akasha/runtime.py`
- in-memory and Postgres job stores
- in-memory and MinIO object stores
- static and database source catalog implementations
- mock provider and mock ingestion service
- Celery app with one registered mock sync task
- API key authentication and response envelope models

### 5.2 Current Phase 1 limitations relevant to Phase 2

- `SyncRequest.job_type` currently only allows `mock_sync`.
- `create_app()` wires `MockIngestionService` and `MockProvider`.
- Celery only registers `akasha.jobs.tasks.mock_sync`.
- `Job` and `JobResponse` model one object output through `asset_ref`.
- `InMemoryObjectStore` and `MinIOObjectStore` only support whole-byte raw package writes.
- The raw mock object path ends in `original.mock`.
- Runtime dependencies do not include STAC, raster, or COG processing libraries.
- Generic TiTiler is deployed, but Phase 2 serving requires constrained TiTiler-PgSTAC with no
  client-supplied arbitrary COG URLs.

### 5.3 Current database baseline

Migration `0001_core_platform` already creates:

- `akasha.satellite_sources`
- `akasha.source_credentials`
- `akasha.provider_execution_policies`
- `akasha.aoi_registry`
- `akasha.provider_scenes`
- `akasha.provider_orders`
- `akasha.scene_assets`
- `akasha.processing_jobs`
- `akasha.raster_outputs`
- `akasha.tile_layers`
- `akasha.audit_logs`

Phase 2 must add provider routes, external/mirrored source asset support, profile tables, analytics
tables, stage checkpoints, and backfill summaries.

Important migration blocker:

- `scene_assets.object_path` is currently `NOT NULL`.
- Earth Search source assets need `asset_href` before mirroring and `object_path` after mirroring.
- Phase 2 migration must drop `object_path` `NOT NULL`, add external asset columns, and enforce a
  location constraint.

## 6. Non-negotiable implementation conventions

Phase 2 must preserve the existing project conventions:

- Add `from __future__ import annotations` at the top of new Python modules.
- Keep manual dependency injection through factories and `app.state`; do not add a DI framework.
- Preserve the `RuntimeBackend.MEMORY` and `RuntimeBackend.EXTERNAL` split.
- Use paired duck-typed implementations when memory and external backends both need behavior.
- Do not introduce ABCs for provider/store/repository interfaces.
- Use raw SQL through `sqlalchemy.text()` for runtime Postgres queries.
- Keep API responses wrapped in `APIResponse[T]`.
- Never return MinIO paths, object keys, Earth Search hrefs, or AWS hrefs in external API responses.
- Keep secrets in `SecretStr`, environment variables, or secret references; never store plaintext
  credentials in the database or docs.
- Preserve Phase 1 mock sync behavior and tests.
- Failed idempotent jobs must remain retryable.

## 7. Architecture decisions locked for Phase 2

### 7.1 Provider strategy

Decision: **Element84 Earth Search + AWS-hosted COGs are the Phase 2 primary provider path.**

Provider roles:

| Source | Provider route | Role | Phase 2 behavior |
| --- | --- | --- | --- |
| `sentinel-2-l2a` | `earthsearch:sentinel-2-l2a` | primary | MVP Sentinel-2 optical path |
| `sentinel-2-l2a` | `cdse:sentinel-2-l2a` | optional future fallback | Not in Phase 2 critical path |
| `landsat-c2-l2` | `earthsearch:landsat-c2-l2` | secondary/fallback | Landsat 8/9 NDVI/NDMI/NDBI/NBR only |
| `landsat-c2-l2` | `usgs:landsat-c2-l2` | official fallback | Opt-in when USGS access is configured |
| `sentinel-1-grd` | `earthsearch:sentinel-1-grd` | future | SAR track only; not optical indices |

CDSE-specific OAuth, OData search, SAFE ZIP download, JP2 parsing, and SAFE archive hardening are
deferred unless the team explicitly adds a future Sentinel-2 official fallback.

### 7.2 AOI-complete source COG mirroring

Decision: **Accepted acquisitions mirror all source COG assets needed for complete AOI coverage into
MinIO before normal processing continues.**

Rules:

- Store the original STAC item JSON and a normalized asset manifest for every accepted item.
- Mirror every source COG asset required to process supported indices and masks for the configured
  AOI or accepted field-processing footprint.
- "Complete AOI" includes same-date multi-tile coverage where the AOI/field crosses MGRS tiles.
- Prefer AOI-clipped source mirrors when technically safe and provenance-complete.
- If clipping would harm reproducibility, mirror the full source COG and record the reason.
- Keep the original external href, selected access path, checksum/ETag where available, source size,
  mirror object path, and mirror checksum.
- For AOI-clipped source mirrors, write provenance metadata with the original href and alternates,
  source item/asset metadata, original checksum/ETag/size where available, clip geometry, source
  CRS, pixel window/bounds, output transform, resolution, resampling/no-resampling decision,
  processing buffer, clipped-object checksum, and the reason clipping was safe.
- Do not mirror JP2 alternates by default.
- Do not silently skip source mirroring to save storage; fail preflight or require an explicit
  operator-approved policy.

This replaces the previous "retain full SAFE ZIP" raw-retention design with source COG retention
that is appropriate for the Element84/AWS COG path.

### 7.3 Private tile serving

Decision: **Use TiTiler-PgSTAC behind an API-signed layer resolver.**

The API returns opaque `layerId` and `queryId` references with short-lived HMAC signatures. The
API-side resolver maps those references to pgSTAC items/assets server-side. TiTiler-PgSTAC serves
only constrained pgSTAC-backed Akasha-derived assets.

Concrete serving contract:

- Public tile URL shape:

  ```text
  /tiles/{layerId}/{z}/{x}/{y}.png?op=tile&exp={unix_ts}&kid={key_id}&sig={hmac}
  ```

- Public stats URL shape:

  ```text
  /api/v1/analytics/field-index/{queryId}?op=stats&exp={unix_ts}&kid={key_id}&sig={hmac}
  ```

- Public routes are handled by the API/Caddy resolver path, not by native TiTiler-PgSTAC routes.
- The resolver validates the signature, expiry, operation, and layer/query scope before resolving
  the backing pgSTAC collection/item/asset.
- TiTiler-PgSTAC is attached only to the internal Docker network.
- Caddy must deny all native TiTiler-PgSTAC collection/item/search tile routes from the public edge,
  including `/collections/*`, `/items/*`, `/searches/*`, `/tilejson*`, and any route that accepts a
  client-supplied COG URL.
- Tests must prove a raw native TiTiler-PgSTAC item/collection/search tile request is rejected at
  the edge.

HMAC canonical string:

```text
v1
{method}
{operation}
{layer_id_or_query_id}
{path_template_or_stats}
{expiry_unix_seconds}
{geometry_or_query_hash}
```

Signature requirements:

- `exp` is required and must be short-lived.
- `op` must match the requested operation (`tile` or `stats`).
- `kid` supports key rotation.
- expired signatures return 401.
- invalid signatures return 401.
- valid signatures for the wrong operation or resource return 403.
- missing backing layer/query records return 404 without disclosing internal paths.

Client-facing URLs must not include `s3://`, MinIO endpoint, bucket, raw object path, direct object
key, Earth Search URL, or AWS Open Data URL.

### 7.4 Mandatory pgSTAC/STAC registration

Phase 2 must register processed outputs as STAC items/assets in local pgSTAC unless the Phase 2 exit
gate is explicitly changed.

Phase 2 conventions:

- collection: `akasha-sentinel-2-l2a-derived-v1`
- item ID: `s2-l2a-{mgrs_tile_or_group}-{acquisition_yyyymmddThhmmss}-{product_hash}`
- asset keys: `ndvi`, `msavi`, `ndmi`, `ndbi`, `ndre`, `reci`, `scl-mask`, `metadata`
- asset hrefs: internal MinIO/S3 hrefs visible only to Postgres, API workers, and TiTiler-PgSTAC on
  the internal network; external API responses must never emit these hrefs.
- `provider_scenes.pgstac_item_id` stores the item ID.
- `raster_outputs.metadata` stores the pgSTAC asset key and collection.
- `tile_layers.layer_id` maps the public opaque layer ID to a `raster_output_id`.
- STAC JSON is constructed/validated with `pystac`.
- pgSTAC insertion uses `pypgstac` against the installed pgSTAC version.

Local pgSTAC serves Akasha-derived index COGs. Source STAC item metadata is retained for lineage and
reproducibility, not exposed as direct public tile targets.

### 7.5 Durable stage and retry model

The existing single-row `processing_jobs` model is not enough for Phase 2 multi-stage processing.

Phase 2 needs durable stage tracking for:

- search;
- manifest storage;
- source asset registration;
- source COG mirroring;
- preprocessing;
- index calculation;
- COG generation;
- COG validation;
- pgSTAC/catalog registration;
- field statistics;
- backfill summary.

The model must support:

- parent backfill job;
- per-scene/acquisition work;
- per-source-asset mirror work;
- per-index work;
- stage attempts;
- stage statuses;
- error codes;
- timestamps;
- metadata;
- retry/resume;
- deterministic output uniqueness;
- recovery from partial DB/object-store success.

### 7.6 Credential resolver and source gating

Earth Search Sentinel-2 primary search/read does not require Sentinel-2 credentials. The credential
resolver remains necessary for:

- ResourceSat/Bhoonidhi;
- USGS/M2M fallback;
- Landsat requester-pays or private AWS credentials if enabled;
- optional future CDSE fallback;
- future Earthdata or commercial providers.

Rules:

- `source_credentials.secret_ref` is the durable database reference.
- `source_credentials.status` records validation and rotation state.
- `Settings` can provide secret-backed local/dev values through `SecretStr`.
- Provider adapters receive resolved credentials only when their route requires them.
- Missing optional fallback credentials gate that route only; they must not block the Earth Search
  Sentinel-2 primary route.
- Credential validation jobs should gate source/provider-route activation and scheduled processing.

## 8. Proposed package structure

Add modules under the existing layered layout:

```text
src/akasha/
  providers/
    contracts.py
    earthsearch.py
    usgs.py
  processing/
    stac_assets.py
    sentinel2.py
    landsat.py
    masks.py
    indices.py
    cog.py
    geometry.py
    mosaics.py
  catalog/
    source_route_repository.py
    scene_repository.py
    asset_repository.py
    raster_repository.py
    profile_repository.py
    field_query_repository.py
    pgstac_repository.py
  services/
    sentinel2_ingestion.py
    source_mirroring.py
    analytics.py
    credentials.py
    signing.py
  jobs/
    sentinel2_tasks.py
    stage_store.py
  api/
    app.py
```

This is illustrative, not a rigid file-by-file mandate. Final names should follow existing
patterns, preserve runtime factory wiring, and keep responsibilities clear.

## 9. Runtime dependencies and settings

### 9.1 Dependencies

Add runtime dependencies deliberately:

| Purpose | Candidate dependency |
| --- | --- |
| HTTP client | `httpx` |
| STAC search | `pystac-client` |
| STAC objects/validation | `pystac` |
| Array math | `numpy` |
| Raster IO and COG reads | `rasterio` |
| COG creation/validation | `rio-cogeo` |
| Geometry | `shapely` |
| CRS/projection | `pyproj` |
| pgSTAC loading | `pypgstac` |

Container images must include GDAL support for:

- `/vsicurl/` HTTPS range reads;
- local/MinIO S3-compatible COG reads;
- optional S3 requester-pays access for Landsat fallback;
- COG creation and validation.

OpenJPEG/JP2 support is no longer required for the Phase 2 primary Sentinel-2 path, but it can be
added later for optional official-provider fallback.

### 9.2 Settings

Add settings with the `AKASHA_` prefix:

| Setting | Purpose |
| --- | --- |
| `AKASHA_EARTHSEARCH_API_URL` | Default `https://earth-search.aws.element84.com/v1` |
| `AKASHA_EARTHSEARCH_TIMEOUT_SECONDS` | STAC search/read timeout |
| `AKASHA_EARTHSEARCH_PAGE_SIZE` | STAC search page size |
| `AKASHA_SOURCE_MIRROR_MODE` | `aoi_clipped` default, `full_asset` fallback |
| `AKASHA_SOURCE_MIRROR_MAX_BYTES_PER_RUN` | Backfill mirror preflight cap |
| `AKASHA_SOURCE_MIRROR_REQUIRED_HEADROOM_BYTES` | Required MinIO headroom |
| `AKASHA_ENABLE_LANDSAT_REQUESTER_PAYS` | Opt-in flag for requester-pays Landsat route |
| `AKASHA_AWS_REQUEST_PAYER` | `requester` when requester-pays is enabled |
| `AKASHA_AWS_REGION` | Region for S3-backed provider assets when needed |
| `AKASHA_SIGNING_SECRET` | HMAC secret for signed layer/stat URLs |
| `AKASHA_SIGNED_URL_TTL_SECONDS` | Tile/stat URL TTL |
| `AKASHA_SCRATCH_DIR` | Worker scratch root |
| `AKASHA_GDAL_CACHEMAX_MB` | GDAL cache tuning |
| `AKASHA_FIELD_MAX_VERTICES` | Geometry validation cap |
| `AKASHA_FIELD_MAX_AREA_SQ_KM` | Geometry validation cap |
| `AKASHA_FIELD_MIN_USABLE_PIXELS` | Field statistics quality floor |
| `AKASHA_MAX_CANDIDATE_SCENES` | Cap for synchronous field-index candidate evaluation |
| `AKASHA_FIELD_USABLE_PIXEL_THRESHOLD` | Default `0.80` |
| `AKASHA_FIELD_MAX_CLOUD_PERCENTAGE` | Default `20` |
| `AKASHA_SENTINEL2_PROFILE_VERSION` | Sentinel-2 processing profile version |
| `AKASHA_SELECTION_POLICY_VERSION` | Field best-scene selection version |
| `AKASHA_LIVE_PROVIDER_TESTS` | Opt-in live provider tests |

Secret settings must use `SecretStr` where appropriate. Requester-pays and official fallback
credentials must be opt-in and route-scoped.

## 10. Database migration plan

Add migration `0002_phase2_sentinel2_vertical_slice.py`.

The migration must use explicit column types, nullability, defaults, foreign keys, check
constraints, and indexes. Runtime code should upsert deterministic outputs with `ON CONFLICT`
rather than inserting duplicate rows during retries.

### 10.1 Add provider routes

Add `akasha.source_provider_routes` so logical satellite sources can have multiple provider routes
without duplicating the source registry.

Fields:

- `id uuid PRIMARY KEY DEFAULT gen_random_uuid()`
- `source_id text NOT NULL REFERENCES akasha.satellite_sources(source_id)`
- `provider_adapter text NOT NULL`
- `provider_collection text NOT NULL`
- `provider_priority integer NOT NULL`
- `provider_role text NOT NULL`
- `status text NOT NULL DEFAULT 'inactive'`
- `access_mode text NOT NULL`
- `execution_policy_ref text REFERENCES akasha.provider_execution_policies(policy_key)`
- `license_profile text`
- `metadata jsonb NOT NULL DEFAULT '{}'::jsonb`
- `created_at timestamptz NOT NULL DEFAULT now()`
- `updated_at timestamptz NOT NULL DEFAULT now()`

Constraints/indexes:

- unique `(source_id, provider_adapter, provider_collection)`
- index `(source_id, status, provider_priority)`
- `CHECK (provider_role IN ('primary', 'secondary', 'fallback', 'future'))`
- `CHECK (status IN ('inactive', 'manual_only', 'active', 'blocked', 'deprecated'))`
- `CHECK (access_mode IN ('public_https', 'requester_pays_s3', 'official_api', 'authenticated_download'))`

`execution_policy_ref` must reference `provider_execution_policies`; do not duplicate rate-limit
policy fields in the route table.

### 10.2 Extend existing tables

Potential extensions:

- `processing_jobs.parent_job_id`
- `processing_jobs.scene_id`
- `processing_jobs.execution_policy_version`
- `provider_scenes.aoi_id`
- `provider_scenes.provider_route_id`
- `provider_scenes.logical_scene_key`
- `provider_scenes.native_crs`
- `provider_scenes.native_resolution`
- `provider_scenes.coverage_percentage`
- `provider_scenes.file_size_bytes`
- `provider_scenes.raw_object_path`
- `raster_outputs.dtype`
- `raster_outputs.scale_factor`
- `raster_outputs.offset`
- `raster_outputs.nodata_value`
- `raster_outputs.min_value`
- `raster_outputs.max_value`
- `raster_outputs.native_resolution`
- `raster_outputs.processing_resolution`
- `raster_outputs.display_resolution`
- `raster_outputs.crs`
- `raster_outputs.cloud_mask_version`

Do not add columns blindly if equivalent metadata can be safely kept in JSONB. Prefer columns for
query-critical fields and JSONB for provider-specific detail.

### 10.3 Extend `akasha.scene_assets`

Phase 2 needs source assets that start as external hrefs and become mirrored MinIO objects.

Required changes:

- `ALTER COLUMN object_path DROP NOT NULL`
- add `asset_href text`
- add `storage_backend text NOT NULL DEFAULT 'minio'`
- add `storage_region text`
- add `requester_pays boolean NOT NULL DEFAULT false`
- add `asset_key text`
- add `scale numeric`
- add `offset numeric`
- add `nodata_value numeric`
- add `roles text[]`
- add `media_type text`
- add `mirror_status text NOT NULL DEFAULT 'not_required'`
- add `mirror_object_path text`
- add `mirror_checksum_sha256 text`
- add `selected_access_mode text`

Location constraint:

```text
CHECK (object_path IS NOT NULL OR asset_href IS NOT NULL OR mirror_object_path IS NOT NULL)
```

Recommended stricter operational rule:

- source asset rows may have both `asset_href` and `mirror_object_path`;
- derived output rows must have local `object_path` and must not rely on an external `asset_href`.

Asset normalization must parse both:

- the default STAC asset `href`;
- any `alternate` hrefs, especially S3 alternates.

The selected access path must be deliberate and recorded as `selected_access_mode` and
`storage_backend`.

### 10.4 New tables

#### `akasha.processing_job_stages`

Tracks durable stage attempts.

Fields:

- `id uuid PRIMARY KEY DEFAULT gen_random_uuid()`
- `job_id uuid NOT NULL REFERENCES akasha.processing_jobs(id) ON DELETE CASCADE`
- `stage_name text NOT NULL`
- `attempt integer NOT NULL CHECK (attempt > 0)`
- `status text NOT NULL DEFAULT 'pending'`
- `error_code text`
- `error_message text`
- `lease_owner text`
- `lease_expires_at timestamptz`
- `metadata jsonb NOT NULL DEFAULT '{}'::jsonb`
- `started_at timestamptz`
- `completed_at timestamptz`
- `created_at timestamptz NOT NULL DEFAULT now()`
- `updated_at timestamptz NOT NULL DEFAULT now()`

Constraints/indexes:

- `CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped', 'retrying', 'cancelled'))`
- unique `(job_id, stage_name, attempt)`
- index `(job_id, stage_name, status)`
- index `(status, lease_expires_at)`

Valid transitions:

```text
pending -> running -> completed
pending -> running -> failed -> retrying -> running
pending -> skipped
running -> cancelled
```

Lease rules:

- only one non-expired `running` lease is allowed per `(job_id, stage_name)`;
- expired leases may be reclaimed by a retry worker;
- reclaiming a lease must create a new attempt or explicitly mark the stale attempt failed.

#### `akasha.backfill_runs`

Stores six-month backfill summaries.

Fields:

- `id uuid PRIMARY KEY DEFAULT gen_random_uuid()`
- `job_id uuid NOT NULL REFERENCES akasha.processing_jobs(id) ON DELETE CASCADE`
- `source_id text NOT NULL`
- `aoi_id text NOT NULL`
- `date_start date NOT NULL`
- `date_end date NOT NULL`
- `status text NOT NULL DEFAULT 'running'`
- `searched_count integer NOT NULL DEFAULT 0`
- `accepted_count integer NOT NULL DEFAULT 0`
- `mirrored_asset_count integer NOT NULL DEFAULT 0`
- `skipped_count integer NOT NULL DEFAULT 0`
- `processed_count integer NOT NULL DEFAULT 0`
- `failed_count integer NOT NULL DEFAULT 0`
- `retryable_failed_count integer NOT NULL DEFAULT 0`
- `terminal_failed_count integer NOT NULL DEFAULT 0`
- `estimated_source_mirror_bytes bigint`
- `actual_source_mirror_bytes bigint`
- `summary_json jsonb NOT NULL DEFAULT '{}'::jsonb`
- `started_at timestamptz`
- `completed_at timestamptz`
- `created_at timestamptz NOT NULL DEFAULT now()`
- `updated_at timestamptz NOT NULL DEFAULT now()`

Constraints/indexes:

- `CHECK (status IN ('running', 'completed', 'failed', 'partial'))`
- unique `(source_id, aoi_id, date_start, date_end, job_id)`
- index `(source_id, aoi_id, date_start, date_end, status)`

#### `akasha.visualization_profiles`

Stores display profile versions.

Fields:

- `id uuid PRIMARY KEY DEFAULT gen_random_uuid()`
- `index_name text NOT NULL`
- `value_domain_min numeric NOT NULL`
- `value_domain_max numeric NOT NULL`
- `display_min numeric NOT NULL`
- `display_max numeric NOT NULL`
- `palette_json jsonb NOT NULL`
- `nodata_color text NOT NULL DEFAULT 'transparent'`
- `version text NOT NULL`
- `is_default boolean NOT NULL DEFAULT false`
- `created_at timestamptz NOT NULL DEFAULT now()`
- `updated_at timestamptz NOT NULL DEFAULT now()`

Constraints:

- unique `(index_name, version)`

#### `akasha.threshold_profiles`

Stores class ranges and labels.

Fields:

- `id uuid PRIMARY KEY DEFAULT gen_random_uuid()`
- `profile_key text NOT NULL`
- `index_name text NOT NULL`
- `crop text`
- `season text`
- `aoi_id text`
- `source_id text`
- `classes_json jsonb NOT NULL`
- `is_default boolean NOT NULL DEFAULT false`
- `version text NOT NULL`
- `created_at timestamptz NOT NULL DEFAULT now()`
- `updated_at timestamptz NOT NULL DEFAULT now()`

Constraints/indexes:

- unique `profile_key`
- index `(index_name, crop, season, aoi_id, source_id, is_default)`

#### `akasha.field_queries`

Stores field-index query results and provenance.

Fields:

- `id uuid PRIMARY KEY DEFAULT gen_random_uuid()`
- `query_id text NOT NULL UNIQUE`
- `field_geometry geometry(Geometry, 4326) NOT NULL`
- `crs text NOT NULL DEFAULT 'EPSG:4326'`
- `index_name text NOT NULL`
- `requested_date date NOT NULL`
- `selected_scene_id uuid REFERENCES akasha.provider_scenes(id) ON DELETE SET NULL`
- `raster_output_id uuid REFERENCES akasha.raster_outputs(id) ON DELETE SET NULL`
- `layer_id text REFERENCES akasha.tile_layers(layer_id) ON DELETE SET NULL`
- `valid_pixel_count integer NOT NULL DEFAULT 0`
- `selection_reason text NOT NULL`
- `stats_json jsonb NOT NULL DEFAULT '{}'::jsonb`
- `class_area_json jsonb NOT NULL DEFAULT '[]'::jsonb`
- `quality_json jsonb NOT NULL DEFAULT '{}'::jsonb`
- `visualization_profile_id uuid REFERENCES akasha.visualization_profiles(id) ON DELETE SET NULL`
- `threshold_profile_id uuid REFERENCES akasha.threshold_profiles(id) ON DELETE SET NULL`
- `expires_at timestamptz`
- `created_at timestamptz NOT NULL DEFAULT now()`

Indexes:

- GIST on `field_geometry`
- index `(index_name, requested_date, created_at)`

Retention:

- field geometries can be location-sensitive;
- do not log full coordinates;
- default cached `field_queries` retention should be 30 days unless configured otherwise;
- longer retention requires explicit operator/business approval.

### 10.5 Deterministic output uniqueness

Add a unique constraint or unique index on deterministic raster output identity. Recommended:

```text
(scene_id, output_kind, index_name, formula_version, processing_profile_version, processing_resolution)
```

Use `ON CONFLICT` upserts for retries. This enforces the acceptance requirement that retries do not
create duplicate deterministic output rows.

### 10.6 Tile layer constraint

`tile_layers` must support only approved references. If mosaics are introduced later, enforce
exactly one backing reference. For Phase 2, prefer `raster_output_id` backed by pgSTAC item/asset
metadata.

## 11. Source registry and seed updates

Update Sentinel-2 seed metadata:

- source ID: `sentinel-2-l2a`
- logical source provider adapter: `earthsearch`
- instrument mode: `MSI`
- analysis level: `L2A`
- schedule state: `manual-only` until live validation passes
- product exposure: `internal_qa` until live validation passes
- supported indices: `ndvi`, `msavi`, `ndmi`, `ndbi`, `ndre`, `reci`
- processing profile version: `sentinel2-l2a-earthsearch-v1`
- validation profile: STAC asset validation, source COG mirror validation, SCL mask validation,
  field-index response validation
- license profile: internal/private serving, attribution if required
- execution policy reference: `earthsearch-default`

Seed provider routes:

| Source ID | Provider route | Role | Access mode | Status |
| --- | --- | --- | --- | --- |
| `sentinel-2-l2a` | `earthsearch:sentinel-2-l2a` | primary | `public_https` | `manual_only` |
| `sentinel-2-l2a` | `cdse:sentinel-2-l2a` | fallback | `authenticated_download` | `inactive` |
| `landsat-c2-l2` | `earthsearch:landsat-c2-l2` | secondary | `requester_pays_s3` or `public_https` | `inactive` |
| `landsat-c2-l2` | `usgs:landsat-c2-l2` | fallback | `official_api` | `inactive` |
| `sentinel-1-grd` | `earthsearch:sentinel-1-grd` | future | `public_https` | `inactive` |

Seed Earth Search execution policy defaults:

- no credentials for Sentinel-2 primary route;
- conservative search concurrency;
- provider retry/backoff policy;
- source mirror concurrency;
- public API availability error mapping;
- version: `phase2-earthsearch-v1`.

Seed default NDVI visualization and threshold profiles.

## 12. Provider adapter plan

### 12.1 Provider DTOs

Introduce lightweight dataclasses or Pydantic models for:

- provider capability descriptor;
- provider route;
- provider scene;
- provider asset;
- normalized STAC item metadata;
- normalized asset manifest;
- mirror request/result;
- provider error category.

Keep duck typing; do not introduce ABCs.

### 12.2 Earth Search adapter behavior

`EarthSearchProvider` responsibilities:

1. Validate Earth Search root endpoint and expected collections.
2. Search STAC `/search` by collection, AOI, date range, pagination, and optional cloud filtering.
3. Normalize item metadata:
   - STAC item ID;
   - logical scene key;
   - acquisition timestamp;
   - platform/instrument;
   - MGRS tile for Sentinel-2 or path/row for Landsat;
   - footprint;
   - cloud percentage from `eo:cloud_cover`;
   - assets and roles.
4. Normalize asset metadata:
   - asset key;
   - href;
   - alternate hrefs;
   - media type;
   - roles;
   - band/common name;
   - raster scale;
   - raster offset;
   - nodata;
   - spatial resolution;
   - storage backend/access mode.
5. Store redacted provider responses and error categories.
6. Never require CDSE credentials for the Sentinel-2 primary route.

### 12.3 Earth Search STAC search contract

Endpoint:

```text
POST https://earth-search.aws.element84.com/v1/search
```

Search request shape:

```json
{
  "collections": ["sentinel-2-l2a"],
  "bbox": [77.023647, 12.537266, 78.131561, 13.61645],
  "datetime": "2026-01-01T00:00:00Z/2026-06-30T23:59:59Z",
  "limit": 100
}
```

Rules:

- Use `intersects` when the AOI polygon is available and `bbox` only for broad search envelopes.
- Use returned `links` for pagination; do not assume one page is complete.
- Rank/filter by `eo:cloud_cover`, but compute field usable pixels from SCL/masks later.
- Start with simple STAC API search fields. Add server-side `query` or CQL2 `filter` only after a
  compatibility spike confirms Earth Search accepts the syntax needed by Akasha.
- Treat provider 429/5xx/timeouts as provider availability/rate errors, not processing errors.

Error mapping examples:

| Provider condition | Akasha error code |
| --- | --- |
| Earth Search unavailable or timeout | `provider_sla_unavailable` |
| 429 or quota-like throttling | `source_rate_limited` |
| STAC item missing required asset | `asset_unavailable` |
| Asset metadata missing required scale or unusable QA/mask metadata | `asset_metadata_invalid` |
| External COG read failed during mirroring | `external_asset_read_failed` |
| Source COG mirror write/checksum failed | `source_mirror_failed` |
| Unexpected metadata shape | `metadata_failed` |

### 12.4 Provider tests

Offline tests:

- root/collection validation;
- STAC search response normalization;
- pagination;
- no-scene search result;
- provider 429/5xx retry classification;
- missing required assets;
- alternate href parsing;
- scale/offset/nodata extraction;
- external href redaction from API responses;
- source mirror manifest creation.

Live tests:

- skipped unless `AKASHA_LIVE_PROVIDER_TESTS=true`;
- Earth Search root is reachable;
- AOI/date search returns Sentinel-2 L2A items;
- one required Sentinel-2 COG asset can be range-read;
- one SCL COG asset can be range-read;
- source mirror stage writes to MinIO and validates checksum for a small AOI/window.

## 13. Object lake plan

Use these zones:

```text
raw/earthsearch/{source_id}/{stac_item_id}/stac-item.json
raw/earthsearch/{source_id}/{stac_item_id}/asset-manifest.json
raw/earthsearch/{source_id}/{stac_item_id}/source-cogs/{asset_key}.tif
raw/earthsearch/{source_id}/{stac_item_id}/source-cogs/{asset_key}.metadata.json
indices/earthsearch/{source_id}/{stac_item_id}/{index}.cog.tif
qa/earthsearch/{source_id}/{stac_item_id}/...
analytics/{query_id}/...
tmp/
```

### 13.1 Storage methods

Add matching memory and MinIO methods for:

- STAC item JSON write/read;
- asset manifest write/read;
- streaming source COG mirror upload;
- mirror checksum sidecar write;
- QA artifact write;
- COG write;
- analytics artifact write;
- object existence;
- object stat;
- object read or file download for processing.

### 13.2 Resource controls

Requirements:

- Estimate source mirror size before starting a backfill.
- Verify MinIO headroom for mirrored source COGs and derived COGs.
- Do not load full COGs into memory.
- Use windowed reads and bounded scratch files.
- Compute checksum during mirror streaming where possible.
- Clean scratch files on success and failure.
- Quarantine or delete partial objects according to explicit policy.
- Record cleanup and failure state in job stages.
- Leave raw/source COG lifecycle cleanup disabled unless explicitly approved.

## 14. STAC item and asset parser plan

The parser must support real Earth Search STAC items, not only synthetic layouts.

### 14.1 Required item metadata

Parse:

- STAC item ID;
- collection;
- acquisition datetime;
- platform;
- constellation;
- instrument;
- MGRS tile or path/row;
- scene footprint;
- cloud percentage;
- item properties;
- asset list;
- item links and provider provenance.

### 14.2 Required asset metadata

Parse for every required asset:

- asset key;
- href and alternates;
- media type;
- roles;
- band/common name;
- raster scale;
- raster offset;
- nodata;
- spatial resolution;
- asset CRS, transform, width, and height as read by rasterio/GDAL;
- checksum/ETag where available.

### 14.3 Sentinel-2 asset role mapping

Earth Search Sentinel-2 default Phase 2 mapping:

| Earth Search asset | Role | Nominal resolution |
| --- | --- | --- |
| `blue` | blue | 10 m |
| `green` | green | 10 m |
| `red` | red | 10 m |
| `rededge1` | red edge | 20 m |
| `nir` | wide NIR | 10 m |
| `nir08` | narrow NIR | 20 m |
| `swir16` | SWIR | 20 m |
| `swir22` | SWIR | 20 m |
| `scl` | scene classification | 20 m |

Phase 2 index profiles must use exact asset choices:

- NDVI: `nir` and `red` at 10 m.
- MSAVI: `nir` and `red` at 10 m.
- NDMI: `nir08` and `swir16` at 20 m.
- NDBI: `swir16` and `nir08` at 20 m.
- NDRE: `nir08` and `rededge1` at 20 m.
- RECI: `nir08` and `rededge1` at 20 m.

Alternate red-edge and SWIR variants require new formula/profile versions.

### 14.4 Radiometric scale/offset requirement

For the Earth Search COG path, reflectance conversion must use per-asset STAC metadata:

```text
reflectance = DN * scale + offset
```

It is not sufficient to divide DN by 10000. It is also wrong to apply the CDSE SAFE
`BOA_ADD_OFFSET` formula to Earth Search COGs, because Earth Search assets already publish the
needed scale/offset metadata through STAC.

If STAC omits `offset`, treat it as `0.0`. Missing or implausible `scale` is invalid. Missing
asset-level nodata is not automatically invalid if GDAL masks and the required SCL/QA asset provide
a reliable validity mask.

Nodata ordering:

1. Read STAC/GDAL nodata and quality masks.
2. Build the validity mask from nodata, SCL/QA, saturation/defect indicators, and finite-value
   checks.
3. Apply scale/offset only to valid pixels.
4. Do not let DN `0` plus a negative offset become a valid negative reflectance pixel.

Tests must include scale/offset fixtures that prove the conversion is applied exactly once.

### 14.5 CRS, windows, and mosaics

STAC search geometry is EPSG:4326, but COG assets are in native UTM or path/row grids.

Requirements:

- Transform the AOI/field geometry into each asset CRS before computing read windows.
- Define a target processing grid per output profile:
  - Sentinel-2 10 m for NDVI/MSAVI;
  - Sentinel-2 20 m for NDMI/NDBI/NDRE/RECI;
  - Landsat 30 m for supported fallback indices.
- If a field/AOI crosses MGRS tiles or same-date item boundaries, merge same-date asset windows into
  a transient mosaic before stats or index COG generation.
- Phase 2 exit does not require persistent AOI date mosaics, but it does require complete coverage
  semantics: boundary fields must either use same-date grouping/mosaicking or return a clear
  `UNAVAILABLE` reason such as `multi_tile_field_not_supported`; partial-field statistics are not
  acceptable.

## 15. Preprocessing and mask plan

### 15.1 Sentinel-2 L2A profile

- Use vendor L2A surface reflectance metadata from Earth Search COG assets.
- Do not run custom atmospheric correction.
- Preserve source STAC item JSON, asset manifest, and mirrored source COGs.
- Use AOI/source processing profile to select CRS.
- For Bangalore Phase 2, use EPSG:32643 where suitable.
- Do not hard-code EPSG:32643 globally.

### 15.2 Resolution strategy

| Output | Default processing resolution |
| --- | --- |
| NDVI | 10 m |
| MSAVI | 10 m |
| NDMI | 20 m |
| NDBI | 20 m |
| NDRE | 20 m |
| RECI | 20 m |

Any resampling must be explicit in the processing profile.

### 15.3 Resampling rules

| Raster type | Resampling |
| --- | --- |
| Reflectance bands | bilinear/average as profile-configured |
| Index COG overviews | average where appropriate |
| Cloud masks | nearest only |
| SCL/classes | nearest only |
| Nodata masks | nearest only |

Never use bilinear/cubic resampling for categorical masks.

### 15.4 SCL invalid classes

Default Sentinel-2 SCL code handling for Phase 2 vegetation/index analytics:

| SCL code | Class | Phase 2 handling |
| --- | --- | --- |
| 0 | No data | invalid |
| 1 | Saturated or defective | invalid |
| 2 | Dark area pixels / topographic shadows | invalid |
| 3 | Cloud shadows | invalid |
| 4 | Vegetation | valid |
| 5 | Bare soils | valid |
| 6 | Water | valid scientific pixel, but interpreted as non-vegetation |
| 7 | Unclassified | invalid |
| 8 | Cloud medium probability | invalid |
| 9 | Cloud high probability | invalid |
| 10 | Thin cirrus | invalid |
| 11 | Snow or ice | invalid for Phase 2 optical vegetation indices |

Snow/ice handling is index-family-dependent long term, but for Phase 2 field vegetation analytics it
is invalid.

### 15.5 Landsat fallback mask note

Landsat fallback uses `qa_pixel`, not SCL. Landsat 8/9 can provide NDVI, NDMI, NDBI, and NBR at 30
m, but cannot provide NDRE or RECI.

### 15.6 Field usable pixels

Field usable-pixel percentage is field-specific and must be computed at query time for candidate
scene or acquisition groups. Scene-level cloud percentage is only a prefilter.

## 16. Index engine plan

### 16.1 Index formulas and default Sentinel-2 assets

| Index | Formula | Sentinel-2 assets | Default output resolution |
| --- | --- | --- | --- |
| NDVI | `(nir - red) / (nir + red)` | `nir`, `red` | 10 m |
| MSAVI | `(2*nir + 1 - sqrt((2*nir + 1)^2 - 8*(nir - red))) / 2` | `nir`, `red` | 10 m |
| NDMI | `(nir08 - swir16) / (nir08 + swir16)` | `nir08`, `swir16` | 20 m |
| NDBI | `(swir16 - nir08) / (swir16 + nir08)` | `swir16`, `nir08` | 20 m |
| NDRE | `(nir08 - rededge1) / (nir08 + rededge1)` | `nir08`, `rededge1` | 20 m |
| RECI | `(nir08 / rededge1) - 1` | `nir08`, `rededge1` | 20 m |

Rationale:

- `nir` is used only for 10 m NDVI/MSAVI in Phase 2.
- `nir08` is used for 20 m NDMI, NDBI, NDRE, and RECI to stay spectrally and spatially consistent
  with 20 m SWIR/red-edge bands.
- `rededge1` is the default red-edge asset for NDRE/RECI in Phase 2.
- Alternate red-edge or SWIR variants require new formula/profile versions.

### 16.2 Output contract

Phase 2 output contracts:

| Index | Formula version | Output dtype | Scale factor | Nodata | Clipping policy | API/stat units |
| --- | --- | --- | --- | --- | --- | --- |
| NDVI | `ndvi-s2-v1` | Int16 | 10000 | -32768 | clip to `[-1, 1]` after formula | unscaled index value |
| MSAVI | `msavi-s2-v1` | Int16 | 10000 | -32768 | clip to `[-1, 1]` after formula | unscaled index value |
| NDMI | `ndmi-s2-v1` | Int16 | 10000 | -32768 | clip to `[-1, 1]` after formula | unscaled index value |
| NDBI | `ndbi-s2-v1` | Int16 | 10000 | -32768 | clip to `[-1, 1]` after formula | unscaled index value |
| NDRE | `ndre-s2-v1` | Int16 | 10000 | -32768 | clip to `[-1, 1]` after formula | unscaled index value |
| RECI | `reci-s2-v1` | Float32 | none | -9999.0 | no hard clip; mask non-finite and physically invalid values | raw RECI value |

RECI uses Float32 because it is a ratio-minus-one index and is not bounded to `[-1, 1]`. Display and
threshold profiles must declare the RECI visualization domain separately from the scientific output
values.

### 16.3 Validation rules

- Reject unsupported source/index combinations.
- Convert scaled and offset reflectance before math.
- Propagate nodata, cloud, shadow, saturation, and invalid masks.
- Handle divide-by-zero explicitly.
- For RECI, apply a denominator/reflectance floor or documented physical-validity guard so very
  small nonzero `rededge1` values do not produce enormous finite but meaningless statistics.
- Record formula version in metadata and `raster_outputs`.
- Emit quality warnings when a Landsat fallback is used for lower-resolution supported indices.

## 17. COG and pgSTAC registration plan

### 17.1 COG scope

Decision: Phase 2 uses AOI-clipped source mirrors where safe and AOI-clipped per-scene/acquisition
per-index derived COGs for the Bangalore AOI to keep single-VM storage and compute bounded.

Full-source COG mirrors are allowed when clipping would weaken reproducibility or provenance.
Full-tile derived index COG generation is deferred unless live validation proves AOI-clipped derived
COGs are insufficient.

Fields near MGRS tile boundaries:

- Field-index selection must evaluate same-date acquisition groups, not only isolated scenes.
- If a field intersects multiple MGRS tiles for the same acquisition date, Phase 2 should build a
  transient pgSTAC search/mosaic view over the matching AOI-clipped derived COGs.
- Do not create persistent AOI date mosaics in Phase 2 unless needed for acceptance.
- Any field statistics returned to users must cover the complete requested field/processing
  footprint after same-date grouping; otherwise return `UNAVAILABLE` with a coverage-specific reason.

### 17.2 COG standard

Each generated COG must include:

- explicit nodata;
- compression, preferably ZSTD or DEFLATE;
- internal tiling, preferably 512 x 512;
- internal overviews;
- internal mask/alpha where appropriate;
- CRS and transform;
- native and processing resolution;
- source/product identifiers;
- formula version;
- processing profile version;
- cloud mask version;
- checksum.

Validate with `rio cogeo validate` or library equivalent.

### 17.3 Registration

For every accepted item/acquisition:

1. Insert or update `provider_scenes`.
2. Insert `scene_assets` for external source assets.
3. Store source STAC item JSON and normalized asset manifest.
4. Mirror required source COGs to MinIO.
5. Update `scene_assets` with mirror object paths/checksums.
6. Generate derived COGs.
7. Insert `raster_outputs`.
8. Register pgSTAC collection/item/assets for derived outputs.
9. Store `provider_scenes.pgstac_item_id`.
10. Insert or resolve `tile_layers.layer_id`.

## 18. Job orchestration and idempotency plan

### 18.1 Job graph

```text
backfill job
  -> search stage
  -> scene/acquisition jobs
       -> manifest stage
       -> source asset registration stage
       -> source mirror stage
       -> preprocess stage
       -> per-index jobs
            -> index stage
            -> cog stage
            -> validation stage
            -> registration stage
  -> backfill summary stage
```

### 18.2 Idempotency keys

Do not change the existing mock sync key behavior.

Add separate helpers for:

- umbrella backfill key;
- per-scene/acquisition key;
- per-source-asset mirror key;
- per-index output key;
- field-query cache key if needed.

Include:

- source ID;
- provider route ID;
- AOI ID;
- date range;
- STAC item ID;
- logical scene key;
- job type;
- stage or output kind;
- request params version;
- processing profile version;
- formula version;
- selection policy version where relevant.

### 18.3 Failure categories

Use explicit error codes:

- `provider_sla_unavailable`
- `source_rate_limited`
- `stac_search_failed`
- `asset_unavailable`
- `asset_metadata_invalid`
- `external_asset_read_failed`
- `source_mirror_failed`
- `metadata_failed`
- `processing_failed`
- `cog_validation_failed`
- `registration_failed`
- `no_scene_available`
- `license_blocked`
- `resource_exhausted`

Keep `download_failed`, `checksum_failed`, and `auth_failed` for official provider fallback routes
that perform authenticated package downloads.

### 18.4 Retry and recovery

Retries must handle:

- external asset row exists but mirror object is missing;
- mirror object exists but DB mirror status is absent;
- derived COG exists but validation status is absent;
- pgSTAC registration failed after COG creation;
- duplicate active job attempt;
- failed job retried with the same logical key;
- partial scratch files after worker failure.

## 19. Field-index API plan

### 19.1 Endpoint

```text
POST /api/v1/analytics/field-index
```

External endpoint; requires `X-API-Key`.

### 19.2 Request shape

```json
{
  "geometry": {
    "type": "Polygon",
    "coordinates": []
  },
  "crs": "EPSG:4326",
  "index": "NDVI",
  "date": "2026-06-30",
  "fallbackPolicy": "nearest_valid_scene",
  "maxCloudPercentage": 20
}
```

### 19.3 Validation

Validate:

- authentication;
- CRS is EPSG:4326;
- polygon/multipolygon type;
- geometry vertex count;
- geometry area;
- date;
- index support;
- fallback policy;
- max cloud percentage;
- AOI/source constraints.

### 19.4 Best-scene selection v1

Rules:

1. Candidate date window is requested date +/- 7 days.
2. Filter to active/internal-validated Sentinel-2 source rows that support the requested index.
3. Group same-date scenes by acquisition date and MGRS coverage when the field intersects multiple
   tiles.
4. Filter to scene or acquisition groups whose footprint covers the complete field-processing
   footprint after same-date grouping.
5. Compute field usable pixels from selected COG/mask over candidate scenes or acquisition groups.
6. Require configured usable-pixel percentage and minimum valid-pixel count.
7. Rank by source priority, lower field cloud percentage, suitable/native resolution, and nearest
   date.
8. Return `UNAVAILABLE` if no valid scene exists.

Do not silently widen the window, interpolate, or substitute SAR. Landsat fallback must be explicit,
limited to Landsat-supported indices, and returned with resolution/source quality warnings.

### 19.5 Successful response shape

```json
{
  "success": true,
  "data": {
    "status": "AVAILABLE",
    "queryId": "opaque-query-id",
    "fieldId": "field_123",
    "index": "NDVI",
    "requestedDate": "2026-06-30",
    "selectedSceneDate": "2026-06-28",
    "source": "sentinel-2-l2a",
    "providerRoute": "earthsearch:sentinel-2-l2a",
    "resolution": {
      "nativeMeters": 10,
      "processingMeters": 10,
      "displayMeters": 10
    },
    "layerId": "opaque-layer-id",
    "tileUrl": "https://example.test/tiles/opaque-layer-id/{z}/{x}/{y}.png?op=tile&exp=...&sig=...",
    "statsUrl": "https://example.test/api/v1/analytics/field-index/opaque-query-id?op=stats&exp=...&sig=...",
    "selection": {
      "windowDays": 7,
      "rule": "quality_first",
      "validPixelCount": 1840
    },
    "statistics": {
      "min": 0.21,
      "max": 0.78,
      "mean": 0.54,
      "median": 0.56,
      "stdDev": 0.09,
      "usablePixelPercentage": 92.4,
      "cloudPercentage": 7.6
    },
    "classStatistics": [
      {
        "class": "Healthy crop",
        "valueRange": [0.6, 0.75],
        "areaSqM": 8420.5,
        "areaPercentage": 48.2
      }
    ],
    "visualization": {
      "displayProfile": "ndvi-default-v1",
      "thresholdProfile": "ndvi-generic-v1",
      "legend": []
    },
    "versions": {
      "atmosphericCorrection": "vendor-l2a",
      "cloudMask": "scl-v1",
      "formula": "ndvi-v1",
      "displayProfile": "ndvi-default-v1",
      "thresholdProfile": "ndvi-generic-v1"
    },
    "quality": {
      "status": "GOOD",
      "reason": "Field cloud cover within threshold",
      "warnings": []
    }
  },
  "error": null
}
```

### 19.6 Unavailable response shape

```json
{
  "success": true,
  "data": {
    "status": "UNAVAILABLE",
    "index": "NDVI",
    "requestedDate": "2026-08-15",
    "reason": "No optical scene with field usable-pixels >= 80% within +/- 7 days",
    "searchedSources": ["sentinel-2-l2a"]
  },
  "error": null
}
```

`UNAVAILABLE` is a successful domain response and should return HTTP 200. Validation errors remain
HTTP 422. Authentication errors remain HTTP 401/503 according to the existing auth behavior.

`fieldId` is optional and caller-supplied when the caller has one. `queryId` is always generated by
Akasha and returned separately from `statsUrl`.

### 19.7 Synchronous stats guardrails

The API should cap candidate scene evaluation. If the field/query exceeds safe synchronous limits,
return a clear unavailable/deferred response according to the approved product behavior instead of
overloading the API process.

Class-area calculations must use an area-preserving projected CRS, not raw EPSG:4326 degrees.

## 20. Six-month Bangalore backfill plan

### 20.1 Scope

Backfill:

- source: `sentinel-2-l2a`
- primary provider route: `earthsearch:sentinel-2-l2a`
- AOI: Bangalore 60 km AOI from seeded `aoi_registry`
- default date range: `2026-01-01` to `2026-06-30`
- products: Earth Search Sentinel-2 L2A STAC items and AWS-hosted COG assets
- expected MGRS tiles from prior discovery include `T43PHQ`, `T43PGR`, and `T43PGQ`; live Earth
  Search results remain the source of truth.

### 20.2 Trigger and status contract

Backfill trigger:

```text
POST /api/v1/ingestion/sync
```

Phase 2 extends the sync request model with:

```json
{
  "source_id": "sentinel-2-l2a",
  "provider_route": "earthsearch:sentinel-2-l2a",
  "aoi_id": "bangalore_60km_geodesic_aoi",
  "date_start": "2026-01-01",
  "date_end": "2026-06-30",
  "job_type": "sentinel2_backfill",
  "mode": "full_pipeline"
}
```

Rules:

- The endpoint remains authenticated.
- The response uses `APIResponse[JobResponse]`.
- `JobResponse.asset_ref` is null for multi-output backfill jobs.
- `GET /api/v1/jobs/{jobId}` returns job state without internal object paths or external hrefs.
- Backfill summary is returned through job `result_metadata` and `backfill_runs`, not as raw object
  paths.
- An operator CLI may wrap the same service for local operations, but the API contract above is the
  canonical Phase 2 trigger.

### 20.3 Behavior

1. Create a durable backfill job.
2. Preflight MinIO headroom for source mirrors plus derived outputs.
3. Search Earth Search by AOI/date.
4. Dedupe by STAC item ID and logical scene key.
5. Create or reuse scene records.
6. Store STAC item JSON and asset manifest.
7. Register source asset hrefs and selected access paths.
8. Mirror AOI-complete source COG assets into MinIO.
9. Preprocess and mask from mirrored source COGs.
10. Generate configured index COGs.
11. Validate and register outputs.
12. Produce `backfill_runs` summary.

### 20.4 Summary requirements

The backfill summary must include:

- searched items;
- accepted items;
- skipped items and reasons;
- mirrored source asset count;
- estimated and actual source mirror bytes;
- processed outputs;
- failed items/assets by error category;
- retryable vs terminal failures;
- STAC item IDs;
- logical scene keys;
- mirror checksums;
- processing profile versions;
- formula versions;
- start/end timestamps.

### 20.5 Bounded default limits

Initial configurable defaults:

| Limit | Default |
| --- | --- |
| Earth Search page size | 100 |
| Backfill search item cap | 1000 |
| Max concurrent Earth Search searches | 1 |
| Max concurrent source COG mirrors | 1 |
| Max source mirror bytes per run | operator-configured |
| Required free object-store headroom before six-month backfill | operator-configured, checked before run |
| API field-index max candidate scene/acquisition groups | 20 |
| API field-index target synchronous latency | 15 seconds for in-bounds queries |
| Max field geometry vertices | 5000 |
| Max field area | 25 sq km |

Source mirror retention remains enabled by default to preserve reprocessing ability. If operators
cannot provide the required object-store headroom, Phase 2 must fail the backfill preflight rather
than silently skip source retention.

Live acceptance minimum:

- at least one clear or mostly clear real Sentinel-2 acquisition over a sample field completes all
  six configured index outputs;
- source COGs needed for that acquisition are mirrored into MinIO before index processing;
- six-month backfill command/API run completes search and processes all eligible items within
  configured limits, or produces categorized retryable failures for provider/data issues;
- all failures are represented in `backfill_runs.summary_json`.

### 20.6 Bangalore readiness and weekly preload policy

The app integration readiness contract is:

```text
GET /api/v1/analytics/readiness?sourceId=sentinel-2-l2a&aoiId=bangalore_60km_geodesic_aoi
```

Rules:

- The endpoint is authenticated like the other `/api/v1` routes and returns the `APIResponse`
  envelope.
- Readiness is read-only: it only inspects registered `provider_scenes`, `raster_outputs`, and
  completed `sentinel2_backfill` jobs. It must not search providers, mirror source assets, process
  rasters, or call TiTiler.
- Staleness is calculated from the newest NDVI output timestamp or an output-producing successful
  `full_pipeline` preload job:
  `freshness_reference_at + AKASHA_SENTINEL2_PRELOAD_FRESHNESS_MAX_AGE_HOURS`. If the current UTC
  time is after that instant, status is `STALE` with reason code `PRELOAD_STALE`.
- `metadata_only`, `mirror_only`, partial, and no-output jobs never refresh readiness freshness.
- Deterministic reason codes are `SOURCE_MISMATCH`, `AOI_MISMATCH`,
  `NO_SUCCESSFUL_PRELOAD_JOB`, `NO_PRELOAD_OUTPUTS`, `MISSING_INDEX_COVERAGE`, and
  `PRELOAD_STALE`.

Default preload policy:

| Setting | Default |
| --- | --- |
| Source | `sentinel-2-l2a` |
| Provider route | `earthsearch:sentinel-2-l2a` |
| AOI | `bangalore_60km_geodesic_aoi` |
| Mode | `full_pipeline` |
| Rolling search window | 180 days |
| Refresh cadence | weekly, Monday 02:30 UTC |
| Freshness threshold | 168 hours |
| Backfill search item cap | 1000, never a one-item smoke cap |

Operators can override the `AKASHA_SENTINEL2_PRELOAD_*` settings, but production readiness for the
app requires the weekly full-pipeline preload to create NDVI derived outputs for the Bangalore AOI.

## 21. Landsat and Sentinel-1 boundaries

### 21.1 Landsat fallback

Landsat is a secondary optical continuity/fallback source, not a replacement for Sentinel-2
red-edge analytics.

Rules:

- Landsat 8/9 current fallback supports NDVI, NDMI, NDBI, and NBR at 30 m.
- Landsat 5/7 are historical-only for Akasha's current crop-health product.
- Landsat has no red-edge bands; NDRE and RECI remain Sentinel-2-only.
- Landsat masking uses `qa_pixel`, not SCL.
- Landsat requester-pays access is opt-in until AWS billing/requester-pays settings are confirmed.
- Official USGS fallback is scoped to Landsat route failures or Earth Search missing/dead links, not
  normal Sentinel-2 operation.

### 21.2 Sentinel-1 future SAR track

Sentinel-1 Earth Search `sentinel-1-grd` can be documented as the future SAR source:

- VV/VH backscatter;
- flood/water and cloud-penetrating monitoring;
- future SAR/RTC processing profile.

It must not be treated as NDVI, NDRE, RECI, or optical fallback in Phase 2.

## 22. Deployment plan

Update:

- API Dockerfile;
- worker Dockerfile;
- compose services;
- TiTiler service image/config to TiTiler-PgSTAC-compatible serving;
- Caddy routing;
- environment variable examples;
- scratch mounts;
- GDAL temp/cache settings.

Worker images must prove:

- `pystac_client` imports;
- `pystac` imports;
- `rasterio` imports;
- `rio_cogeo` imports;
- GDAL can perform HTTPS COG range reads;
- GDAL can read MinIO/S3-compatible COGs;
- optional requester-pays S3 read checks are skipped unless explicitly enabled;
- `rio cogeo validate` or equivalent validation works.

TiTiler-PgSTAC deployment must include:

- internal-only network exposure;
- Postgres/pgSTAC connection settings;
- MinIO/S3 read settings for GDAL, including endpoint URL, path-style addressing, access key,
  secret key, and virtual-hosting disabled;
- `AWS_VIRTUAL_HOSTING=FALSE`;
- `AWS_HTTPS=NO` for internal MinIO unless TLS is enabled;
- `AWS_S3_ENDPOINT` or GDAL-compatible endpoint equivalent;
- `AWS_ENDPOINT_URL=http://minio:9000`;
- credentials supplied as environment/secrets, never committed;
- Caddy rules that expose only the signed resolver routes and block native TiTiler routes.

The TiTiler-PgSTAC smoke test must prove that a registered pgSTAC item/asset backed by private MinIO
can render internally, while the public edge rejects unsigned native TiTiler item routes.

## 23. CI and test plan

### 23.1 Unit tests

- provider DTOs and Earth Search normalization;
- provider route source gating;
- STAC asset parser;
- alternate href parsing;
- scale/offset/nodata handling;
- CRS/window calculation;
- SCL mask mapping;
- Landsat QA_PIXEL fallback mapping when enabled;
- index formulas;
- nodata/divide-by-zero handling;
- signed URL generation/verification;
- best-scene ranking.

### 23.2 Golden raster tests

- tiny synthetic reflectance rasters;
- known expected index pixels;
- expected mask behavior;
- nodata-before-scale/offset behavior;
- expected COG metadata;
- expected zonal statistics.

Default tolerances:

| Check | Tolerance |
| --- | --- |
| Reflectance conversion | absolute error <= `1e-6` before output scaling |
| Index formula float value | absolute error <= `1e-6` |
| Int16 scaled output | +/- 1 scaled unit |
| Zonal mean/min/max | absolute error <= `1e-4` after unscaling |
| Class-area total | within max(1 percent, one output pixel area) |
| Mask propagation | exact pixel match |
| SCL nearest-neighbor resampling | exact class-code match at tested pixels |

### 23.3 Repository tests

- memory/Postgres parity where both are expected;
- provider route CRUD and policy references;
- scene asset external href plus mirror object path behavior;
- stage checkpoint lifecycle;
- idempotency and failed retry behavior;
- deterministic output uniqueness;
- path and external href non-disclosure.

### 23.4 API tests

- auth required;
- validation envelope;
- success response shape;
- unavailable response shape;
- no internal paths;
- no Earth Search/AWS hrefs;
- signed URL behavior;
- OpenAPI documents expected errors.

### 23.5 Integration tests

- migrations and seeds;
- MinIO object write/read/stat;
- source mirror write/read/stat;
- pgSTAC registration;
- TiTiler-PgSTAC route smoke;
- Celery stage execution with eager mode where feasible.

### 23.6 Live tests

Live tests must be opt-in and skipped by default. They require:

- `AKASHA_LIVE_PROVIDER_TESTS=true`;
- safe AOI/date window;
- Earth Search root/search reachability;
- HTTPS COG range-read support;
- redacted logs.

Landsat requester-pays live tests additionally require explicit requester-pays enablement and AWS
configuration.

## 24. Documentation deliverables

Update or add:

- Earth Search setup and live-validation runbook;
- provider route contract;
- external/mirrored source asset catalog notes;
- source COG mirroring runbook and storage preflight guidance;
- Sentinel-2 L2A Earth Search processing profile;
- SCL mask class mapping;
- STAC scale/offset/nodata handling;
- COG standard and validation report;
- field-index API examples;
- backfill runbook;
- Landsat fallback notes and requester-pays guardrails;
- TiTiler-PgSTAC private serving threat model;
- operations smoke-test checklist update.

## 25. Implementation sequence

### 25.1 Phase 2A: architecture gates and foundation

1. Finalize provider route conventions.
2. Finalize source asset location/mirror constraints.
3. Finalize pgSTAC item/asset conventions.
4. Finalize durable stage schema.
5. Add dependencies, settings, and image smoke checks.
6. Add migration `0002`.
7. Add seed updates for provider routes, Sentinel-2 profiles, and NDVI profiles.

### 25.2 Phase 2B: provider and catalog

1. Add provider DTOs and Earth Search adapter.
2. Add provider route repository.
3. Extend object store methods for STAC manifests and source COG mirrors.
4. Add scene/asset/raster/profile/stage/backfill repositories.
5. Add mocked provider tests.

### 25.3 Phase 2C: source mirroring and processing pipeline

1. Implement STAC item/asset parser.
2. Implement source COG mirror service.
3. Implement CRS/window calculation.
4. Implement Sentinel-2 preprocessing profile.
5. Implement SCL mask handling.
6. Implement index engine.
7. Implement COG generation and validation.
8. Implement pgSTAC registration.

### 25.4 Phase 2D: orchestration

1. Add Sentinel-2 Celery tasks.
2. Add stage checkpoint lifecycle.
3. Add per-scene, per-source-asset, and per-index idempotency.
4. Add retry/resume behavior.
5. Add backfill command or sync mode.

### 25.5 Phase 2E: analytics API and serving

1. Add field-index request/response schemas.
2. Add best-scene selection.
3. Add signed layer/stat URL resolver.
4. Add TiTiler-PgSTAC serving smoke.
5. Add zonal and class-area stats.
6. Add API tests.

### 25.6 Phase 2F: live validation

1. Validate Earth Search search for the Bangalore AOI/window.
2. Mirror source COGs for at least one real Sentinel-2 L2A item/acquisition.
3. Run full pipeline for one real field polygon.
4. Run six-month Bangalore backfill.
5. Produce backfill summary and COG validation evidence.

## 26. Acceptance criteria

Phase 2 implementation is accepted when:

1. All existing Phase 1 tests remain green.
2. Offline tests cover provider contract, STAC asset parsing, scale/offset handling, SCL masks,
   index formulas, COG validation, repositories, idempotency, best-scene selection, signed URLs,
   field-index response envelopes, and path/href non-disclosure.
3. Worker/API images prove GDAL/rasterio/rio-cogeo/STAC support and HTTPS COG range reads.
4. Failed stages retry/resume without duplicate active jobs, duplicate deterministic output rows, or
   orphaned final objects.
5. Accepted source COGs needed for AOI-complete processing are mirrored into MinIO.
6. Processed Sentinel-2 outputs are registered in Postgres and pgSTAC.
7. API responses and client-visible tile/stat URLs expose no internal storage paths or provider
   asset hrefs.
8. With Earth Search live access and one real Sentinel-2 item/acquisition, the pipeline completes:

```text
Earth Search search
  -> STAC manifest
  -> source COG mirror
  -> SCL mask
  -> indices
  -> COG
  -> catalog/pgSTAC/tile layer
  -> field-index API
```

9. A real field polygon returns tile URL, statistics, class-area statistics, source/date, provider
   route, resolution, cloud score, quality, and provenance.
10. `UNAVAILABLE` is returned when no valid scene exists within the selection policy.
11. Six-month Bangalore Sentinel-2 backfill is executable, deduped, retryable, bounded by resource
    controls, and produces a categorized summary.
12. Landsat fallback, if enabled, returns only Landsat-supported indices and quality warnings for 30
    m resolution; it never returns NDRE/RECI.
13. Sentinel-1 remains unavailable for optical vegetation indices.

## 27. Open items

| Item | Status | Notes |
| --- | --- | --- |
| Earth Search public API SLA | Accepted risk | Mitigate through cached manifests and source COG mirroring |
| Source mirror storage estimate | Required before live backfill | Backfill must preflight MinIO headroom |
| AOI-clipped vs full-source mirror safety criteria | Defaulted | Prefer AOI-clipped when reproducibility/provenance are complete; otherwise full-source mirror |
| Landsat requester-pays access | Gated | Enable only after AWS billing/requester-pays config is confirmed |
| CDSE Sentinel-2 fallback | Deferred | Optional future route, not Phase 2 critical path |
| pgSTAC insertion helper choice | Locked | Use `pystac` for STAC construction and `pypgstac` for loading |
| Geometry limits | Defaulted | Initial defaults are 5000 vertices and 25 sq km; operator can tune |
| Synchronous stats latency target | Defaulted | Initial target is 15 seconds for in-bounds queries |
| COG scope | Locked for Phase 2 | AOI-clipped source mirrors where safe, AOI-clipped per-scene/acquisition per-index derived COGs |
| RECI output dtype/range | Locked | Float32, nodata -9999.0, no hard `[-1, 1]` clip |

## 28. Review history

Initial Phase 2 planning was reviewed with GPT-5.5 and Opus 4.8. The original CDSE-first standalone
document was then reviewed again with GPT-5.5 and Opus 4.8.

After provider due diligence, the plan was revised to Element84 Earth Search + AWS COG primary. Opus
4.8 review feedback was incorporated, especially:

- `scene_assets.object_path` migration constraints;
- provider route modeling without duplicating execution-policy fields;
- parsing default and alternate STAC asset hrefs;
- nodata-before-scale/offset ordering;
- CRS-aware read-window computation;
- same-date multi-tile mosaicking;
- logical scene dedupe across provider routes;
- Landsat fallback scope and requester-pays gating;
- source COG mirroring for AOI-complete reproducibility.

GPT-5.5 synthesis incorporated the final retention decision: accepted acquisitions mirror all source
COG assets needed for complete AOI coverage into MinIO, process from those mirrors, and keep
external href provenance without exposing it through public APIs.
