from __future__ import annotations

from datetime import date

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
from akasha.config import Environment, RuntimeBackend, Settings
from akasha.processing.overlay import render_clipped_index_overlay
from akasha.services.analytics import AnalyticsService
from akasha.storage.object_store import InMemoryObjectStore

_FIELD_GEOMETRY = {
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
_COG_BOUNDS = (77.595, 12.945, 77.615, 12.965)


def _synthetic_ndvi_cog(
    *,
    bounds: tuple[float, float, float, float] = _COG_BOUNDS,
    width: int = 48,
    height: int = 48,
    dn: int = 6500,
    nodata: int = 0,
) -> bytes:
    """Build a tiny EPSG:4326 single-band NDVI COG (DN = ndvi * 10000)."""

    minx, miny, maxx, maxy = bounds
    transform = from_bounds(minx, miny, maxx, maxy, width, height)
    data = np.full((height, width), dn, dtype="int16")
    data[:6, :6] = nodata  # nodata corner exercises transparency
    with MemoryFile() as mem:
        with mem.open(
            driver="GTiff",
            height=height,
            width=width,
            count=1,
            dtype="int16",
            crs="EPSG:4326",
            transform=transform,
            nodata=nodata,
        ) as dataset:
            dataset.write(data, 1)
        return mem.read()


def test_render_clipped_index_overlay_produces_png_and_corners() -> None:
    png, corners = render_clipped_index_overlay(
        _synthetic_ndvi_cog(),
        geometry=_FIELD_GEOMETRY,
        index_name="NDVI",
        scale_factor=10000,
        nodata=0,
    )

    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert corners is not None
    assert len(corners) == 4
    assert all(len(pair) == 2 for pair in corners)
    # Corners must bracket the field polygon (EPSG:4326 lng/lat).
    lngs = [pair[0] for pair in corners]
    lats = [pair[1] for pair in corners]
    assert min(lngs) <= 77.600 and max(lngs) >= 77.610
    assert min(lats) <= 12.950 and max(lats) >= 12.960


def test_render_clipped_index_overlay_transparent_when_no_overlap() -> None:
    far_geometry = {
        "type": "Polygon",
        "coordinates": [
            [
                [10.0, 10.0],
                [10.001, 10.0],
                [10.001, 10.001],
                [10.0, 10.001],
                [10.0, 10.0],
            ]
        ],
    }

    png, corners = render_clipped_index_overlay(
        _synthetic_ndvi_cog(),
        geometry=far_geometry,
        index_name="NDVI",
        scale_factor=10000,
        nodata=0,
    )

    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert corners is None


def _settings() -> Settings:
    return Settings(
        environment=Environment.TEST,
        runtime_backend=RuntimeBackend.MEMORY,
        signing_secret="test-signing-secret",
        public_base_url="http://testserver",
    )


def _build_overlay_client() -> tuple[TestClient, AnalyticsService, str]:
    settings = _settings()
    object_store = InMemoryObjectStore()
    raster_repository = InMemoryRasterRepository()
    field_query_repository = InMemoryFieldQueryRepository()

    object_path = "derived/ndvi/scene-overlay.tif"
    object_store.put_bytes(object_path, _synthetic_ndvi_cog())
    raster = raster_repository.upsert_derived_index(
        RasterOutputRecord(
            id=None,
            scene_id="scene-overlay",
            output_kind="derived_index",
            index_name="ndvi",
            object_path=object_path,
            formula_version="ndvi-v1",
            processing_profile_version="sentinel2-l2a-earthsearch-v1",
            processing_resolution=10.0,
            scale_factor=10000,
            nodata_value=0,
        )
    )
    record = field_query_repository.save(
        FieldQueryRecord(
            query_id="qid-overlay-1",
            field_geometry=_FIELD_GEOMETRY,
            index_name="ndvi",
            requested_date=date(2026, 1, 15),
            selection_reason="quality_first",
            raster_output_id=raster.id,
        )
    )

    analytics = AnalyticsService(
        field_query_repository=field_query_repository,
        scene_repository=None,
        raster_repository=raster_repository,
        tile_layer_repository=None,
        object_store=object_store,
        profile_repository=None,
        settings=settings,
    )
    app = create_app(settings)
    app.state.analytics_service = analytics
    return TestClient(app), analytics, record.query_id


def _signed_overlay_query(analytics: AnalyticsService, query_id: str) -> str:
    reference = analytics._signing.sign(
        method="GET",
        operation="overlay",
        resource_id=query_id,
        path_template=f"/api/v1/analytics/field-index/{query_id}/overlay.png",
        geometry_or_query_hash=analytics._signing.query_hash(f"{query_id}:overlay"),
    )
    return reference.query_string()


def test_overlay_route_returns_clipped_png() -> None:
    client, analytics, query_id = _build_overlay_client()

    response = client.get(
        f"/api/v1/analytics/field-index/{query_id}/overlay.png"
        f"?{_signed_overlay_query(analytics, query_id)}"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert "X-Akasha-Overlay-Corners" in response.headers
    assert "derived/" not in str(response.content)


def test_overlay_route_rejects_invalid_signature() -> None:
    client, _analytics, query_id = _build_overlay_client()

    response = client.get(
        f"/api/v1/analytics/field-index/{query_id}/overlay.png"
        "?op=overlay&exp=9999999999&kid=default&sig=bad"
    )

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "invalid signature"


def test_overlay_route_rejects_wrong_operation() -> None:
    client, analytics, query_id = _build_overlay_client()
    query = _signed_overlay_query(analytics, query_id).replace("op=overlay", "op=tile")

    response = client.get(f"/api/v1/analytics/field-index/{query_id}/overlay.png?{query}")

    assert response.status_code == 403


def test_overlay_route_unknown_query_returns_404() -> None:
    client, analytics, _query_id = _build_overlay_client()
    unknown = "qid-does-not-exist"

    response = client.get(
        f"/api/v1/analytics/field-index/{unknown}/overlay.png"
        f"?{_signed_overlay_query(analytics, unknown)}"
    )

    assert response.status_code == 404
