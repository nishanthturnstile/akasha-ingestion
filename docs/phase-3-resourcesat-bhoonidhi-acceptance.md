# Phase 3 ResourceSat Bhoonidhi Acceptance Gates

This document freezes the Phase 3 ResourceSat/Bhoonidhi acceptance gates before implementation.
It defines the Phase 1 TASK-003 baseline-evidence gate and satisfies TASK-005, TASK-006, and TASK-007 from
`docs/impl-plan/data-resourcesat-bhoonidhi-ingestion-pipeline-1.md`.

Canonical references:

- `docs/akasha-ingestion-plan.md`: ResourceSat exposure is gated on Sentinel-2 cross-validation;
  usable pixels, min valid pixels, and no-leak API behavior are product contracts.
- `docs/implementation-roadmap.md`: Phase 3 exit gate requires ResourceSat surface reflectance and
  NDVI within tolerance of Sentinel-2 before exposure.
- `docs/phase-0/aoi-demo-inputs.md` and `docs/phase-0/phase0-status.md`: AOI, bbox, center, and
  clear-season window.
- `docs/phase-2-sentinel-2-vertical-slice-implementation-plan.md`: Sentinel-2 readiness evidence
  and readiness API baseline.

Model-review findings incorporated as acceptance gates:

| Finding | Acceptance gate |
| --- | --- |
| Canonical ResourceSat water index is `ndwi_green_nir` | Seed data, index engine, field-index input, readiness, outputs, and response provenance use `NDWI_GREEN_NIR`; generic `NDWI` fails |
| ResourceSat jobs use existing queues | Stage evidence records only `heavy-cpu`, `preprocess`, `cog`, or `stats` for ResourceSat work |
| Live runs need fail-closed root checks | Unsafe roots are rejected before any non-dry-run provider download or raster write |
| Readiness depends on summary field names | Job metadata contains `processed_count` and `failed_count`, not only `processed` or `failed` |
| Browser leak rule is scoped to product-app JSON/headers | Server-to-server signed ingestion URLs are allowed only inside the BFF; browser leak count must be 0 |
| `SyncRequest` must allow Bhoonidhi ResourceSat routes | Dry-run and live ResourceSat sync requests reach route validation without a Sentinel-only rejection |
| Source/AOI locks already exist | Evidence shows ResourceSat extends the existing scheduler lock path and serializes same source/AOI live jobs |
| App-native removal is destructive | TASK-105/cutover cannot run until TASK-116, TASK-117, and TASK-118 live evidence passes |

## Source and collection contracts

Acceptance fails if any source advertises an unsupported ResourceSat index, omits a required index,
or uses a different Bhoonidhi collection ID.

| Source | Bhoonidhi collection | Required indices | Exposure role |
| --- | --- | --- | --- |
| `resourcesat-2a-liss3-boa` | `ResourceSat-2A_LISS3_BOA` | `NDVI`, `MSAVI`, `NDMI`, `NDWI_GREEN_NIR` | Primary ResourceSat field-analytics source after acceptance |
| `resourcesat-2a-liss4-mx70-l2` | `ResourceSat-2A_LISS4-MX70_L2` | `NDVI`, `MSAVI`, `NDWI_GREEN_NIR` | Partial high-resolution enhancement only |
| `resourcesat-2a-awifs-boa` | `ResourceSat-2A_AWIFS_BOA` | `NDVI`, `MSAVI`, `NDMI`, `NDWI_GREEN_NIR` | Regional/coarse context only |

Rejected ResourceSat indices: `NDRE`, `RECI`, generic `NDWI`, `NDBI`, `SAVI`, and `GNDVI`.
LISS-4 also rejects `NDMI` because it has no SWIR1 band.

## AOI mapping and validation window

| Field | Acceptance value |
| --- | --- |
| Ingestion AOI | `bangalore_60km_geodesic_aoi` |
| Product/app AOI label | `bangalore-60km` |
| CRS | `EPSG:4326` |
| Center | `[77.5776037099731, 13.076858177177233]` |
| Provider/search bbox | `[77.023647, 12.537266, 78.131561, 13.61645]` |
| Clear-season validation window | `2026-01-15` to `2026-04-15` |

Pass condition: every ResourceSat job, readiness record, pgSTAC item, output manifest, and product
BFF request maps `bangalore_60km_geodesic_aoi` to `bangalore-60km` without introducing a third AOI
identifier. Mismatched AOI IDs fail acceptance.

## Sentinel-2 baseline and cross-validation

TASK-003 is incomplete until the Phase 3 evidence section records the Sentinel-2 processed dates,
AOI footprint, source output IDs, and overlap fields used as the ResourceSat baseline.

ResourceSat exposure fails closed unless all of these pass:

1. Sentinel-2 clear-date outputs exist over the ResourceSat AOI overlap inside
   `2026-01-15` to `2026-04-15`.
2. ResourceSat surface reflectance and NDVI are compared against Sentinel-2 on overlapping clear
   dates and common AOI/field geometries.
3. The numeric tolerances for NDVI and per-band surface-reflectance deltas are frozen in the
   implementation evidence before live exposure.
4. Until those numeric tolerances are frozen, cross-validation status is `FAIL_NOT_FROZEN` and no
   ResourceSat output may be marked `AVAILABLE`.
5. After freeze, pass requires every reported metric to satisfy:
   `observed_abs_delta <= frozen_tolerance` for the applicable NDVI or SR metric.

## Coverage, usable-pixel, valid-pixel, and freshness gates

| Source | AOI coverage pass gate | Field usable-pixel pass gate | Required warning/exclusion |
| --- | ---: | ---: | --- |
| LISS-3 | `coverage_pct >= 95` | `usable_pixel_pct >= 80` | None after all other gates pass |
| LISS-4 | Measured and `coverage_pct >= 10` | `usable_pixel_pct >= 80` inside covered footprint | Must emit partial-coverage warning; never a LISS-3 production replacement |
| AWiFS | `usable_coverage_pct >= 60` | `usable_pixel_pct >= 80` | Must emit coarse/regional warning and be excluded from normal field decisions unless coarse/regional use is requested |

Minimum valid-pixel floor:

- Pass requires `validPixelCount >= AKASHA_FIELD_MIN_USABLE_PIXELS` and the response must report
  both `validPixelCount` and the configured floor value.
- If the setting is not explicitly configured, the current default floor is `1`; the evidence must
  record that value.
- A negative test with `validPixelCount < AKASHA_FIELD_MIN_USABLE_PIXELS` must return
  `UNAVAILABLE`, or for explicitly coarse/regional AWiFS use only, a low-confidence/mixed-pixel
  warning. Any normal field-level `AVAILABLE` response below the floor fails acceptance.

Freshness and acquisition age:

- Each source must record its configured refresh cadence and max freshness age in hours.
- Pass requires `freshness_reference_at + configured_max_age_hours >= validation_time_utc`.
- If the expression is false, readiness and field analytics must not return `AVAILABLE`; they must
  return stale/unavailable status with a deterministic reason.
- If a source lacks configured ResourceSat freshness settings, acceptance fails until the settings
  are frozen and recorded.

Deterministic source/date selection:

- Pass requires every field-index response to record requested date, selected acquisition date,
  selected source, candidate count, and selection reason.
- Candidate ordering must be deterministic: requested-date distance, then usable-pixel percentage,
  then coverage percentage, then cloud percentage, then native resolution, then product ID.
- Repeating the same request against unchanged outputs must select the same source/date/product ID.

## Operational gates

| Gate | Pass threshold | Evidence fields to record |
| --- | --- | --- |
| Raw download integrity | Every downloaded product has size in bytes; checksum is present where provider supplies one, otherwise an internally computed SHA-256 is recorded | source, product_id, object_uri/redacted path class, size_bytes, checksum_type, checksum_value |
| Raw retention | Raw ZIP/source product remains retained after prepared COG, composite, and derived index outputs are generated | product_id, raw_object_exists=true, retention_policy |
| Idempotency | Immediate rerun with same source/AOI/date/caps creates 0 duplicate `provider_scenes`, 0 duplicate `raster_outputs`, 0 duplicate pgSTAC items, and 0 duplicate final objects | before_count, after_count, duplicate_count=0, rerun_job_id |
| Job summary contract | ResourceSat summaries contain `processed_count` and `failed_count` keys; readiness reads those names | job_id, processed_count, failed_count |
| Safe disk roots | Live run preflight rejects roots under `/`, `/tmp`, `/var/tmp`, `/var/lib/docker`, `/data/coolify`, or outside approved `/srv/akasha`/configured `AKASHA_` root | resolved_roots, rejected_root_test_result, approved_root_test_result |
| COG validity | `rio cogeo validate` passes for every analytic, mask, composite, and derived index COG | cog_uri/redacted path class, validation_exit_code=0 |
| pgSTAC catalog | Derived pgSTAC collection exists and all generated items/assets are present for accepted outputs | collection_id, item_count, asset_keys, date |
| Provenance | Every output includes source, collection, acquisition date, mask method/version, processing profile version, resolution, and coverage/usable-pixel metrics | provenance JSON fields |
| Dry-run safety | Dry-run creates no provider downloads, no raster processing, no object uploads, and no pgSTAC mutations | dry_run_job_id, download_count=0, upload_count=0, pgstac_mutation_count=0 |

## API, BFF, leakage, and cutover gates

Pass conditions:

1. `FieldIndexRequest` accepts `sourceId` and index `NDWI_GREEN_NIR`.
2. Product BFF sends `sourceId` for ResourceSat field statistics, overlays, point lookup, trend, and
   dates; product browser contracts remain stable.
3. Browser-visible product-app JSON and headers contain none of:
   `s3://`, MinIO object keys, storage paths, provider hrefs, Bhoonidhi URLs, signed provider URLs,
   `tileUrl`, `statsUrl`, `overlayUrl`, `pointUrl`, `layerId`, `sig`, `kid`, `exp`, credentials,
   API keys, internal TiTiler URLs, internal MinIO URLs, internal Postgres URLs, or internal IP/host
   names.
4. Server-to-server ingestion signed URLs may exist only between product BFF and ingestion; they must
   not be forwarded to the browser.
5. App-native ResourceSat processing is removed or disabled only after live staging acceptance for
   TASK-116, TASK-117, and TASK-118 is recorded here. Unit tests alone are not cutover evidence.
6. App-native ResourceSat removal/cutover fails if any accepted path silently falls back to local app
   COGs instead of standalone ingestion.

## Compact acceptance checklist

| ID | Gate | Pass threshold | Evidence fields |
| --- | --- | --- | --- |
| A1 | LISS-3 contract | Correct source, collection, and exactly 4 required indices | source_id, collection_id, supported_indices |
| A2 | LISS-4 contract | Correct source, collection, exactly 3 required indices, no `NDMI` | source_id, collection_id, supported_indices |
| A3 | AWiFS contract | Correct source, collection, and exactly 4 required indices | source_id, collection_id, supported_indices |
| A4 | AOI mapping | Ingestion AOI maps to app AOI; bbox/center/window match this doc | aoi_id, app_aoi, bbox, center, date_window |
| A5 | Sentinel-2 baseline | Dates, AOI footprint, and output IDs recorded before ResourceSat exposure | s2_dates, s2_aoi_footprint, s2_output_ids |
| A6 | Cross-validation | Tolerances frozen and all deltas `<= frozen_tolerance` | tolerance_version, ndvi_delta, sr_delta_by_band |
| A7 | LISS-3 quality | `coverage_pct >= 95`, `usable_pixel_pct >= 80`, valid pixels above floor | coverage_pct, usable_pixel_pct, validPixelCount |
| A8 | LISS-4 quality | measured `coverage_pct >= 10` plus partial warning | coverage_pct, warning_code |
| A9 | AWiFS quality | `usable_coverage_pct >= 60` plus coarse/regional exclusion | usable_coverage_pct, warning_code, use_case |
| A10 | Freshness | no stale source marked `AVAILABLE` | freshness_reference_at, max_age_hours, validation_time_utc, status |
| A11 | Operations | integrity, retention, idempotency, COG validation, pgSTAC, and preflight all pass | job_ids, product_ids, counts, checksums, cog_validation |
| A12 | API/BFF/leakage | `sourceId` and `NDWI_GREEN_NIR` accepted; browser leak count is 0 | request_samples, response_samples, leak_count=0 |
| A13 | Cutover | app-native ResourceSat removed only after live staging acceptance | TASK-116/117/118 evidence links, cutover_commit |

## Evidence log

Append one row per capped live run or cutover validation. Empty evidence means acceptance has not
passed.

| Date UTC | Task | Source | AOI | Product IDs | Job IDs | Output dates | Coverage | Usable pixels | Freshness status | Cross-validation status | Result |
| --- | --- | --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- |
| TBD | TASK-116 | `resourcesat-2a-liss3-boa` | `bangalore_60km_geodesic_aoi` | TBD | TBD | TBD | TBD | TBD | TBD | TBD | PENDING |
| TBD | TASK-117 | `resourcesat-2a-liss4-mx70-l2` | `bangalore_60km_geodesic_aoi` | TBD | TBD | TBD | TBD | TBD | TBD | TBD | PENDING |
| TBD | TASK-118 | `resourcesat-2a-awifs-boa` | `bangalore_60km_geodesic_aoi` | TBD | TBD | TBD | TBD | TBD | TBD | TBD | PENDING |
| TBD | TASK-119 | product app cutover | `bangalore-60km` | TBD | TBD | TBD | TBD | TBD | TBD | TBD | PENDING |
| TBD | TASK-120 | product app app-domain validation | `bangalore-60km` | TBD | TBD | TBD | TBD | TBD | TBD | TBD | PENDING |
