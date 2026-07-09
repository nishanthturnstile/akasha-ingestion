from __future__ import annotations

from datetime import UTC, date, datetime
from urllib.parse import urlparse

import numpy as np
import pytest
from fastapi.testclient import TestClient
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds

from akasha.api.app import create_app
from akasha.catalog.field_query_repository import InMemoryFieldQueryRepository
from akasha.catalog.profile_repository import InMemoryProfileRepository, build_memory_profiles
from akasha.catalog.raster_repository import InMemoryRasterRepository, RasterOutputRecord
from akasha.catalog.scene_repository import InMemorySceneRepository, ProviderSceneRecord
from akasha.catalog.seed_db import THRESHOLD_PROFILES, VISUALIZATION_PROFILES
from akasha.catalog.tile_layer_repository import InMemoryTileLayerRepository
from akasha.config import Environment, RuntimeBackend, Settings
from akasha.processing.resourcesat import (
    AWIFS_PROFILE,
    LISS3_PROFILE,
    LISS4_PROFILE,
    RESOURCESAT_AWIFS_BOA_SOURCE_ID,
    RESOURCESAT_LISS3_BOA_SOURCE_ID,
    RESOURCESAT_LISS4_MX70_L2_SOURCE_ID,
    RESOURCESAT_MASK_METHOD,
)
from akasha.security import hash_api_key
from akasha.services.analytics import AnalyticsService
from akasha.services.resourcesat_outputs import RESOURCESAT_COMPOSITE_OUTPUT_KIND
from akasha.storage.object_store import InMemoryObjectStore

API_KEY = "test-akasha-key"
AOI_ID = "bangalore_60km_geodesic_aoi"
FIELD_GEOMETRY = {
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
COG_BOUNDS = (77.600, 12.950, 77.640, 12.990)
VALID_POINT = {"lng": 77.625, "lat": 12.965}
NODATA = -32768


def test_resourcesat_liss3_field_index_is_available_and_signed_urls_work() -> None:
    client, _analytics = _client_with_scene(
        source_id=RESOURCESAT_LISS3_BOA_SOURCE_ID,
        product_id="RS_LISS3_001",
        value=6500,
        coverage_percentage=98.0,
        profile=LISS3_PROFILE,
    )

    response = client.post(
        "/api/v1/analytics/field-index",
        headers=_headers(),
        json=_payload(source_id=RESOURCESAT_LISS3_BOA_SOURCE_ID, index="NDVI"),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "AVAILABLE"
    assert data["source"] == RESOURCESAT_LISS3_BOA_SOURCE_ID
    assert data["providerRoute"] == "bhoonidhi:ResourceSat-2A_LISS3_BOA"
    assert data["resolution"]["nativeMeters"] == 23.5
    assert data["selection"]["windowDays"] == 30
    assert data["statistics"]["mean"] == pytest.approx(0.65)
    assert data["quality"] == {
        "status": "GOOD",
        "reason": "Field usable pixels satisfy threshold",
        "warnings": [],
    }
    serialized = str(response.json())
    assert "s3://" not in serialized
    assert "raw/" not in serialized
    assert "bhoonidhi-api" not in serialized

    overlay_url = urlparse(data["overlayUrl"])
    overlay_response = client.get(f"{overlay_url.path}?{overlay_url.query}")
    assert overlay_response.status_code == 200
    assert overlay_response.headers["content-type"] == "image/png"
    assert overlay_response.content[:8] == b"\x89PNG\r\n\x1a\n"

    point_url = urlparse(data["pointUrl"])
    point_response = client.get(
        f"{point_url.path}?{point_url.query}&lng={VALID_POINT['lng']}&lat={VALID_POINT['lat']}"
    )
    assert point_response.status_code == 200
    point_data = point_response.json()["data"]
    assert point_data["source"] == RESOURCESAT_LISS3_BOA_SOURCE_ID
    assert point_data["value"] == 0.65


def test_resourcesat_field_index_prefers_higher_coverage_candidate() -> None:
    harness = _analytics_harness()
    _seed_scene_and_raster(
        harness,
        source_id=RESOURCESAT_LISS3_BOA_SOURCE_ID,
        product_id="RS_LISS3_LOW_COVERAGE",
        value=2000,
        coverage_percentage=80.0,
        profile=LISS3_PROFILE,
    )
    _seed_scene_and_raster(
        harness,
        source_id=RESOURCESAT_LISS3_BOA_SOURCE_ID,
        product_id="RS_LISS3_HIGH_COVERAGE",
        value=6500,
        coverage_percentage=98.0,
        profile=LISS3_PROFILE,
    )
    client = _client_from_harness(harness)

    response = client.post(
        "/api/v1/analytics/field-index",
        headers=_headers(),
        json=_payload(source_id=RESOURCESAT_LISS3_BOA_SOURCE_ID, index="NDVI"),
    )

    assert response.status_code == 200
    assert response.json()["data"]["statistics"]["mean"] == pytest.approx(0.65)


def test_resourcesat_liss4_partial_coverage_returns_warning() -> None:
    client, _analytics = _client_with_scene(
        source_id=RESOURCESAT_LISS4_MX70_L2_SOURCE_ID,
        product_id="RS_LISS4_001",
        value=6000,
        coverage_percentage=25.0,
        profile=LISS4_PROFILE,
    )

    response = client.post(
        "/api/v1/analytics/field-index",
        headers=_headers(),
        json=_payload(source_id=RESOURCESAT_LISS4_MX70_L2_SOURCE_ID, index="NDVI"),
    )

    assert response.status_code == 200
    quality = response.json()["data"]["quality"]
    assert quality["status"] == "WARN"
    assert any("LISS-4" in warning for warning in quality["warnings"])


def test_resourcesat_awifs_coarse_resolution_returns_warning() -> None:
    client, _analytics = _client_with_scene(
        source_id=RESOURCESAT_AWIFS_BOA_SOURCE_ID,
        product_id="RS_AWIFS_001",
        value=6000,
        coverage_percentage=80.0,
        profile=AWIFS_PROFILE,
    )

    response = client.post(
        "/api/v1/analytics/field-index",
        headers=_headers(),
        json=_payload(source_id=RESOURCESAT_AWIFS_BOA_SOURCE_ID, index="NDVI"),
    )

    assert response.status_code == 200
    quality = response.json()["data"]["quality"]
    assert quality["status"] == "WARN"
    assert any("AWiFS" in warning for warning in quality["warnings"])


def test_resourcesat_field_index_rejects_source_incompatible_indices() -> None:
    client, _analytics = _client_with_scene(
        source_id=RESOURCESAT_LISS4_MX70_L2_SOURCE_ID,
        product_id="RS_LISS4_001",
        value=6000,
        coverage_percentage=25.0,
        profile=LISS4_PROFILE,
    )

    response = client.post(
        "/api/v1/analytics/field-index",
        headers=_headers(),
        json=_payload(source_id=RESOURCESAT_LISS4_MX70_L2_SOURCE_ID, index="NDMI"),
    )

    assert response.status_code == 422
    assert "unsupported index" in response.json()["error"]["message"]


def test_resourcesat_field_index_unavailable_uses_requested_source_window() -> None:
    client = _client_from_harness(_analytics_harness())

    response = client.post(
        "/api/v1/analytics/field-index",
        headers=_headers(),
        json=_payload(source_id=RESOURCESAT_LISS3_BOA_SOURCE_ID, index="NDVI"),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "UNAVAILABLE"
    assert data["searchedSources"] == [RESOURCESAT_LISS3_BOA_SOURCE_ID]
    assert "+/- 30 days" in data["reason"]


def _client_with_scene(
    *,
    source_id: str,
    product_id: str,
    value: int,
    coverage_percentage: float,
    profile,
) -> tuple[TestClient, AnalyticsService]:
    harness = _analytics_harness()
    _seed_scene_and_raster(
        harness,
        source_id=source_id,
        product_id=product_id,
        value=value,
        coverage_percentage=coverage_percentage,
        profile=profile,
    )
    client = _client_from_harness(harness)
    return client, harness.analytics


class _AnalyticsHarness:
    def __init__(self) -> None:
        self.settings = Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.MEMORY,
            api_key_hashes=f"test:{hash_api_key(API_KEY)}",
            public_base_url="http://testserver",
            signing_secret="test-signing-secret",
        )
        self.object_store = InMemoryObjectStore()
        self.scene_repository = InMemorySceneRepository()
        self.raster_repository = InMemoryRasterRepository()
        self.tile_layer_repository = InMemoryTileLayerRepository(
            raster_repository=self.raster_repository,
            scene_repository=self.scene_repository,
        )
        self.field_query_repository = InMemoryFieldQueryRepository()
        visualization_profiles, threshold_profiles = build_memory_profiles(
            VISUALIZATION_PROFILES,
            THRESHOLD_PROFILES,
        )
        self.profile_repository = InMemoryProfileRepository(
            visualization_profiles=visualization_profiles,
            threshold_profiles=threshold_profiles,
        )
        self.analytics = AnalyticsService(
            field_query_repository=self.field_query_repository,
            scene_repository=self.scene_repository,
            raster_repository=self.raster_repository,
            tile_layer_repository=self.tile_layer_repository,
            object_store=self.object_store,
            profile_repository=self.profile_repository,
            settings=self.settings,
        )


def _analytics_harness() -> _AnalyticsHarness:
    return _AnalyticsHarness()


def _client_from_harness(harness: _AnalyticsHarness) -> TestClient:
    app = create_app(harness.settings, object_store=harness.object_store)
    app.state.analytics_service = harness.analytics
    app.state.tile_layer_repository = harness.tile_layer_repository
    return TestClient(app)


def _seed_scene_and_raster(
    harness: _AnalyticsHarness,
    *,
    source_id: str,
    product_id: str,
    value: int,
    coverage_percentage: float,
    profile,
) -> None:
    scene = harness.scene_repository.upsert(
        ProviderSceneRecord(
            id=None,
            provider_adapter="bhoonidhi",
            source_id=source_id,
            provider_product_id=product_id,
            acquisition_at=datetime.combine(date(2026, 3, 19), datetime.min.time(), tzinfo=UTC),
            status="composited",
            cloud_percent=5.0,
            aoi_id=AOI_ID,
            coverage_percentage=coverage_percentage,
            provider_metadata={
                "output_kind": RESOURCESAT_COMPOSITE_OUTPUT_KIND,
                "provider_collection": profile.collection_id,
            },
            native_resolution=profile.native_resolution_m,
        )
    )
    object_path = f"indices/bhoonidhi/{source_id}/{product_id}/ndvi.cog.tif"
    harness.object_store.put_bytes(object_path, _synthetic_index_cog(value))
    harness.raster_repository.upsert_derived_index(
        RasterOutputRecord(
            id=None,
            scene_id=scene.id or "",
            output_kind="derived_index",
            index_name="ndvi",
            object_path=object_path,
            formula_version="ndvi-resourcesat-v1",
            processing_profile_version=profile.processing_profile_version,
            processing_resolution=profile.native_resolution_m,
            scale_factor=10000,
            nodata_value=NODATA,
            native_resolution=profile.native_resolution_m,
            display_resolution=profile.native_resolution_m,
            cloud_mask_version=RESOURCESAT_MASK_METHOD,
        )
    )


def _synthetic_index_cog(value: int) -> bytes:
    transform = from_bounds(*COG_BOUNDS, width=4, height=4)
    data = np.full((4, 4), value, dtype="int16")
    data[0, 0] = NODATA
    with MemoryFile() as mem:
        with mem.open(
            driver="GTiff",
            height=4,
            width=4,
            count=1,
            dtype="int16",
            crs="EPSG:4326",
            transform=transform,
            nodata=NODATA,
        ) as dataset:
            dataset.write(data, 1)
        return mem.read()


def _payload(*, source_id: str, index: str) -> dict[str, object]:
    return {
        "geometry": FIELD_GEOMETRY,
        "sourceId": source_id,
        "crs": "EPSG:4326",
        "index": index,
        "date": "2026-03-19",
        "fallbackPolicy": "nearest_valid_scene",
        "maxCloudPercentage": 20,
    }


def _headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}
