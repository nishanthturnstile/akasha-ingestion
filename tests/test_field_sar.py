from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlsplit

import numpy as np
from fastapi.testclient import TestClient
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds

from akasha.api.app import create_app
from akasha.catalog.asset_repository import SceneAssetRecord
from akasha.catalog.scene_repository import ProviderSceneRecord
from akasha.config import Environment, RuntimeBackend, Settings
from akasha.processing.eos04 import EOS04_SOURCE_ID
from akasha.processing.overlay import render_clipped_sar_overlay
from akasha.processing.raster_stats import sar_field_stats
from akasha.security import hash_api_key

API_KEY = "test-akasha-key"
FIELD = {
    "type": "Polygon",
    "coordinates": [
        [
            [77.600, 12.950],
            [77.610, 12.950],
            [77.610, 12.960],
            [77.600, 12.960],
            [77.600, 12.950],
        ]
    ],
}


def _sar_cog(*, nodata_fraction: float = 0.0) -> bytes:
    width = height = 48
    transform = from_bounds(77.595, 12.945, 77.615, 12.965, width, height)
    data = np.stack(
        [
            np.full((height, width), -10.0, dtype="float32"),
            np.full((height, width), -20.0, dtype="float32"),
        ]
    )
    if nodata_fraction:
        rows = int(height * nodata_fraction)
        data[:, :rows, :] = -9999.0
    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff",
            height=height,
            width=width,
            count=2,
            dtype="float32",
            crs="EPSG:4326",
            transform=transform,
            nodata=-9999.0,
        ) as dataset:
            dataset.write(data)
            dataset.set_band_description(1, "HH")
            dataset.set_band_description(2, "HV")
        return memory.read()


def _client(*, cog: bytes | None = None) -> TestClient:
    settings = Settings(
        environment=Environment.TEST,
        runtime_backend=RuntimeBackend.MEMORY,
        api_key_hashes=f"test:{hash_api_key(API_KEY)}",
        signing_secret="field-sar-signing-secret",
        public_base_url="http://testserver",
    )
    app = create_app(settings)
    if cog is None:
        return TestClient(app)
    scene = app.state.scene_repository.upsert(
        ProviderSceneRecord(
            id=None,
            provider_adapter="bhoonidhi",
            source_id=EOS04_SOURCE_ID,
            provider_product_id="EOS04-FIELD-SAR-20260717",
            acquisition_at=datetime(2026, 7, 17, 0, 40, 49, tzinfo=UTC),
            status="accepted",
            pgstac_item_id="eos04-field-sar-20260717",
            aoi_id="bangalore",
            native_crs="EPSG:4326",
            native_resolution=18.0,
        )
    )
    assert scene.id is not None
    object_path = "eos04/2026-07-17/backscatter.tif"
    app.state.object_store.put_bytes(object_path, cog)
    app.state.asset_repository.upsert(
        SceneAssetRecord(
            id=None,
            scene_id=scene.id,
            asset_kind="sar_backscatter",
            asset_key="backscatter",
            object_path=object_path,
            nodata_value=-9999.0,
            metadata={
                "polarizations": ["HH", "HV"],
                "processing_profile_version": "eos04-sar-mrs-l2b-gamma0-v2",
            },
        )
    )
    return TestClient(app)


def _payload() -> dict:
    return {
        "geometry": FIELD,
        "fieldId": "field-1",
        "sourceId": EOS04_SOURCE_ID,
        "targetDate": "2026-07-18",
        "windowDays": 21,
        "minimumCoveragePercent": 95,
    }


def test_sar_field_stats_returns_robust_band_evidence() -> None:
    result = sar_field_stats(
        _sar_cog(),
        geometry=FIELD,
        band_names=["HH", "HV"],
        encoded_nodata=-9999.0,
    )

    assert result["coveragePercent"] == 100.0
    assert result["validPixelCount"] > 0
    assert result["bands"][0]["median"] == -10.0
    assert result["bands"][1]["median"] == -20.0
    assert result["features"]["HH_MINUS_HV_DB"] == 10.0


def test_sar_overlay_is_field_clipped_png() -> None:
    png, corners = render_clipped_sar_overlay(
        _sar_cog(),
        geometry=FIELD,
        band_index=1,
        nodata=-9999.0,
    )

    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert corners is not None
    assert len(corners) == 4


def test_field_sar_api_returns_evidence_and_signed_overlay() -> None:
    client = _client(cog=_sar_cog())

    response = client.post(
        "/api/v1/analytics/field-sar",
        headers={"X-API-Key": API_KEY},
        json=_payload(),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "AVAILABLE"
    assert data["acquisitionDate"] == "2026-07-17"
    assert data["daysFromTarget"] == -1
    assert data["coveragePercent"] == 100.0
    assert data["polarizations"] == ["HH", "HV"]
    assert data["displayedPolarization"] == "HH"
    assert data["features"]["HH_MINUS_HV_DB"] == 10.0
    assert data["quality"]["confidence"] == "high"
    assert "s3://" not in str(data)
    overlay = urlsplit(data["overlayUrl"])
    overlay_response = client.get(f"{overlay.path}?{overlay.query}")
    assert overlay_response.status_code == 200
    assert overlay_response.headers["content-type"] == "image/png"
    assert "X-Akasha-Overlay-Corners" in overlay_response.headers


def test_field_sar_returns_typed_unavailable_without_scene() -> None:
    response = _client().post(
        "/api/v1/analytics/field-sar",
        headers={"X-API-Key": API_KEY},
        json=_payload(),
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "status": "UNAVAILABLE",
        "fieldId": "field-1",
        "sourceId": EOS04_SOURCE_ID,
        "requestedDate": "2026-07-18",
        "reasonCode": "no_scene",
        "reason": "No accepted EOS-04 scene is available within the support window.",
    }


def test_field_sar_requires_api_key() -> None:
    response = _client(cog=_sar_cog()).post(
        "/api/v1/analytics/field-sar",
        json=_payload(),
    )

    assert response.status_code == 401
