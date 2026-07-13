from __future__ import annotations

from datetime import UTC, date, datetime
from urllib.parse import parse_qs, urlparse

import numpy as np
from fastapi.testclient import TestClient
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds

from akasha.api.app import create_app
from akasha.catalog.field_query_repository import (
    FieldQueryRecord,
    InMemoryFieldQueryRepository,
)
from akasha.catalog.raster_repository import InMemoryRasterRepository, RasterOutputRecord
from akasha.catalog.scene_repository import InMemorySceneRepository, ProviderSceneRecord
from akasha.catalog.tile_layer_repository import InMemoryTileLayerRepository
from akasha.config import Environment, RuntimeBackend, Settings
from akasha.schemas import FieldIndexRequest
from akasha.security import hash_api_key
from akasha.services.analytics import AnalyticsService
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
_COG_BOUNDS = (77.600, 12.950, 77.640, 12.990)
_VALID_POINT = {"lng": 77.625, "lat": 12.965}
_MASKED_POINT = {"lng": 77.605, "lat": 12.985}
_NODATA = -32768


def _synthetic_ndvi_cog() -> bytes:
    transform = from_bounds(*_COG_BOUNDS, width=4, height=4)
    data = np.full((4, 4), 6500, dtype="int16")
    data[0, 0] = _NODATA
    with MemoryFile() as mem:
        with mem.open(
            driver="GTiff",
            height=4,
            width=4,
            count=1,
            dtype="int16",
            crs="EPSG:4326",
            transform=transform,
            nodata=_NODATA,
        ) as dataset:
            dataset.write(data, 1)
        return mem.read()


def _settings(*, api_key: str | None = None) -> Settings:
    return Settings(
        environment=Environment.TEST,
        runtime_backend=RuntimeBackend.MEMORY,
        api_key_hashes=f"test:{hash_api_key(api_key)}" if api_key else "",
        signing_secret="test-signing-secret",
        public_base_url="http://testserver",
    )


def _scene_repository() -> InMemorySceneRepository:
    scene_repository = InMemorySceneRepository()
    scene_repository.upsert(
        ProviderSceneRecord(
            id="scene-point-1",
            provider_adapter="earthsearch",
            source_id="sentinel-2-l2a",
            provider_product_id="S2_POINT_001",
            acquisition_at=datetime(2026, 1, 15, tzinfo=UTC),
            cloud_percent=5.0,
            provider_metadata={"provider_route": "earthsearch:sentinel-2-l2a"},
        )
    )
    return scene_repository


def _put_raster(
    *,
    object_store: InMemoryObjectStore,
    raster_repository: InMemoryRasterRepository,
) -> RasterOutputRecord:
    object_path = "indices/earthsearch/sentinel-2-l2a/S2_POINT_001/ndvi.cog.tif"
    object_store.put_bytes(object_path, _synthetic_ndvi_cog())
    return raster_repository.upsert_derived_index(
        RasterOutputRecord(
            id=None,
            scene_id="scene-point-1",
            output_kind="derived_index",
            index_name="ndvi",
            object_path=object_path,
            formula_version="ndvi-s2-v1",
            processing_profile_version="sentinel2-l2a-earthsearch-v1",
            processing_resolution=10.0,
            scale_factor=10000,
            nodata_value=_NODATA,
            native_resolution=10.0,
            display_resolution=10.0,
            cloud_mask_version="scl-v1",
        )
    )


def _analytics_with_dependencies(settings: Settings) -> AnalyticsService:
    object_store = InMemoryObjectStore()
    raster_repository = InMemoryRasterRepository()
    scene_repository = _scene_repository()
    tile_layer_repository = InMemoryTileLayerRepository(
        raster_repository=raster_repository,
        scene_repository=scene_repository,
    )
    field_query_repository = InMemoryFieldQueryRepository()
    _put_raster(object_store=object_store, raster_repository=raster_repository)
    return AnalyticsService(
        field_query_repository=field_query_repository,
        scene_repository=scene_repository,
        raster_repository=raster_repository,
        tile_layer_repository=tile_layer_repository,
        object_store=object_store,
        profile_repository=None,
        settings=settings,
    )


def _build_point_client() -> tuple[TestClient, AnalyticsService, str]:
    settings = _settings()
    object_store = InMemoryObjectStore()
    raster_repository = InMemoryRasterRepository()
    field_query_repository = InMemoryFieldQueryRepository()
    scene_repository = _scene_repository()
    raster = _put_raster(object_store=object_store, raster_repository=raster_repository)
    record = field_query_repository.save(
        FieldQueryRecord(
            query_id="qid-point-1",
            field_geometry=_FIELD_GEOMETRY,
            index_name="ndvi",
            requested_date=date(2026, 1, 15),
            selection_reason="quality_first",
            selected_scene_id="scene-point-1",
            raster_output_id=raster.id,
        )
    )
    analytics = AnalyticsService(
        field_query_repository=field_query_repository,
        scene_repository=scene_repository,
        raster_repository=raster_repository,
        tile_layer_repository=None,
        object_store=object_store,
        profile_repository=None,
        settings=settings,
    )
    app = create_app(settings)
    app.state.analytics_service = analytics
    return TestClient(app), analytics, record.query_id


def _signed_point_query(analytics: AnalyticsService, query_id: str) -> str:
    reference = analytics._signing.sign(
        method="GET",
        operation="point",
        resource_id=query_id,
        path_template=f"/api/v1/analytics/field-index/{query_id}/point",
        geometry_or_query_hash=analytics._signing.query_hash(f"{query_id}:point"),
    )
    return reference.query_string()


def test_field_index_available_response_includes_signed_point_url() -> None:
    api_key = "test-akasha-key"
    settings = _settings(api_key=api_key)
    analytics = _analytics_with_dependencies(settings)
    app = create_app(settings)
    app.state.analytics_service = analytics
    client = TestClient(app)

    response = client.post(
        "/api/v1/analytics/field-index",
        headers={"X-API-Key": api_key},
        json=FieldIndexRequest(
            geometry=_FIELD_GEOMETRY,
            index="NDVI",
            date=date(2026, 1, 15),
        ).model_dump(mode="json"),
    )

    assert response.status_code == 200
    point_url = response.json()["data"]["pointUrl"]
    parsed = urlparse(point_url)
    query = parse_qs(parsed.query)
    assert parsed.path.endswith("/point")
    assert query["op"] == ["point"]
    assert "lng" not in query
    assert "lat" not in query

    lookup = client.get(
        f"{parsed.path}?{parsed.query}&lng={_VALID_POINT['lng']}&lat={_VALID_POINT['lat']}"
    )
    assert lookup.status_code == 200
    assert lookup.json()["data"]["value"] == 0.65


def test_point_route_rejects_invalid_signature() -> None:
    client, _analytics, query_id = _build_point_client()

    response = client.get(
        f"/api/v1/analytics/field-index/{query_id}/point"
        f"?op=point&exp=9999999999&kid=default&sig=bad"
        f"&lng={_VALID_POINT['lng']}&lat={_VALID_POINT['lat']}"
    )

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "invalid signature"


def test_point_route_rejects_wrong_operation() -> None:
    client, analytics, query_id = _build_point_client()
    query = _signed_point_query(analytics, query_id).replace("op=point", "op=stats")

    response = client.get(
        f"/api/v1/analytics/field-index/{query_id}/point"
        f"?{query}&lng={_VALID_POINT['lng']}&lat={_VALID_POINT['lat']}"
    )

    assert response.status_code == 403
    assert response.json()["error"]["message"] == "wrong operation"


def test_point_route_unknown_query_returns_404() -> None:
    client, analytics, _query_id = _build_point_client()
    unknown = "qid-does-not-exist"

    response = client.get(
        f"/api/v1/analytics/field-index/{unknown}/point"
        f"?{_signed_point_query(analytics, unknown)}"
        f"&lng={_VALID_POINT['lng']}&lat={_VALID_POINT['lat']}"
    )

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "query not found"


def test_point_route_returns_masked_response_for_nodata_pixel() -> None:
    client, analytics, query_id = _build_point_client()

    response = client.get(
        f"/api/v1/analytics/field-index/{query_id}/point"
        f"?{_signed_point_query(analytics, query_id)}"
        f"&lng={_MASKED_POINT['lng']}&lat={_MASKED_POINT['lat']}"
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["queryId"] == query_id
    assert data["masked"] is True
    assert data["value"] is None
    assert data["maskClass"] is None


def test_point_route_returns_valid_pixel_value() -> None:
    client, analytics, query_id = _build_point_client()

    response = client.get(
        f"/api/v1/analytics/field-index/{query_id}/point"
        f"?{_signed_point_query(analytics, query_id)}"
        f"&lng={_VALID_POINT['lng']}&lat={_VALID_POINT['lat']}"
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data == {
        "queryId": query_id,
        "index": "NDVI",
        "lng": _VALID_POINT["lng"],
        "lat": _VALID_POINT["lat"],
        "value": 0.65,
        "masked": False,
        "maskClass": None,
        "source": "sentinel-2-l2a",
    }


def test_point_route_missing_raster_returns_typed_not_found() -> None:
    client, analytics, query_id = _build_point_client()
    analytics._object_store._objects.clear()

    response = client.get(
        f"/api/v1/analytics/field-index/{query_id}/point"
        f"?{_signed_point_query(analytics, query_id)}"
        f"&lng={_VALID_POINT['lng']}&lat={_VALID_POINT['lat']}"
    )

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Raster output was not found."


def test_field_index_skips_missing_nearest_raster_for_valid_fallback() -> None:
    settings = _settings()
    object_store = InMemoryObjectStore()
    raster_repository = InMemoryRasterRepository()
    scene_repository = InMemorySceneRepository()
    tile_layer_repository = InMemoryTileLayerRepository(
        raster_repository=raster_repository,
        scene_repository=scene_repository,
    )
    for scene_id, product_id, acquisition_at in (
        ("scene-missing", "S2_MISSING", datetime(2026, 1, 15, tzinfo=UTC)),
        ("scene-valid", "S2_VALID", datetime(2026, 1, 16, tzinfo=UTC)),
    ):
        scene_repository.upsert(
            ProviderSceneRecord(
                id=scene_id,
                provider_adapter="earthsearch",
                source_id="sentinel-2-l2a",
                provider_product_id=product_id,
                acquisition_at=acquisition_at,
                cloud_percent=5.0,
                provider_metadata={"provider_route": "earthsearch:sentinel-2-l2a"},
            )
        )
        object_path = f"indices/earthsearch/sentinel-2-l2a/{product_id}/ndvi.cog.tif"
        if scene_id == "scene-valid":
            object_store.put_bytes(object_path, _synthetic_ndvi_cog())
        raster_repository.upsert_derived_index(
            RasterOutputRecord(
                id=None,
                scene_id=scene_id,
                output_kind="derived_index",
                index_name="ndvi",
                object_path=object_path,
                formula_version="ndvi-s2-v1",
                processing_profile_version="sentinel2-l2a-earthsearch-v1",
                processing_resolution=10.0,
                scale_factor=10000,
                nodata_value=_NODATA,
                native_resolution=10.0,
                display_resolution=10.0,
                cloud_mask_version="scl-v1",
            )
        )
    analytics = AnalyticsService(
        field_query_repository=InMemoryFieldQueryRepository(),
        scene_repository=scene_repository,
        raster_repository=raster_repository,
        tile_layer_repository=tile_layer_repository,
        object_store=object_store,
        profile_repository=None,
        settings=settings,
    )

    result = analytics.field_index(
        FieldIndexRequest(
            geometry=_FIELD_GEOMETRY,
            index="NDVI",
            date=date(2026, 1, 15),
        )
    )

    assert result.status == "AVAILABLE"
    assert result.selectedSceneDate == date(2026, 1, 16)
