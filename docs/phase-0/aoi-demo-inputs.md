# Phase 0 AOI and Demo Inputs

This document records the Workstream A decisions for Phase 0 setup, access, and sample-product spike.

## Authoritative AOI

The authoritative Phase 0 AOI is a geodesic 60 km polygon derived from the user-provided Bangalore center point:

- Center: `[77.5776037099731, 13.076858177177233]`
- CRS: `EPSG:4326`
- Radius: `60 km`
- GeoJSON artifact: `docs/phase-0/bangalore-aoi.geojson`
- Authoritative feature ID: `bangalore_60km_geodesic_aoi`

The rectangular bbox is retained only as a provider-search and validation envelope:

```text
[77.023647, 12.537266, 78.131561, 13.61645]
```

## Demo and clear-season windows

| Input | Value |
| --- | --- |
| Demo date range | `2026-01-01` to `2026-06-30` |
| Clear-season sample window | `2026-01-15` to `2026-04-15` |

## Representative sample fields

The project will use the three user-provided real field polygons as the Phase 0 sample fields for processing and validation. No synthetic field polygon is included.

| Field ID | Source | Approx. area | Notes |
| --- | --- | ---: | --- |
| `bangalore_sample_field_1` | User provided | 22.7457 ha | Inside authoritative AOI |
| `bangalore_sample_field_2` | User provided | 35.1579 ha | Inside authoritative AOI |
| `bangalore_sample_field_3` | User provided | 18.4683 ha | Inside authoritative AOI; smallest provided sample |

The roadmap requested small-field cases. For this Phase 0 sample set, the smallest available real field is approximately 18.4683 ha. Sub-hectare small-field validation should be added later if the team supplies a real small-field polygon.

## Validation summary

- GeoJSON uses lon/lat coordinate order in `EPSG:4326`.
- The authoritative AOI polygon is derived geodesically, not by a planar degree buffer.
- The bbox encloses the derived 60 km AOI and is not treated as the operational AOI.
- All three sample fields are closed polygons.
- All sample-field vertices are inside the 60 km AOI and inside the bbox envelope.
- Approximate sample-field areas were calculated from a local planar approximation around the AOI center for Phase 0 documentation.

