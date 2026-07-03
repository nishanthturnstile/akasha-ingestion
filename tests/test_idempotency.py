from __future__ import annotations

from akasha.jobs.idempotency import (
    compute_backfill_idempotency_key,
    compute_index_output_idempotency_key,
    compute_sync_idempotency_key,
)


def test_idempotency_key_is_stable() -> None:
    values = {
        "source_id": "sentinel-2-l2a",
        "aoi_id": "bangalore_60km_geodesic_aoi",
        "date_start": "2026-01-15",
        "date_end": "2026-04-15",
        "job_type": "mock_sync",
        "request_params_version": "v1",
        "processing_profile_version": "phase1-mock-v1",
    }

    assert compute_sync_idempotency_key(**values) == compute_sync_idempotency_key(**values)


def test_idempotency_key_changes_with_source() -> None:
    common = {
        "aoi_id": "bangalore_60km_geodesic_aoi",
        "date_start": "2026-01-15",
        "date_end": "2026-04-15",
        "job_type": "mock_sync",
        "request_params_version": "v1",
        "processing_profile_version": "phase1-mock-v1",
    }

    assert compute_sync_idempotency_key(source_id="sentinel-2-l2a", **common) != (
        compute_sync_idempotency_key(source_id="landsat-8-9-c2-l2", **common)
    )


def test_idempotency_key_uses_structured_material() -> None:
    common = {
        "date_start": "2026-01-15",
        "date_end": "2026-04-15",
        "job_type": "mock_sync",
        "request_params_version": "v1",
        "processing_profile_version": "phase1-mock-v1",
    }

    first = compute_sync_idempotency_key(source_id="x", aoi_id="y|z", **common)
    second = compute_sync_idempotency_key(source_id="x|y", aoi_id="z", **common)

    assert first != second


def test_backfill_idempotency_key_includes_provider_route_and_mode() -> None:
    common = {
        "source_id": "sentinel-2-l2a",
        "provider_route": "earthsearch:sentinel-2-l2a",
        "aoi_id": "bangalore_60km_geodesic_aoi",
        "date_start": "2026-01-01",
        "date_end": "2026-06-30",
        "request_params_version": "v1",
        "processing_profile_version": "sentinel2-l2a-earthsearch-v1",
    }

    assert compute_backfill_idempotency_key(mode="metadata_only", **common) != (
        compute_backfill_idempotency_key(mode="full_pipeline", **common)
    )


def test_index_output_idempotency_key_includes_formula_version() -> None:
    common = {
        "source_id": "sentinel-2-l2a",
        "provider_route": "earthsearch:sentinel-2-l2a",
        "stac_item_id": "S2A_001",
        "index_name": "ndvi",
        "request_params_version": "v1",
        "processing_profile_version": "sentinel2-l2a-earthsearch-v1",
    }

    assert compute_index_output_idempotency_key(formula_version="ndvi-s2-v1", **common) != (
        compute_index_output_idempotency_key(formula_version="ndvi-s2-v2", **common)
    )
