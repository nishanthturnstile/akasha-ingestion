
# Akasha Satellite Data Ingestion & Vegetation Index Processing — Final Plan

## 1. Final Direction

Akasha will be built as an **EOSDA Crop Monitoring–style backend intelligence platform for Indian agricultural use cases**, but the first major deliverable is **not the UI**. The first deliverable is the **satellite data ingestion, processing, and vegetation-index generation pipeline**.

The UI will exist as a separate application. That UI will allow users to draw/select a field polygon on a normal base map such as ArcGIS, Google satellite imagery, or another map provider. Once the user selects a field and chooses an index such as NDVI or NDMI, the UI will send the field coordinates to this ingestion/index service. This service will then return the required index layer, field-level statistics, quality score, and related metadata.

The core goal is:

> Build a configurable, on-premises, low-cost satellite data pipeline that can ingest approved satellite data sources, process them into analysis-ready raster layers, calculate vegetation indices, and expose the results through APIs and TiTiler-compatible raster outputs.

The uploaded satellite catalogue already identifies 20 approved satellite platforms/sensors, including ISRO/NRSC sources, Sentinel, Landsat, MODIS, SAR sources, and gated commercial/high-resolution sources. The catalogue also defines source slugs, provider adapters, product state, resolution, revisit cadence, band availability, and index support.

---

## 2. Confirmed Decisions

| Area                            | Final Decision                                                                                |
| ------------------------------- | --------------------------------------------------------------------------------------------- |
| Production hosting              | On-premises only                                                                              |
| Internet access                 | Allowed in production                                                                         |
| Reason for on-prem              | Keep operating cost low for Indian customers                                                  |
| Development/testing             | Cloud is allowed temporarily                                                                  |
| Current provider access         | Bhoonidhi/Bhuvan/NRSC API credentials available                                               |
| Current whitelisted machine     | Azure VM already whitelisted                                                                  |
| Production prerequisite         | On-prem public/static IP must also be whitelisted later                                       |
| MVP AOI                         | Bangalore + approximately 60 km radius                                                        |
| AOI configurability             | Must support future regions such as Kolkata or any custom AOI                                 |
| MVP historical backfill         | 6 months                                                                                      |
| Production historical retention | Minimum 1 year raw-data retention                                                             |
| Raw data after retention        | Delete raw data after retention window, but retain metadata and derived outputs as configured |
| Initial resolution baseline     | Per-source native grids (not one value): Sentinel-2 10 m for NDVI/MSAVI and 20 m for red-edge/SWIR indices; ResourceSat LISS-4 5–5.8 m (VNIR only), LISS-3 23.5 m, AWiFS 56 m; Landsat 30 m. Do not upsample coarse data to imply false precision |
| Higher-resolution data          | Architecturally supported, but enabled later based on cost/license                            |
| UI responsibility               | Separate project                                                                              |
| This project responsibility     | Ingestion, processing, index generation, tile/API serving                                     |
| Raw satellite visual display    | Not required for end users                                                                    |
| True color/false color imagery  | Optional internal QA only                                                                     |
| Initial indices (source-aware)  | NDVI, MSAVI from all optical MVP sources; NDMI, NDBI where SWIR exists (Sentinel-2, ResourceSat LISS-3/AWiFS, Landsat); NDRE, RECI only from Red-Edge sources (Sentinel-2 — ResourceSat has no Red Edge). See §11 Source×Index matrix |
| Advanced indices                | Supported through configurable index engine                                                   |
| Drone/UAV data                  | Future extension, not MVP                                                                     |
| MVP active optical sources      | Sentinel-2 (first vertical slice), ResourceSat-2A (parallel India differentiator), Landsat 8/9 (continuity); Sentinel-1 SAR deferred to Phase 6 |
| First vertical slice            | Sentinel-2 — ships analysis-ready L2A surface reflectance + SCL cloud mask, so the pipeline is proven end-to-end fastest |
| ResourceSat atmospheric correction | Built in MVP as a parallel, non-blocking workstream (Py6S/6S, DOS interim); validated against Sentinel-2 on overlapping clear dates |
| External field API access       | Authenticated (API keys/OAuth/mTLS); no public/raw bucket access; internal storage paths never returned to clients |

---

## 3. Project Scope

## 3.1 In Scope

This project must build:

1. Satellite source registry.
2. Provider adapters.
3. AOI-based satellite scene search.
4. Historical backfill for the last 6 months for MVP.
5. Automated scheduled sync based on satellite revisit cadence.
6. Raw data download and storage.
7. Raw data extraction and metadata parsing.
8. Pre-processing pipeline.
9. Band extraction and band mapping.
10. Cloud/quality masking.
11. Analysis-ready raster generation.
12. Vegetation index calculation.
13. GeoTIFF / Cloud Optimized GeoTIFF generation.
14. TiTiler-compatible tile serving.
15. Field polygon clipping.
16. Field-level statistics API.
17. Quality/confidence scoring.
18. Job monitoring, retries, and failure debugging.
19. On-prem deployment package.
20. Per-source atmospheric correction (vendor SR; custom DOS/6S for ResourceSat).
21. Per-sensor cloud/shadow masking (S2 SCL, Landsat QA_PIXEL, ResourceSat custom).
22. Multi-scene coverage mosaicking and best-scene selection service.
23. Provider order/staging state machine and download integrity (checksums, resume, quotas).
24. Multi-provider credentials/secrets (Bhoonidhi/NRSC, CDSE, USGS, Earthdata).
25. External API authentication, rate limiting, and signed tile/stats URLs.
26. License/exposure enforcement before any source is served publicly.
27. Output provenance/versioning (AC, mask, formula versions) for reproducibility.

## 3.2 Out of Scope for MVP

These should not block the first working pipeline:

1. Full crop disease prediction.
2. Yield prediction.
3. AI recommendation engine.
4. Prescription/fertilizer advisory maps.
5. Paid commercial satellite tasking.
6. Drone/UAV ingestion.
7. End-user UI implementation.
8. Complex crop-specific agronomy models.
9. Automated field-boundary detection.
10. Real-time live monitoring.

---

## 4. Target Architecture

The system should be designed as a **satellite data processing backend service**.

```text
External Providers
  ├── Bhoonidhi / NRSC / Bhuvan APIs
  ├── Sentinel / Copernicus Adapter
  ├── Landsat / USGS Adapter
  ├── Earthdata / MODIS Adapter
  ├── SAR Adapters
  └── Commercial Vendor Adapters - gated

        ↓

Akasha Ingestion Service
  ├── Source Registry
  ├── Provider Adapter Layer
  ├── Scene Search
  ├── Download Manager
  ├── Scheduler
  ├── Job Queue
  └── Metadata Catalogue

        ↓

Storage + Processing
  ├── Raw Product Storage
  ├── Extracted Band Storage
  ├── Pre-processing Pipeline
  ├── Cloud / Quality Masking
  ├── Index Calculation Engine
  ├── COG / GeoTIFF Generator
  └── Derived Raster Store

        ↓

Serving Layer
  ├── TiTiler
  ├── Field Analytics API
  ├── Zonal Statistics API
  ├── Time-Series API
  └── Internal Operator Dashboard

        ↓

Separate UI Application
  ├── User draws field polygon
  ├── User selects index
  ├── UI sends coordinates to API
  └── UI renders returned tile/statistics
```

Recommended stack:

| Component                   | Recommendation                                         |
| --------------------------- | ------------------------------------------------------ |
| Backend processing language | Python                                                 |
| API framework               | FastAPI                                                |
| Raster processing           | GDAL, Rasterio, Rioxarray, Xarray, NumPy               |
| Spatial DB                  | PostgreSQL + PostGIS                                   |
| Object storage              | MinIO                                                  |
| Queue                       | Celery + Redis/RabbitMQ, or RQ for simpler MVP         |
| Scheduler                   | Celery Beat / APScheduler / custom scheduler table     |
| Tile service                | TiTiler                                                |
| Deployment                  | Docker Compose for MVP/on-prem                         |
| Monitoring                  | Prometheus + Grafana or lightweight logs dashboard     |
| Logs                        | Structured JSON logs                                   |
| Atmospheric correction      | Vendor SR for S2/Landsat; Py6S/6S + dark-object subtraction for ResourceSat |
| Cloud masking               | Sentinel-2 SCL/s2cloudless, Landsat QA_PIXEL, ResourceSat custom mask |
| API security                | API keys/OAuth2/mTLS gateway, signed tile URLs, rate limiting |
| Secrets                     | Per-provider secret store (Bhoonidhi/NRSC, CDSE, USGS, Earthdata); Docker/Vault secrets from Phase 1; rotation; log redaction |

---

## 5. Satellite Source Strategy

The 20 satellites should be handled as an **approved source catalogue**, not as 20 sources that must all be fully automated on day one.

### 5.1 Source Tiers

| Tier   | Meaning                                           | Examples                                                      | MVP Action                             |
| ------ | ------------------------------------------------- | ------------------------------------------------------------- | -------------------------------------- |
| Tier 1 | Free/open/low-cost and useful for field analytics | ResourceSat-2A, Sentinel-2, Landsat 8/9                       | Implement first                        |
| Tier 2 | Useful but needs separate processing profile      | Sentinel-1, EOS-04, NISAR, MODIS, EOS-06                      | Add after optical pipeline is stable   |
| Tier 3 | High-resolution or paid/gated                     | PlanetScope, Cartosat-3, SkySat, BlackSky, KOMPSAT, SuperView | Registry support now, activation later |
| Tier 4 | Historical/archive-only                           | Landsat 5/7, IRS-1C                                           | On-demand only                         |
| Tier 5 | Future/non-India reference                        | Drone/UAV, NAIP methodology reference                         | Future only                            |

### 5.2 MVP Active Sources

For MVP, sources are activated in this order. Sentinel-2 proves the pipeline first; ResourceSat is built in parallel as the India-specific differentiator. Each source is gated behind adapter validation plus a sample-scene processing check before its outputs are exposed.

1. **Sentinel-2 (first vertical slice)**

   * 10 m multispectral baseline (20 m for red-edge/SWIR indices).
   * Ships analysis-ready L2A surface reflectance + SCL cloud/shadow mask, so NDVI → COG → TiTiler → field-stats can be proven end to end fastest, with no custom atmospheric correction or cloud masker needed.
   * The only MVP source with a Red Edge band, so it is the source for NDRE and RECI.
   * Provider adapter `cdse`; product exposure stays disabled until CDSE validation passes (catalogue §8).

2. **ResourceSat-2A via Bhoonidhi/NRSC (parallel India differentiator)**

   * India-focused priority source, but treated as three distinct instruments with different capabilities — do not model it as one band set:

     | Instrument (sourceId) | Bands | Producible indices | Cannot produce | Native res |
     | --------------------- | ----- | ------------------ | -------------- | ---------- |
     | LISS-4 (`resourcesat-2a-liss4-mx70-l2`) | G, R, NIR | NDVI, MSAVI, SAVI, NDWI, GNDVI | NDMI/NDBI (no SWIR), NDRE/RECI (no Red Edge) | 5–5.8 m |
     | LISS-3 (`resourcesat-2a-liss3-boa`) | G, R, NIR, SWIR | NDVI, MSAVI, SAVI, NDWI, NDMI, NDBI | NDRE/RECI (no Red Edge), EVI (no Blue) | 23.5 m |
     | AWiFS (`resourcesat-2a-awifs-boa`) | G, R, NIR, SWIR | NDVI, MSAVI, SAVI, NDWI, NDMI, NDBI | NDRE/RECI (no Red Edge) | 56 m |

   * No off-the-shelf surface-reflectance or cloud-mask product — requires the custom atmospheric correction (Py6S/6S, DOS interim) and custom cloud masking built as parallel workstreams (see §9–§10).
   * LISS-4's 70 km swath does not cover the Bangalore ~120 km-diameter AOI in one scene → multi-scene coverage/mosaic required.

3. **Landsat 8/9**

   * 30 m; useful for longer-term continuity, thermal context, NDBI/NDMI, and gap filling. No Red Edge → no NDRE/RECI.
   * Ships Collection-2 Level-2 surface reflectance + QA_PIXEL mask (no custom correction needed). Provider adapter `usgs`; gated until operator validation.

4. **Sentinel-1 (deferred to Phase 6)**

   * Separate SAR pipeline; never forced into optical index logic.
   * Useful for cloudy/monsoon periods and soil moisture/flood context. In MVP, cloudy optical gaps are reported as UNAVAILABLE rather than filled by SAR (see §10).

The catalogue confirms multispectral sources are suitable for vegetation indices, while SAR sources are treated separately for backscatter, soil moisture, flood mapping, and all-weather monitoring.

---

## 6. Data Retention Strategy

### 6.1 Raw Data

| Environment     | Raw Data Retention                                             |
| --------------- | -------------------------------------------------------------- |
| MVP             | 6 months                                                       |
| Production      | Rolling 12 months minimum                                      |
| After retention | Delete raw product files                                       |
| Before deletion | Ensure derived outputs and metadata are successfully generated |

Raw data is expensive to store, so a 12-month rolling retention policy is acceptable. However, once raw data is deleted, reprocessing older outputs will require re-downloading from the provider if still available.

### 6.2 Derived Outputs

Recommended policy:

| Data Type              | Retention                    |
| ---------------------- | ---------------------------- |
| Scene metadata         | Long-term / indefinite       |
| Provider product IDs   | Long-term / indefinite       |
| Processing logs        | 6–12 months                  |
| Index COGs             | 1–3 years if storage permits |
| Field statistics       | Long-term                    |
| Cloud/quality metadata | Long-term                    |
| Provenance + versions (AC/mask/formula) | Long-term       |
| Checksums              | Long-term                    |
| Minimal ARD/bands (optional) | Longer than raw ZIPs to enable reprocessing |
| Raw ZIP/native files   | 12 months production rolling |

This gives you a good balance: raw storage is controlled, but useful analytics history remains available. Because raw deletion blocks re-running a fixed cloud mask or formula, always retain provenance, checksums, provider product IDs, and processing versions — and optionally keep minimal ARD/band assets longer than the raw ZIPs so outputs can be regenerated without re-downloading.

---

## 7. Storage Structure

Use MinIO buckets or folder prefixes like this:

```text
akasha-data/
  raw/
    provider/
      satellite/
        product_id/
          original.zip

  extracted/
    provider/
      satellite/
        product_id/
          bands/
          metadata/

  ard/
    provider/
      satellite/
        product_id/
          surface_reflectance/
          masks/

  indices/
    provider/
      satellite/
        product_id/
          ndvi.cog.tif
          ndre.cog.tif
          msavi.cog.tif
          reci.cog.tif
          ndmi.cog.tif
          ndbi.cog.tif

  qa/
    provider/
      satellite/
        product_id/
          cloud_mask.cog.tif
          usable_pixel_mask.cog.tif
          preview.png

  reports/
    field_id/
      date/
        stats.json
```

> The `indices/` tree above is illustrative. Only the indices a source's bands support are generated (see §11 Source×Index matrix) — e.g., ResourceSat never produces `ndre`/`reci`, and LISS-4 produces no `ndmi`/`ndbi`.

Do not generate permanent per-field TIFFs by default. That will create too many files. The serving baseline is **per-scene index COGs** (one COG per scene per index), not pre-built AOI mosaics:

1. Generate one index COG per scene (stored under the per-`product_id` path above).
2. At query time, pick the best scene covering the field (see §8.3 best-scene selection) and clip/calculate field-level stats on demand.
3. Serve AOI-wide views via TiTiler MosaicJSON over the per-scene COGs; pre-built AOI date-mosaics are an optional cached layer, not the MVP baseline.
4. Cache field-level results only when needed.

---

## 8. Processing Workflow

## 8.1 Backfill Workflow

For MVP:

```text
Input:
  AOI = Bangalore 60 km radius
  Date range = last 6 months
  Sources = ResourceSat-2A, Sentinel-2, Landsat 8/9 initially

Steps:
  1. Load source registry.
  2. Search provider catalogue by AOI and date range.
  3. Filter by product availability, cloud metadata, and license state.
  4. Download raw products.
  5. Store raw products in MinIO.
  6. Extract product metadata.
  7. Extract required bands.
  8. Apply correction/masking/pre-processing.
  9. Generate analysis-ready rasters.
  10. Calculate required indices.
  11. Generate COG outputs.
  12. Register outputs in PostGIS metadata table.
  13. Expose through TiTiler/API.
```

## 8.2 Scheduled Sync Workflow

After backfill:

```text
For each active satellite source:
  1. Check revisit cadence.
  2. Determine next expected acquisition window.
  3. Search provider catalogue for new scenes.
  4. Skip already downloaded products.
  5. Download only new eligible products.
  6. Process and generate index layers.
  7. Register new per-scene index COGs as available scenes for the AOI (no precomputed AOI mosaic — "latest available" is resolved per query; see §8.3).
  8. Mark job success/failure.
```

The scheduler must be source-aware. A daily satellite, 5-day satellite, 6-day SAR source, and 16-day Landsat source should not be treated with the same sync frequency.

## 8.3 Best-Scene Selection (field-index queries)

"Latest available" and "best scene for this field" are resolved deterministically at query time, not precomputed:

```text
Given (field geometry, index, requested date):
  1. Candidate window = requested date ± 7 days (hard cap; configurable downward only).
  2. Eligible scene =
       source supports the index for its instrument (band-aware), AND
       source is validated/active, AND
       scene covers the field (mosaic same-date tiles if the field straddles scenes), AND
       field usable-pixels >= 80% (after cloud/shadow/no-data masking).
  3. Rank eligible scenes — quality first:
       a. highest source priority (configurable), then
       b. lowest field cloud %, then
       c. highest resolution, then
       d. nearest date as the final tie-break.
  4. If none eligible in window: try other eligible sources within ± 7 days.
  5. If still none: return status = UNAVAILABLE (never interpolate or silently fill).
```

This rule is the single source of truth for what `/analytics/field-index` returns; two implementations must produce identical results for the same inputs.

---

## 9. Pre-processing Requirements

Before calculating any index, the raw product must go through a standard pre-processing profile.

Required steps:

1. Extract raw ZIP/native product.
2. Read metadata and product manifest.
3. Validate projection and coordinate system.
4. Validate acquisition date/time.
5. Validate band availability.
6. Extract required bands.
7. Apply the source's radiometric/atmospheric-correction profile to produce surface reflectance (see §9.1). Never feed TOA/DN values into indices.
8. Prefer vendor analysis-ready surface reflectance / BOA where available; otherwise run the configured correction and tag the output with the AC method + version.
9. Reproject to the platform processing CRS — UTM zone 43N (EPSG:32643) for the Bangalore AOI; keep raw assets in their native CRS; API field geometry is accepted in EPSG:4326.
10. Resample bands to common resolution.
11. Clip/subset to configured AOI.
12. Assemble same-date scenes into AOI coverage via MosaicJSON over per-scene COGs; do not pre-build persistent AOI date-mosaics in MVP (see §7).
13. Apply the sensor-specific cloud mask (Sentinel-2 SCL/s2cloudless; Landsat QA_PIXEL/CFMask; ResourceSat custom — see §9.2).
14. Apply cloud-shadow mask where the sensor supports it; for ResourceSat, flag pixels as "cloud confidence unknown" until the custom mask is validated.
15. Apply invalid/no-data mask.
16. Generate usable pixel percentage.
17. Generate analysis-ready raster.
18. Generate COG.

Important rule:

> Never calculate NDVI/NDMI/NDRE directly from unvalidated raw pixels. The data must first be corrected, masked, aligned, and converted into a consistent analysis-ready format.

### 9.1 Atmospheric correction profiles (per source)

| Source | Correction in MVP |
| ------ | ----------------- |
| Sentinel-2 | Vendor L2A surface reflectance (Sen2Cor). No custom correction. |
| Landsat 8/9 | Collection-2 Level-2 surface reflectance. No custom correction. |
| ResourceSat-2A | NRSC products may arrive as TOA/DN. Build a custom path: dark-object subtraction (DOS) as the interim, upgrading to 6S/Py6S using ancillary inputs (aerosol optical depth, water vapour, sun/view angles, DEM). Runs as a parallel, non-blocking workstream. |

Validation: ResourceSat surface reflectance and derived NDVI must be cross-validated against Sentinel-2 over overlapping clear-sky dates before ResourceSat outputs are exposed. If a product cannot be corrected to acceptable surface reflectance, it is rejected (status recorded), not approximated.

### 9.2 Cloud masking by sensor

| Source | Cloud / shadow mask |
| ------ | ------------------- |
| Sentinel-2 | SCL scene-classification layer (+ optional s2cloudless). |
| Landsat 8/9 | QA_PIXEL (CFMask) cloud + cloud-shadow + cirrus bits. |
| ResourceSat-2A | No standard product and no thermal/cirrus band → custom mask (spectral thresholds + spatial/temporal heuristics). Until validated, ResourceSat field-cloud % carries a `confidence: unknown` flag. |

Usable-pixel percentage (step 16) is computed after the sensor-specific mask is applied.

---

## 10. Cloud and Quality Policy

Cloud handling should be done at two levels:

1. **Scene-level quality**
2. **Field/AOI-level quality**

### 10.1 Default Policy

| Quality Check               | Default Rule                                             |
| --------------------------- | -------------------------------------------------------- |
| Preferred scene cloud cover | ≤ 20%                                                    |
| Field-level cloud cover     | ≤ 20%                                                    |
| Field usable pixels         | ≥ 80%                                                    |
| If scene cloud > 20%        | Do not automatically reject; check field/AOI-level cloud |
| If field cloud > 20%        | Mark as low-quality or unavailable                       |
| Cloud mask source           | Sensor-specific: S2 SCL, Landsat QA_PIXEL, ResourceSat custom (confidence unknown until validated) |
| Scene selection             | Best-scene rule, window ± 7 days, quality-first (see §8.3) |
| Minimum field pixels        | Field must contain ≥ a configured count of valid pixels for the source's resolution; below the floor, return a mixed-pixel / low-confidence warning (see §10.4) |
| If no valid optical scene in ± 7 days | Try other optical sources in window; if none, status UNAVAILABLE (SAR context is Phase 6, not MVP) |

### 10.2 Why Scene-Level Alone Is Not Enough

A full satellite scene may have more than 20% cloud cover, but the user’s 2-acre or 2-hectare field may still be clear. In that case, rejecting the whole scene would waste usable data.

So the rule should be:

```text
Scene cloud <= 20%:
  Accept for processing.

Scene cloud > 20%:
  Process only if AOI/field-level usable pixel percentage is acceptable.

Field cloud > 20%:
  Do not show as normal crop-health output.
  Return low-confidence / unavailable status.
```

### 10.3 Fallback Strategy

When a field is cloudy:

1. Try another scene from the same satellite near the same date.
2. Try another optical source.
3. Use the nearest valid date within the ± 7-day window (no wider).
4. For monsoon/cloudy conditions, use SAR-based context separately (Phase 6; not in MVP — MVP returns UNAVAILABLE).
5. Never silently interpolate or fill values without marking them as estimated.

### 10.4 Small-field reliability

Field statistics are only as reliable as the pixel count inside the polygon. A 2-acre field is a handful of pixels at ResourceSat LISS-3 (23.5 m) or AWiFS (56 m). The API therefore:

1. Computes valid (unmasked) pixel count per field per source.
2. Enforces a configurable minimum-valid-pixel floor; below it, results carry a `mixed_pixel` / low-confidence warning.
3. Reports the source resolution so the caller can judge suitability (prefer Sentinel-2 10 m or ResourceSat LISS-4 5–5.8 m for very small fields).

---

## 11. Index Engine

The index engine must be **band-aware** and **source-aware**.

Not every satellite supports every index. The system must validate whether required bands are present before calculating an index. For example, NDRE and RECI require Red Edge, while NDMI and NDBI require SWIR. The satellite catalogue explicitly tracks spectral band availability across satellites, including Red, Green, NIR, Red Edge, SWIR, Thermal, and SAR bands.

### Source × Index capability matrix (band-aware)

Before calculating any index, the engine validates that the required bands exist for that specific `sourceId`/instrument. Unsupported `(source, index)` pairs are rejected, never silently approximated. For MVP sources:

| Source (instrument) | Bands | Producible MVP indices | Not possible | Processing res |
| ------------------- | ----- | ---------------------- | ------------ | -------------- |
| Sentinel-2 L2A | Coastal, B, G, R, RedEdge, NIR, SWIR | NDVI, MSAVI, NDMI, NDBI, NDRE, RECI (+ SAVI, EVI, GNDVI, NDWI) | — | 10 m (NDVI/MSAVI), 20 m (red-edge/SWIR) |
| ResourceSat-2A LISS-4 | G, R, NIR | NDVI, MSAVI, SAVI, NDWI, GNDVI | NDMI/NDBI (no SWIR), NDRE/RECI (no Red Edge) | 5–5.8 m |
| ResourceSat-2A LISS-3 | G, R, NIR, SWIR | NDVI, MSAVI, SAVI, NDWI, NDMI, NDBI | NDRE/RECI (no Red Edge) | 23.5 m |
| ResourceSat-2A AWiFS | G, R, NIR, SWIR | NDVI, MSAVI, SAVI, NDWI, NDMI, NDBI | NDRE/RECI (no Red Edge) | 56 m |
| Landsat 8/9 C2 L2 | Coastal, B, G, R, NIR, SWIR, Thermal | NDVI, MSAVI, NDMI, NDBI (+ SAVI, EVI, GNDVI, NDWI, NBR) | NDRE/RECI (no Red Edge) | 30 m |

> NDRE and RECI require a Red Edge band, which among MVP sources only Sentinel-2 has. They are **not** producible from any ResourceSat instrument or from Landsat.

## 11.1 Initial Required Indices

| Index | Formula                                                 | Required Bands | Purpose                               |
| ----- | ------------------------------------------------------- | -------------- | ------------------------------------- |
| NDVI  | `(NIR - Red) / (NIR + Red)`                             | NIR, Red       | General vegetation health             |
| NDRE  | `(NIR - RedEdge) / (NIR + RedEdge)`                     | NIR, Red Edge  | Chlorophyll/stress detection          |
| MSAVI | `(2*NIR + 1 - sqrt((2*NIR + 1)^2 - 8*(NIR - Red))) / 2` | NIR, Red       | Vegetation in exposed soil areas      |
| RECI  | `(NIR / RedEdge) - 1`                                   | NIR, Red Edge  | Chlorophyll concentration proxy       |
| NDMI  | `(NIR - SWIR) / (NIR + SWIR)`                           | NIR, SWIR      | Crop/canopy moisture stress           |
| NDBI  | `(SWIR - NIR) / (SWIR + NIR)`                           | SWIR, NIR      | Built-up/non-crop area identification |

> These formulas are universal, but per-source availability is constrained by the capability matrix above — e.g., in the MVP, NDRE/RECI run only on Sentinel-2, and NDMI/NDBI need SWIR (so not on ResourceSat LISS-4).

## 11.2 Advanced Indices to Add Later

| Index        | Formula                                            | Required Bands   | Use                          |
| ------------ | -------------------------------------------------- | ---------------- | ---------------------------- |
| SAVI         | `((NIR - Red) / (NIR + Red + L)) * (1 + L)`        | NIR, Red         | Soil-adjusted vegetation     |
| EVI          | `2.5 * (NIR - Red) / (NIR + 6*Red - 7.5*Blue + 1)` | NIR, Red, Blue   | Dense vegetation correction  |
| EVI2         | `2.5 * (NIR - Red) / (NIR + 2.4*Red + 1)`          | NIR, Red         | EVI alternative without Blue |
| GNDVI        | `(NIR - Green) / (NIR + Green)`                    | NIR, Green       | Greenness/chlorophyll        |
| NDWI         | `(Green - NIR) / (Green + NIR)`                    | Green, NIR       | Water body detection         |
| LSWI         | `(NIR - SWIR) / (NIR + SWIR)`                      | NIR, SWIR        | Leaf/canopy water            |
| NBR          | `(NIR - SWIR2) / (NIR + SWIR2)`                    | NIR, SWIR2       | Burn/stress analysis         |
| CI Green     | `(NIR / Green) - 1`                                | NIR, Green       | Chlorophyll proxy            |
| VCI          | `(NDVI - NDVI_min) / (NDVI_max - NDVI_min)`        | NDVI time series | Vegetation condition anomaly |
| NDVI Anomaly | `Current NDVI - historical baseline NDVI`          | NDVI time series | Stress detection over time   |

## 11.3 SAR-Specific Outputs

SAR should be handled separately.

| SAR Output          | Source              | Purpose                     |
| ------------------- | ------------------- | --------------------------- |
| VV backscatter      | Sentinel-1 / EOS-04 | Surface roughness/moisture  |
| VH backscatter      | Sentinel-1 / EOS-04 | Vegetation structure        |
| VV/VH ratio         | Sentinel-1 / EOS-04 | Crop/soil moisture patterns |
| SAR RVI             | Sentinel-1 dual-pol | Radar vegetation proxy      |
| Coherence           | SAR pairs           | Change/flood/disturbance    |
| Flood mask          | SAR                 | Flood extent                |
| Soil moisture proxy | SAR                 | Irrigation/soil condition   |

SAR outputs should not be shown as NDVI replacement. They should be labelled clearly as SAR-derived indicators.

---

## 12. Output Format

The final output should not simply be a normal TIFF. The recommended output is:

1. **Cloud Optimized GeoTIFF**

   * For NDVI, NDRE, MSAVI, RECI, NDMI, NDBI, and other raster indices.
   * Best for TiTiler and map tile serving.
   * Encoding standard: Int16 scaled by 10,000 (or Float32), explicit nodata value, internal mask/alpha, ZSTD or DEFLATE compression, 512×512 internal tiles, internal overviews, validated with `rio cogeo validate`.

2. **JSON statistics**

   * For field-level API response.

3. **Tile URL / TileJSON**

   * For UI rendering.

4. **Metadata response**

   * Source, date, satellite, cloud percentage, quality score, resolution.

### Example API Response

```json
{
  "fieldId": "field_123",
  "index": "NDVI",
  "requestedDate": "2026-06-30",
  "selectedSceneDate": "2026-06-28",
  "source": "sentinel-2-l2a",
  "resolution": {
    "nativeMeters": 10,
    "processingMeters": 10,
    "displayMeters": 10
  },
  "layerId": "ndvi-s2-2026-06-28-7f3a9c",
  "tileUrl": "https://akasha-domain/tiles/{layerId}/{z}/{x}/{y}.png?sig=...",
  "statsUrl": "https://akasha-domain/analytics/field-index/{queryId}",
  "selection": {
    "window": "±7d",
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
  "versions": {
    "atmosphericCorrection": "vendor-L2A",
    "cloudMask": "scl-v1",
    "formula": "ndvi-v1"
  },
  "quality": {
    "status": "GOOD",
    "confidence": 0.91,
    "reason": "Field cloud cover within threshold"
  }
}
```

> The external response never includes internal storage paths (`s3://…`). Clients receive an opaque `layerId` plus signed, time-limited `tileUrl`/`statsUrl`.

---

## 13. API Requirements

All external endpoints (the analytics surface the separate UI calls) require authentication — API keys, OAuth2, or mTLS — plus per-client rate limits, a maximum request-geometry size, and audit logging. Internal/admin endpoints are network-isolated and separately authorized. Field geometry is accepted in EPSG:4326. Tile and stats URLs returned to clients are signed and time-limited; internal storage paths are never exposed.

### 13.1 Source Registry API

```text
GET /sources
```

Returns enabled/disabled satellites, source status, supported indices, resolution, revisit, and provider.

### 13.2 Manual Sync API

```text
POST /ingestion/sync
```

Input:

```json
{
  "sourceId": "resourcesat-2a-liss3-boa",
  "aoiId": "bangalore-60km",
  "fromDate": "2026-01-01",
  "toDate": "2026-06-30",
  "mode": "FULL_PIPELINE",
  "forceRedownload": false
}
```

### 13.3 Job Status API

```text
GET /jobs/{jobId}
GET /jobs?status=failed&page=1&pageSize=50
```

### 13.4 Field Index API

```text
POST /analytics/field-index
```

Input:

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

Returns tile URL, stats, source, date, cloud score, and confidence. If no scene satisfies the ± 7-day best-scene rule (§8.3), it returns an explicit unavailable response instead of guessing:

```json
{
  "status": "UNAVAILABLE",
  "index": "NDVI",
  "requestedDate": "2026-08-15",
  "reason": "No optical scene with field usable-pixels ≥ 80% within ± 7 days",
  "searchedSources": ["sentinel-2-l2a", "resourcesat-2a-liss3-boa", "landsat-9-c2-l2"]
}
```

### 13.5 Time-Series API

```text
POST /analytics/field-timeseries
```

Returns NDVI/NDMI/etc. trend across available dates.

---

## 14. Database Model

Core tables (aligned with the catalogue §8 scheduler fields and the per-scene serving model):

```text
satellite_sources
  id
  catalog_slug                -- e.g. resourcesat-2a
  catalog_platform            -- e.g. ResourceSat-2A
  source_id                   -- e.g. resourcesat-2a-liss3-boa
  provider                    -- e.g. ISRO/NRSC
  provider_adapter            -- bhoonidhi | cdse | usgs | earthdata | vendor
  satellite_name
  instrument_mode             -- LISS-3 / LISS-4 / AWiFS
  product_family
  product_variant
  product_type
  analysis_level              -- TOA / BOA / L2A / C2L2
  resolution
  revisit_days
  bands                       -- per-instrument band list
  supported_indices           -- per-instrument (band-aware)
  access_type
  status
  schedule_state              -- routine | gated | archive-only | blocked
  product_exposure            -- disabled until validated
  commercial_state
  validation_profile
  processing_profile
  credential_ref              -- FK -> source_credentials
  license_profile

source_credentials
  id
  provider_adapter            -- bhoonidhi | cdse | usgs | earthdata | vendor
  secret_ref                  -- pointer to Vault/Docker secret (never the secret itself)
  rotated_at
  status

aoi_registry
  id
  name
  geometry
  srid
  buffer_km
  active

provider_scenes
  id
  source_id                   -- FK -> satellite_sources
  aoi_id                      -- FK -> aoi_registry (nullable; intersect-derived)
  provider_product_id
  acquisition_date
  scene_geometry
  native_crs
  native_resolution
  orbit_path_row_tile
  cloud_percentage
  coverage_percentage
  product_level
  status                      -- see provider_orders state machine
  checksum
  file_size_bytes
  raw_path
  metadata_json

provider_orders               -- access/staging state machine
  id
  scene_id                    -- FK -> provider_scenes
  provider_order_id
  state                       -- discovered|ordered|accepted|rejected|staged|downloaded|verified|expired|failed
  download_url
  url_expires_at
  requested_at
  updated_at

scene_assets                  -- per-band / per-file assets of a scene
  id
  scene_id                    -- FK -> provider_scenes
  asset_role                  -- band name / mask / metadata
  path
  checksum
  dtype

processing_jobs
  id
  job_type
  source_id
  scene_id
  params_json
  idempotency_key
  status
  started_at
  completed_at
  error_message
  retry_count

raster_outputs                -- one per scene per index (per-scene COG baseline)
  id
  scene_id                    -- FK -> provider_scenes
  index_name
  cog_path
  dtype
  scale_factor
  offset
  nodata_value
  min_value
  max_value
  native_resolution
  processing_resolution
  display_resolution
  crs
  atmospheric_correction_version
  cloud_mask_version
  formula_version
  generated_at

aoi_mosaics                   -- optional cached AOI views / MosaicJSON
  id
  aoi_id
  index_name
  composite_date
  method                      -- mosaicjson | composite-rule
  mosaic_path_or_json
  generated_at

mosaic_scene_members
  mosaic_id                   -- FK -> aoi_mosaics
  scene_id                    -- FK -> provider_scenes

aoi_scene_coverage            -- which scenes cover which AOI per date
  aoi_id
  scene_id
  coverage_percentage
  acquisition_date

tile_layers                   -- opaque layerId registry for serving
  layer_id
  raster_output_id            -- nullable FK -> raster_outputs
  mosaic_id                   -- nullable FK -> aoi_mosaics
  -- CHECK: exactly one of (raster_output_id, mosaic_id) is non-null
  signed_url_policy
  created_at

field_queries
  id
  field_geometry
  crs
  index_name
  requested_date
  selected_scene_id           -- FK -> provider_scenes
  raster_output_id            -- nullable FK -> raster_outputs
  mosaic_id                   -- nullable FK -> aoi_mosaics
  -- CHECK: exactly one of (raster_output_id, mosaic_id) is non-null
  valid_pixel_count
  selection_reason
  stats_json
  quality_json
  created_at
```

---

## 15. On-Prem Hardware Recommendation

Because the AOI is around Bangalore 60 km radius and production will eventually retain one year of raw data, this should not be designed as a tiny VM.

### 15.1 MVP Server

| Component         | Minimum MVP       |
| ----------------- | ----------------- |
| CPU               | 16 cores          |
| RAM               | 64 GB             |
| NVMe scratch disk | 1–2 TB            |
| Object storage    | 4–8 TB usable     |
| Network           | 1 Gbps preferred  |
| OS                | Ubuntu Server LTS |
| Deployment        | Docker Compose    |
| GPU               | Not required      |

### 15.2 Production Server

| Component         | Recommended Production                                 |
| ----------------- | ------------------------------------------------------ |
| CPU               | 32 cores                                               |
| RAM               | 128 GB                                                 |
| NVMe scratch disk | 2–4 TB                                                 |
| Object storage    | 16–32 TB usable, expandable                            |
| Storage type      | RAID/ZFS or equivalent redundancy                      |
| Network           | 1 Gbps minimum                                         |
| Backup storage    | Separate NAS/object backup                             |
| GPU               | Not required for index pipeline; optional later for ML |
| OS                | Ubuntu Server LTS                                      |

Storage should be finalized after the first 6-month Bangalore backfill because actual provider product size may vary depending on whether you receive clipped AOI data or full scenes.

> **Compute note:** custom ResourceSat atmospheric correction (6S/Py6S) and multi-scene AOI mosaicking are CPU- and scratch-intensive. Size cores and NVMe scratch toward the upper end of the MVP range, and finalize after the Phase 0 sample-product spike and first backfill.

---

## 16. Production Deployment Requirements

Production must support:

1. Internet access from on-prem server.
2. Static public IP for provider whitelisting.
3. Bhoonidhi/NRSC credentials stored securely.
4. MinIO storage with backup.
5. PostgreSQL/PostGIS backup.
6. Docker Compose deployment.
7. Health checks for API, workers, queue, DB, MinIO, and TiTiler.
8. Job retry and resume.
9. Operator dashboard/logs.
10. Disk usage monitoring.
11. Raw data retention cleanup job.
12. TLS/HTTPS for APIs.
13. Access control for internal/admin APIs.
14. No public access to raw MinIO buckets.
15. Audit logs for data download and processing jobs.
16. CDSE, USGS/M2M, and Earthdata credentials in addition to Bhoonidhi/NRSC.
17. Provider order/staging handling with download-link expiry and resume.
18. External API authentication, rate limits, and signed tile/stats URLs.
19. License/product-exposure enforcement before public serving (Copernicus/USGS/NRSC terms).
20. Secret rotation and log redaction for all provider credentials.

---

## 17. Implementation Phases

Phases are **ordered and gated**, not time-boxed. Each phase has an entry gate (what must be true to start) and an exit gate (what must be demonstrably true to finish). Sequencing reflects two decisions: Sentinel-2 proves the pipeline first, and ResourceSat (with its custom atmospheric correction) is built in parallel as the India differentiator.

### Phase 0 — Setup, Access & Sample-Product Spike

Entry gate: project start.

Deliverables:

* Confirm exact Bangalore AOI coordinates/polygon and a clear-season backfill window.
* Confirm on-prem server specs and production static IP + Bhoonidhi whitelisting process.
* Provision and validate all provider accounts: Bhoonidhi/NRSC, CDSE (Sentinel-2), USGS/M2M (Landsat), Earthdata (MODIS).
* Confirm MVP source list, ± 7-day best-scene policy, cloud thresholds, and retention policy.
* **Spike: pull 3–5 sample products per source** and document what each actually delivers — product level (TOA/BOA/L2A/C2L2), ResourceSat BOA availability, native cloud masks, band layout, scene size — to ground the AC/masking effort and storage sizing.

Exit gate: every MVP source's real product characteristics documented; credentials proven; storage sizing input captured.

---

### Phase 1 — Core Platform Foundation

Entry gate: Phase 0 exit.

Deliverables:

* Docker Compose base; PostgreSQL/PostGIS; MinIO; FastAPI; Worker; Queue/scheduler; TiTiler.
* Database schema per §14 (sources with catalogue §8 fields, AOI registry, jobs, provider_orders, scene_assets, source_credentials).
* Secret store wired from day one (per-provider credentials, rotation, log redaction).

Exit gate: all services healthy; schema migrated; secrets resolving.

---

### Phase 2 — Sentinel-2 Vertical Slice (first end-to-end)

Entry gate: Phase 1 exit + CDSE validation passed.

Deliverables:

* CDSE/Sentinel-2 adapter; AOI search; download with integrity (checksum, resume).
* Raw + per-band asset storage; metadata; SCL cloud/shadow mask.
* Index calc: NDVI, MSAVI, NDMI, NDBI, **NDRE, RECI** (Sentinel-2 has Red Edge); COG encoding standard; raster output catalogue.
* TiTiler serving; field polygon clipping; zonal statistics; ± 7-day best-scene selection (§8.3); field-index API incl. UNAVAILABLE.
* 6-month clear-season Bangalore backfill for Sentinel-2.

Exit gate: a field polygon returns tile + stats + quality end to end for Sentinel-2.

---

### Phase 3 — ResourceSat Ingestion + Atmospheric Correction (parallel)

Entry gate: Phase 1 exit + Phase 0 ResourceSat spike (runs in parallel with Phase 2).

Deliverables:

* Bhoonidhi adapter + provider order/staging state machine; per-instrument source config (LISS-3/LISS-4/AWiFS).
* AOI search + multi-scene same-date coverage (MosaicJSON, no persistent AOI date-mosaic); download integrity; duplicate detection.
* **Atmospheric correction** (DOS interim → 6S/Py6S) and **custom cloud mask**.
* Per-instrument band mapping + index calc (NDVI/MSAVI/NDMI/NDBI where bands exist; no NDRE/RECI).
* **Cross-validation of ResourceSat SR/NDVI vs Sentinel-2** on overlapping clear dates; 6-month ResourceSat backfill.

Exit gate: ResourceSat surface reflectance + NDVI within tolerance of Sentinel-2; outputs exposure-gated until the check passes.

---

### Phase 4 — Landsat 8/9 + Cross-Source Selection & Time-Series

Entry gate: Phase 2 exit.

Deliverables:

* USGS adapter (Collection-2 L2 SR + QA_PIXEL); band mapping.
* Integrate Landsat into best-scene selection across Sentinel-2 / ResourceSat / Landsat.
* Time-series API with per-point sensor tags; mixed-sensor harmonization flags/notes.

Exit gate: multi-source best-scene selection is deterministic; time-series returns sensor-tagged points.

---

### Phase 5 — Scheduler & Automation

Entry gate: Phases 2–4 exit.

Deliverables:

* Revisit-aware scheduler (per-source cadence); retry policy; failed-job dashboard.
* Auto-download / auto-process new scenes; register new per-scene COGs (no precomputed AOI mosaic).
* Provider quota / rate-limit handling.

Exit gate: new scenes are auto-ingested and immediately queryable.

---

### Phase 6 — SAR & Advanced Sources (later)

Entry gate: MVP accepted.

Deliverables:

* Sentinel-1 SAR pipeline; EOS-04 SAR pipeline; backscatter preprocessing; VV/VH ratio; SAR RVI.
* Flood / soil-moisture proxy layers; NISAR support once products are validated; commercial source placeholders.

---

### Phase 7 — Production Hardening

Entry gate: Phases 2–5 exit.

Deliverables:

* On-prem deployment; static IP whitelisting validation; backup/restore; monitoring; disk cleanup.
* External API authentication, signed tile/stats URLs, rate limits; security hardening; license/exposure enforcement.
* Load testing; end-to-end dry run; production runbook.

---

## 18. Acceptance Criteria

The MVP is successful when:

1. The system can backfill 6 months of clear-season satellite data for the Bangalore 60 km AOI.
2. **Sentinel-2 ingestion → index → field query works end to end first** (the proving vertical slice); ResourceSat ingestion works end to end as the parallel India track.
3. Raw products and per-band assets are stored safely in MinIO with verified checksums.
4. Metadata and provenance are stored in PostGIS.
5. Duplicate downloads are avoided.
6. Bands are extracted and mapped correctly per source/instrument.
7. **Per-sensor cloud/quality masks are applied** (Sentinel-2 SCL, Landsat QA_PIXEL, ResourceSat custom).
8. **NDVI is generated correctly, first from Sentinel-2.**
9. NDMI, MSAVI, and NDBI are generated only where the source/instrument has the required bands.
10. NDRE and RECI are generated only for Red-Edge sources (Sentinel-2 in MVP; not ResourceSat).
11. **ResourceSat surface reflectance is atmospherically corrected and cross-validated against Sentinel-2 within tolerance** before its outputs are exposed.
12. COG outputs follow the encoding standard (dtype/scale/nodata/mask/overviews) and pass `rio cogeo validate`.
13. TiTiler can serve index layers.
14. UI/backend can send a field polygon (EPSG:4326) and index name.
15. API returns an opaque layer reference, signed tile/stats URLs, statistics, source + sensor + date, cloud score, resolution, confidence, and selection reason — **with no internal storage paths leaked**.
16. **Best-scene selection is deterministic within the requested date ± 7 days** (quality-first, nearest-date tie-break).
17. Scenes/fields below the usable-pixel floor return **status UNAVAILABLE** rather than a misleading layer; small fields get a min-valid-pixel / mixed-pixel warning.
18. The external API enforces authentication, rate limits, and geometry-size limits.
19. Scheduler can automatically check, download, and process new data; new per-scene COGs become queryable.
20. Failed jobs are visible and retryable.
21. Outputs are reproducible — formula, AC, mask, and source versions are recorded per raster output.
22. The same pipeline can be reconfigured for another AOI later.
23. The system can run fully on-premises with internet access.
24. Raw data cleanup works according to retention policy.

---

## 19. Remaining Final Inputs Needed

Most major design decisions are now resolved in this document (see §2 and the decisions log). The remaining inputs are operational and needed before/at implementation start:

1. Exact Bangalore AOI coordinates or polygon.
2. Confirmation of the clear-season backfill window for the demo (months with low cloud over Bangalore).
3. Provider account confirmations and whitelisting:

   * Bhoonidhi/NRSC (ResourceSat) — account + production static-IP whitelisting.
   * CDSE (Sentinel-2) — account/credentials.
   * USGS / M2M (Landsat) — account/credentials.
   * Earthdata (MODIS) — account/credentials.
4. Atmospheric-correction ancillary-data source for ResourceSat (aerosol / water-vapour inputs, or DOS-only for the interim).
5. Production server decision and production static IP.
6. Expected number of users / field queries per day (to size API auth + rate limits).
7. Whether processed COGs should be retained beyond 1 year.
8. Crop-specific threshold requirements, if any.
9. Whether the first surface is only the API response or also an internal operator dashboard.

> MVP source activation is already decided in §2: Sentinel-2 (Phase 2, first), ResourceSat-2A (Phase 3, parallel), and Landsat 8/9 (Phase 4) are all in the MVP; Sentinel-1/SAR is deferred to Phase 6. So source activation is no longer an open input.

---

## 20. Final One-Line Requirement

Build an on-premises, internet-enabled, configurable satellite ingestion and vegetation-index processing platform for Indian agricultural AOIs, starting with Bangalore 60 km and 6 months of clear-season history — proving the pipeline on Sentinel-2 first while building atmospherically-corrected ResourceSat in parallel as the India differentiator — generating analysis-ready per-scene COG index layers with per-sensor cloud masking and deterministic ± 7-day best-scene selection, computing each index only where a source's bands support it, preserving raw data and full provenance for a defined retention window, and exposing field-level NDVI/NDRE/MSAVI/RECI/NDMI/NDBI analytics through an authenticated API and TiTiler for a separate UI application.
