from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pytest
from fastapi.testclient import TestClient
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds

from akasha.api.app import create_app
from akasha.catalog.asset_repository import InMemorySceneAssetRepository, SceneAssetRecord
from akasha.catalog.field_query_repository import InMemoryFieldQueryRepository
from akasha.catalog.raster_repository import InMemoryRasterRepository, RasterOutputRecord
from akasha.catalog.scene_repository import InMemorySceneRepository, ProviderSceneRecord
from akasha.catalog.tile_layer_repository import InMemoryTileLayerRepository
from akasha.config import Environment, RuntimeBackend, Settings
from akasha.schemas import FieldDateAvailability, FieldDatesRequest, FieldIndexRequest
from akasha.security import hash_api_key
from akasha.services.analytics import AnalyticsRasterUnavailable, AnalyticsService
from akasha.storage.object_store import InMemoryObjectStore

_FIELD_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [
        [
            [77.600, 12.950],
            [77.640, 12.950],
            [77.640, 12.990],
            [77.600, 12.990],
            [77.600, 12.950],
        ]
    ],
}
_NODATA = -32768


def _cog(*, valid: bool) -> bytes:
    data = np.full((4, 4), 6500 if valid else _NODATA, dtype="int16")
    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff",
            height=4,
            width=4,
            count=1,
            dtype="int16",
            crs="EPSG:4326",
            transform=from_bounds(77.600, 12.950, 77.640, 12.990, 4, 4),
            nodata=_NODATA,
        ) as dataset:
            dataset.write(data, 1)
        return memory.read()


def _mask_cog(value: int | np.ndarray) -> bytes:
    data = (
        np.full((4, 4), value, dtype="uint8")
        if isinstance(value, int)
        else np.asarray(value, dtype="uint8")
    )
    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff",
            height=4,
            width=4,
            count=1,
            dtype="uint8",
            crs="EPSG:4326",
            transform=from_bounds(77.600, 12.950, 77.640, 12.990, 4, 4),
            nodata=0,
        ) as dataset:
            dataset.write(data, 1)
        return memory.read()


def _build_service() -> tuple[
    AnalyticsService,
    InMemoryFieldQueryRepository,
    InMemoryTileLayerRepository,
]:
    settings = Settings(environment=Environment.TEST, runtime_backend=RuntimeBackend.MEMORY)
    scenes = InMemorySceneRepository()
    rasters = InMemoryRasterRepository()
    assets = InMemorySceneAssetRepository()
    objects = InMemoryObjectStore()
    queries = InMemoryFieldQueryRepository()
    layers = InMemoryTileLayerRepository(raster_repository=rasters, scene_repository=scenes)

    cases = (
        (date(2026, 6, 1), 5.0, True),
        (date(2026, 6, 2), 35.0, True),
        (date(2026, 6, 3), 5.0, False),
    )
    for acquisition_date, cloud_percent, valid in cases:
        scene_id = f"scene-{acquisition_date.isoformat()}"
        scenes.upsert(
            ProviderSceneRecord(
                id=scene_id,
                provider_adapter="earthsearch",
                source_id="sentinel-2-l2a",
                provider_product_id=scene_id,
                acquisition_at=datetime.combine(
                    acquisition_date,
                    datetime.min.time(),
                    tzinfo=UTC,
                ),
                cloud_percent=cloud_percent,
                aoi_id="bangalore_60km_geodesic_aoi",
            )
        )
        object_path = f"indices/{scene_id}/ndvi.cog.tif"
        objects.put_bytes(object_path, _cog(valid=valid))
        mask_path = f"masks/{scene_id}/scl.tif"
        objects.put_bytes(mask_path, _mask_cog(8 if acquisition_date.day == 2 else 4))
        assets.upsert(
            SceneAssetRecord(
                id=None,
                scene_id=scene_id,
                asset_kind="source",
                asset_key="scl",
                mirror_object_path=mask_path,
            )
        )
        rasters.upsert_derived_index(
            RasterOutputRecord(
                id=None,
                scene_id=scene_id,
                output_kind="derived_index",
                object_path=object_path,
                index_name="ndvi",
                formula_version="ndvi-s2-v1",
                processing_profile_version="sentinel2-l2a-earthsearch-v1",
                processing_resolution=10.0,
                scale_factor=10000,
                nodata_value=_NODATA,
            )
        )

    return (
        AnalyticsService(
            field_query_repository=queries,
            settings=settings,
            scene_repository=scenes,
            raster_repository=rasters,
            asset_repository=assets,
            tile_layer_repository=layers,
            object_store=objects,
        ),
        queries,
        layers,
    )


def _request() -> FieldDatesRequest:
    return FieldDatesRequest(
        geometry=_FIELD_GEOMETRY,
        sourceId="sentinel-2-l2a",
        index="NDVI",
        dates=[date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)],
        maxCloudPercentage=20,
    )


def test_field_dates_filters_cloudy_and_low_usable_dates_without_side_effects() -> None:
    service, queries, layers = _build_service()

    response = service.field_dates(_request())

    by_date = {item.acquisitionDate: item for item in response.dates}
    assert by_date[date(2026, 6, 1)].available is True
    assert by_date[date(2026, 6, 1)].usablePixelPercentage == 100.0
    assert by_date[date(2026, 6, 1)].cloudPercentage == 0.0
    assert by_date[date(2026, 6, 1)].fieldCoveragePercentage == 100.0
    assert by_date[date(2026, 6, 2)].available is False
    assert by_date[date(2026, 6, 3)].available is False
    assert queries._queries == {}
    assert layers._layers_by_id == {}


def test_field_index_uses_field_mask_instead_of_global_scene_cloud() -> None:
    service, _queries, _layers = _build_service()
    mask_path = "masks/scene-2026-06-02/scl.tif"
    service._object_store.put_bytes(mask_path, _mask_cog(4))

    response = service.field_index(
        FieldIndexRequest(
            geometry=_FIELD_GEOMETRY,
            sourceId="sentinel-2-l2a",
            index="NDVI",
            date=date(2026, 6, 2),
            maxCloudPercentage=35,
        )
    )

    assert response.status == "AVAILABLE"
    assert response.selectedSceneDate == date(2026, 6, 2)


def test_field_dates_require_at_least_ninety_five_percent_spatial_coverage() -> None:
    service, _queries, _layers = _build_service()
    mask = np.full((4, 4), 4, dtype="uint8")
    mask[0, 0] = 0  # 15/16 covered = 93.75%
    service._object_store.put_bytes("masks/scene-2026-06-01/scl.tif", _mask_cog(mask))

    request = _request().model_copy(update={"dates": [date(2026, 6, 1)]})
    result = service.field_dates(request)

    assert result.dates[0].available is False


def test_field_dates_treat_cloud_and_shadow_as_obscured_pixels() -> None:
    service, _queries, _layers = _build_service()
    mask = np.full((4, 4), 4, dtype="uint8")
    mask[0, :2] = 8
    mask[1, :2] = 3  # 25% combined cloud + shadow
    service._object_store.put_bytes("masks/scene-2026-06-01/scl.tif", _mask_cog(mask))

    request = _request().model_copy(update={"dates": [date(2026, 6, 1)]})
    result = service.field_dates(request)

    assert result.dates[0].available is False


def test_field_dates_rejects_cloud_limit_above_twenty_percent() -> None:
    payload = _request().model_dump(mode="json")
    payload["maxCloudPercentage"] = 35

    with pytest.raises(ValueError, match="less than or equal to 20"):
        FieldDatesRequest.model_validate(payload)


def test_field_dates_rejects_more_than_sixty_four_dates() -> None:
    payload = _request().model_dump(mode="json")
    payload["dates"] = [
        date(2026, 1, 1).fromordinal(date(2026, 1, 1).toordinal() + offset).isoformat()
        for offset in range(65)
    ]

    with pytest.raises(ValueError, match="at most 64"):
        FieldDatesRequest.model_validate(payload)


def test_available_field_date_requires_exact_scene_and_usable_pixels() -> None:
    with pytest.raises(ValueError, match="exact acquisition date"):
        FieldDateAvailability(
            acquisitionDate=date(2026, 6, 2),
            available=True,
            selectedSceneDate=date(2026, 6, 1),
            usablePixelPercentage=95,
            validPixelCount=10,
        )

    with pytest.raises(ValueError, match="field quality percentages"):
        FieldDateAvailability(
            acquisitionDate=date(2026, 6, 2),
            available=True,
            selectedSceneDate=date(2026, 6, 2),
            usablePixelPercentage=95,
            validPixelCount=10,
        )

    with pytest.raises(ValueError, match="less than or equal to 20"):
        FieldDateAvailability(
            acquisitionDate=date(2026, 6, 2),
            available=True,
            selectedSceneDate=date(2026, 6, 2),
            usablePixelPercentage=95,
            cloudPercentage=35,
            fieldCoveragePercentage=100,
            shadowPercentage=0,
            obscuredPercentage=35,
            validPixelCount=10,
        )


def test_field_dates_accepts_unknown_global_cloud_when_field_mask_is_clear() -> None:
    service, _queries, _layers = _build_service()
    acquisition_date = date(2026, 6, 4)
    scene_id = "scene-unknown-cloud"
    service._scene_repository.upsert(
        ProviderSceneRecord(
            id=scene_id,
            provider_adapter="earthsearch",
            source_id="sentinel-2-l2a",
            provider_product_id=scene_id,
            acquisition_at=datetime.combine(
                acquisition_date,
                datetime.min.time(),
                tzinfo=UTC,
            ),
            cloud_percent=None,
            aoi_id="bangalore_60km_geodesic_aoi",
        )
    )
    object_path = f"indices/{scene_id}/ndvi.cog.tif"
    service._object_store.put_bytes(object_path, _cog(valid=True))
    mask_path = f"masks/{scene_id}/scl.tif"
    service._object_store.put_bytes(mask_path, _mask_cog(4))
    service._asset_repository.upsert(
        SceneAssetRecord(
            id=None,
            scene_id=scene_id,
            asset_kind="source",
            asset_key="scl",
            mirror_object_path=mask_path,
        )
    )
    service._raster_repository.upsert_derived_index(
        RasterOutputRecord(
            id=None,
            scene_id=scene_id,
            output_kind="derived_index",
            object_path=object_path,
            index_name="ndvi",
            formula_version="ndvi-s2-v1",
            processing_profile_version="sentinel2-l2a-earthsearch-v1",
            processing_resolution=10.0,
            scale_factor=10000,
            nodata_value=_NODATA,
        )
    )

    response = service.field_dates(
        FieldDatesRequest(
            geometry=_FIELD_GEOMETRY,
            sourceId="sentinel-2-l2a",
            index="NDVI",
            dates=[acquisition_date],
        )
    )

    assert response.dates[0].available is True


def test_field_dates_uses_field_quality_for_cloudy_resourcesat_composite() -> None:
    service, _queries, _layers = _build_service()
    acquisition_date = date(2026, 1, 11)
    scene_id = "liss4-composite"
    service._scene_repository.upsert(
        ProviderSceneRecord(
            id=scene_id,
            provider_adapter="bhoonidhi",
            source_id="resourcesat-2a-liss4-mx70-l2",
            provider_product_id="resourcesat-2a-liss4-mx70-l2:composite:aoi:2026-01-11",
            acquisition_at=datetime.combine(
                acquisition_date,
                datetime.min.time(),
                tzinfo=UTC,
            ),
            cloud_percent=65.0,
            provider_metadata={"composite": True},
            aoi_id="bangalore_60km_geodesic_aoi",
        )
    )
    object_path = f"indices/{scene_id}/ndvi.cog.tif"
    service._object_store.put_bytes(object_path, _cog(valid=True))
    mask_path = f"masks/{scene_id}/mask.tif"
    service._object_store.put_bytes(mask_path, _mask_cog(1))
    service._raster_repository.upsert_derived_index(
        RasterOutputRecord(
            id=None,
            scene_id=scene_id,
            output_kind="derived_index",
            object_path=object_path,
            index_name="ndvi",
            formula_version="ndvi-resourcesat-v1",
            processing_profile_version="resourcesat-liss4-v1",
            processing_resolution=11.6,
            scale_factor=10000,
            nodata_value=_NODATA,
            metadata={"mask_object_path": mask_path},
        )
    )

    response = service.field_dates(
        FieldDatesRequest(
            geometry=_FIELD_GEOMETRY,
            sourceId="resourcesat-2a-liss4-mx70-l2",
            index="NDVI",
            dates=[acquisition_date],
        )
    )

    assert response.dates[0].available is True
    assert response.dates[0].usablePixelPercentage == 100.0
    assert response.dates[0].cloudPercentage == 0.0
    assert response.dates[0].fieldCoveragePercentage == 100.0
    assert response.dates[0].obscuredPercentage == 0.0


def test_field_dates_raises_when_candidate_raster_cannot_be_read() -> None:
    service, _queries, _layers = _build_service()
    service._object_store._objects.pop("indices/scene-2026-06-01/ndvi.cog.tif")

    with pytest.raises(AnalyticsRasterUnavailable):
        service.field_dates(_request())


def test_field_dates_endpoint_requires_auth_and_returns_batch() -> None:
    api_key = "test-akasha-key"
    settings = Settings(
        environment=Environment.TEST,
        runtime_backend=RuntimeBackend.MEMORY,
        api_key_hashes=f"test:{hash_api_key(api_key)}",
    )
    service, _queries, _layers = _build_service()
    app = create_app(settings)
    app.state.analytics_service = service
    client = TestClient(app)

    unauthorized = client.post(
        "/api/v1/analytics/field-dates",
        json=_request().model_dump(mode="json"),
    )
    response = client.post(
        "/api/v1/analytics/field-dates",
        headers={"X-API-Key": api_key},
        json=_request().model_dump(mode="json"),
    )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.json()["data"]["sourceId"] == "sentinel-2-l2a"
    assert len(response.json()["data"]["dates"]) == 3


def test_field_dates_endpoint_returns_503_for_raster_outage() -> None:
    api_key = "test-akasha-key"
    settings = Settings(
        environment=Environment.TEST,
        runtime_backend=RuntimeBackend.MEMORY,
        api_key_hashes=f"test:{hash_api_key(api_key)}",
    )
    service, _queries, _layers = _build_service()
    service._object_store._objects.pop("indices/scene-2026-06-01/ndvi.cog.tif")
    app = create_app(settings)
    app.state.analytics_service = service

    response = TestClient(app).post(
        "/api/v1/analytics/field-dates",
        headers={"X-API-Key": api_key},
        json=_request().model_dump(mode="json"),
    )

    assert response.status_code == 503


def test_field_dates_rejects_duplicate_dates() -> None:
    payload = _request().model_dump(mode="json")
    payload["dates"] = ["2026-06-01", "2026-06-01"]

    response = TestClient(create_app(Settings(
        environment=Environment.TEST,
        runtime_backend=RuntimeBackend.MEMORY,
    ))).post("/api/v1/analytics/field-dates", json=payload)

    assert response.status_code in {401, 503}
    # Model-level validation is covered directly because API auth intentionally runs first.
    try:
        FieldDatesRequest.model_validate(payload)
    except ValueError as exc:
        assert "dates must be unique" in str(exc)
    else:
        raise AssertionError("duplicate dates must be rejected")
