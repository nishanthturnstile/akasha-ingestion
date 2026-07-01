# Phase 0 Status

Last updated: 2026-07-01 22:46 IST

This document summarizes the current execution status for Phase 0: setup, access, and sample-product spike.

## Overall status

| Status | Count | Workstreams |
| --- | ---: | --- |
| Complete | 2 | AOI/demo inputs; Azure VM setup and egress |
| Blocked | 1 | Provider account and access-flow validation |
| Pending | 5 | Sample downloads; product characteristics; ResourceSat AC feasibility; storage/compute sizing; Phase 0 exit handoff |

Phase 0 is partially complete. The main active blocker is authenticated provider validation.

## Workstream status

| Workstream | Status | Evidence / notes |
| --- | --- | --- |
| A. AOI and demo inputs | Complete | `bangalore-aoi.geojson` now contains the authoritative geodesic 60 km AOI, bbox envelope, demo window, clear-season window, and three real sample fields. See `aoi-demo-inputs.md`. |
| B. Azure Linux development VM | Complete | `akasha-staging` validated with Ubuntu 24.04, Docker/Compose, `/srv/akasha` data disk, provider egress, Phase 0 directories, and localhost-bound node exporter. See `vm-bootstrap-notes.md`. |
| C. Provider account and access flows | Blocked | No-secret checks are complete. Authenticated validation is blocked until credentials/tokens are entered on `akasha-staging`. See `provider-access-validation.md` and `provider-access-matrix.csv`. |
| D. Sample product downloads | Pending | Depends on provider authenticated validation. |
| E. Product characteristics | Pending | Depends on downloaded samples. |
| F. ResourceSat AC feasibility | Pending | Depends on ResourceSat samples and metadata inspection. |
| G. Storage and compute sizing | Pending | Depends on measured sample product sizes and inspection/runtime notes. |
| H. Phase 0 exit handoff | Pending | Depends on provider access, samples, product characteristics, ResourceSat AC feasibility, and sizing. |

## Completed decisions

| Decision | Current value |
| --- | --- |
| Authoritative AOI geometry | Geodesic 60 km polygon derived from the Bangalore center point |
| Provider search envelope | Existing bbox retained as envelope: `[77.023647, 12.537266, 78.131561, 13.61645]` |
| Demo date range | `2026-01-01` to `2026-06-30` |
| Clear-season sample window | `2026-01-15` to `2026-04-15` |
| Sample fields | Three user-provided real polygons; no synthetic fields |
| Small-field note | Smallest current real field is approximately 18.4683 ha; sub-hectare field validation remains pending unless supplied later |
| Azure VM | Existing `akasha-staging` VM is used for Phase 0 validation |
| VM egress IP | `20.219.3.35` |
| VM data/scratch paths | `/srv/akasha/raw-samples`, `/srv/akasha/scratch`, `/srv/akasha/logs` |
| Provider secret handling | VM-only env file: `/srv/akasha/secrets/provider-validation.env`, mode `600` |
| Coolify usage | Keep `akasha-control` / Coolify for frontend/simple web deployments only; do not use it for ingestion stack or provider validation |

## Provider access status

| Provider | Status | Current evidence | Blocker / next action |
| --- | --- | --- | --- |
| Bhoonidhi/NRSC | Network validated, auth pending | `akasha-staging` reaches Bhoonidhi over HTTPS; egress IP is `20.219.3.35` | Enter Bhoonidhi credentials directly in `/srv/akasha/secrets/provider-validation.env`, then run VM-only auth/search/order/download validation |
| CDSE | Catalogue validated, blocked on credentials | Anonymous OData query returned Sentinel-2 L2A products for AOI/window locally and from VM | Create/provide CDSE account credentials |
| USGS/M2M | Catalogue validated, blocked on credentials | Anonymous LandsatLook STAC query returned Landsat C2 L2 products for AOI/window locally and from VM | Create/provide USGS account and M2M API token/access |
| Earthdata | Network validated, blocked on credentials | URS endpoint reachable locally and from VM | Create/provide Earthdata Login credentials; auth readiness only for Phase 0 |

## Current blockers

1. Authenticated provider validation cannot complete until credentials/tokens are available on the VM.
2. CDSE, USGS/M2M, and Earthdata accounts/tokens are not yet available.
3. Bhoonidhi credentials are available manually but not yet entered/validated on `akasha-staging`.
4. Sample downloads cannot start until the relevant provider validation is complete.
5. Product characteristics, ResourceSat AC feasibility, and sizing remain blocked by missing downloaded samples.

## Files updated or added in Phase 0

| File | Purpose |
| --- | --- |
| `docs/phase-0/bangalore-aoi.geojson` | AOI, bbox envelope, demo/clear-season window, and sample-field geometries |
| `docs/phase-0/aoi-demo-inputs.md` | Human-readable AOI and demo input summary |
| `docs/phase-0/vm-bootstrap-notes.md` | Azure VM setup, validation evidence, gaps, and Phase 1 Ansible conversion notes |
| `docs/phase-0/provider-access-matrix.csv` | Provider validation status matrix |
| `docs/phase-0/provider-access-validation.md` | Provider validation runbook, no-secret evidence, and credential handling |
| `docs/phase-0/coolify-assessment.md` | Coolify usage recommendation and boundary |
| `docs/phase-0/phase0-status.md` | Current status summary |

## Immediate next steps

1. Enter Bhoonidhi credentials directly on `akasha-staging` in `/srv/akasha/secrets/provider-validation.env`.
2. Create/provide CDSE account credentials.
3. Create/provide USGS/M2M API token and confirm M2M access is enabled.
4. Create/provide Earthdata credentials.
5. Resume Workstream C authenticated validation.
6. Start Workstream D sample downloads once each provider reaches the required validation status.

