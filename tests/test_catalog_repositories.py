from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from akasha.catalog.asset_repository import InMemorySceneAssetRepository, SceneAssetRecord
from akasha.catalog.backfill_repository import BackfillRunRecord, InMemoryBackfillRepository
from akasha.catalog.field_query_repository import FieldQueryRecord, InMemoryFieldQueryRepository
from akasha.catalog.raster_repository import InMemoryRasterRepository, RasterOutputRecord
from akasha.catalog.scene_repository import (
    InMemorySceneRepository,
    ProviderSceneRecord,
    _row_to_scene,
)


def test_memory_scene_and_asset_repositories_upsert_deterministically() -> None:
    scenes = InMemorySceneRepository()
    assets = InMemorySceneAssetRepository()
    scene = ProviderSceneRecord(
        id=None,
        provider_adapter="earthsearch",
        source_id="sentinel-2-l2a",
        provider_product_id="S2A_001",
        acquisition_at=datetime(2026, 1, 15, tzinfo=UTC),
        logical_scene_key="sentinel-2-l2a:S2A_001",
    )

    first_scene = scenes.upsert(scene)
    second_scene = scenes.upsert(scene)
    asset = assets.upsert(
        SceneAssetRecord(
            id=None,
            scene_id=first_scene.id or "",
            asset_kind="source",
            asset_key="red",
            asset_href="https://earth-search.test/S2A_001/red.tif",
            storage_backend="https",
            selected_access_mode="public_https",
            mirror_status="pending",
        )
    )

    assert first_scene.id == second_scene.id
    assert asset.id
    assert assets.list_for_scene(first_scene.id or "") == [asset]


def test_memory_field_query_cache_enforces_expiry() -> None:
    repository = InMemoryFieldQueryRepository()
    active = repository.save(
        FieldQueryRecord(
            query_id="active",
            field_geometry={"type": "Polygon", "coordinates": []},
            index_name="sar_backscatter:history",
            requested_date=date(2026, 7, 17),
            selected_scene_id="scene-1",
            selection_reason="test",
            geometry_hash="geometry",
            analysis_version="v1",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    repository.save(
        FieldQueryRecord(
            query_id="expired",
            field_geometry={"type": "Polygon", "coordinates": []},
            index_name="sar_backscatter:history",
            requested_date=date(2026, 7, 1),
            selected_scene_id="scene-1",
            selection_reason="test",
            geometry_hash="geometry",
            analysis_version="v1",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )

    cached = repository.find_cached(
        selected_scene_id="scene-1",
        geometry_hash="geometry",
        index_name="sar_backscatter:history",
        analysis_version="v1",
    )

    assert cached == active
    assert repository.get("expired") is None
    assert repository.delete_expired() == 1


def test_memory_asset_repository_preserves_existing_mirror_on_registration_upsert() -> None:
    repository = InMemorySceneAssetRepository()
    mirrored = repository.upsert(
        SceneAssetRecord(
            id=None,
            scene_id="scene-1",
            asset_kind="source",
            asset_key="red",
            asset_href="https://earth-search.test/S2A_001/red.tif",
            mirror_status="mirrored",
            mirror_object_path="raw/earthsearch/sentinel-2-l2a/S2A_001/source-cogs/red.tif",
            mirror_checksum_sha256="abc123",
            size_bytes=123,
        )
    )

    registered_again = repository.upsert(
        SceneAssetRecord(
            id=None,
            scene_id="scene-1",
            asset_kind="source",
            asset_key="red",
            asset_href="https://earth-search.test/S2A_001/red.tif",
            mirror_status="pending",
        )
    )

    assert registered_again.id == mirrored.id
    assert registered_again.mirror_status == "mirrored"
    assert registered_again.mirror_object_path == mirrored.mirror_object_path
    assert registered_again.mirror_checksum_sha256 == "abc123"
    assert registered_again.size_bytes == 123


def test_memory_raster_repository_requires_deterministic_identity() -> None:
    repository = InMemoryRasterRepository()

    with pytest.raises(ValueError, match="deterministic identity"):
        repository.upsert_derived_index(
            RasterOutputRecord(
                id=None,
                scene_id="scene-1",
                output_kind="derived_index",
                object_path="indices/earthsearch/sentinel-2-l2a/S2A_001/ndvi.cog.tif",
            )
        )

    output = repository.upsert_derived_index(
        RasterOutputRecord(
            id=None,
            scene_id="scene-1",
            output_kind="derived_index",
            index_name="ndvi",
            object_path="indices/earthsearch/sentinel-2-l2a/S2A_001/ndvi.cog.tif",
            formula_version="ndvi-s2-v1",
            processing_profile_version="sentinel2-l2a-earthsearch-v1",
            processing_resolution=10,
        )
    )

    assert output.id


def test_memory_backfill_repository_tracks_run_by_job() -> None:
    repository = InMemoryBackfillRepository()
    run = repository.upsert(
        BackfillRunRecord(
            id=None,
            job_id="job-1",
            source_id="sentinel-2-l2a",
            aoi_id="bangalore_60km_geodesic_aoi",
            date_start=date(2026, 1, 1),
            date_end=date(2026, 6, 30),
            searched_count=5,
            summary_json={"stac_item_ids": ["S2A_001"]},
        )
    )

    assert repository.get_by_job("job-1") == run


def test_database_scene_row_mapping_preserves_geojson_geometry() -> None:
    row = _Row(
        id="scene-1",
        provider_adapter="earthsearch",
        source_id="sentinel-2-l2a",
        provider_product_id="S2A_001",
        acquisition_at=datetime(2026, 1, 15, tzinfo=UTC),
        scene_geometry_geojson={"type": "Polygon", "coordinates": []},
        status="accepted",
        cloud_percent=2,
        license_state="open",
        pgstac_item_id=None,
        provider_metadata={},
        aoi_id="bangalore_60km_geodesic_aoi",
        provider_route_id=None,
        logical_scene_key="sentinel-2-l2a:S2A_001",
        native_crs=None,
        native_resolution=None,
        coverage_percentage=None,
        file_size_bytes=None,
        raw_object_path=None,
        created_at=None,
        updated_at=None,
    )

    scene = _row_to_scene(row)

    assert scene.scene_geometry == {"type": "Polygon", "coordinates": []}


class _Row(dict):
    def __getattr__(self, key: str):
        return self[key]
