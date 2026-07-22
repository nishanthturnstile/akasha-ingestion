from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from akasha.api.app import create_app
from akasha.catalog.profile_repository import InMemoryProfileRepository, build_memory_profiles
from akasha.catalog.raster_repository import InMemoryRasterRepository, RasterOutputRecord
from akasha.catalog.scene_repository import InMemorySceneRepository, ProviderSceneRecord
from akasha.catalog.seed_db import THRESHOLD_PROFILES, VISUALIZATION_PROFILES
from akasha.catalog.tile_layer_repository import InMemoryTileLayerRepository, TileLayerRecord
from akasha.config import Environment, RuntimeBackend, Settings
from akasha.services.analytics import AnalyticsService
from akasha.services.titiler_tiles import TiTilerTileService
from akasha.storage.object_store import InMemoryObjectStore

_TITILER_INTERNAL_URL = "http://titiler-internal.private:8000"
_MOCK_PNG = b"\x89PNG\r\n\x1a\n" + b"mock-tile-bytes" * 16


def _settings() -> Settings:
    return Settings(
        environment=Environment.TEST,
        runtime_backend=RuntimeBackend.MEMORY,
        signing_secret="test-signing-secret",
        public_base_url="http://testserver",
        titiler_internal_url=_TITILER_INTERNAL_URL,
    )


def _seed_layer(
    raster_repository: InMemoryRasterRepository,
    scene_repository: InMemorySceneRepository,
    tile_layer_repository: InMemoryTileLayerRepository,
) -> str:
    scene = scene_repository.upsert(
        ProviderSceneRecord(
            id="scene-1",
            provider_adapter="earthsearch",
            source_id="sentinel-2-l2a",
            provider_product_id="S2A_TEST_001",
            acquisition_at=datetime(2026, 1, 15, tzinfo=UTC),
            pgstac_item_id="s2-l2a-T43PHQ-20260115-item",
        )
    )
    raster = raster_repository.upsert_derived_index(
        RasterOutputRecord(
            id=None,
            scene_id=scene.id or "",
            output_kind="derived_index",
            index_name="ndvi",
            object_path="derived/ndvi/scene-1.tif",
            formula_version="ndvi-v1",
            processing_profile_version="sentinel2-l2a-earthsearch-v1",
            processing_resolution=10.0,
            min_value=-0.1,
            max_value=0.85,
            metadata={
                "pgstac_collection": "akasha-sentinel-2-l2a-derived-v1",
                "pgstac_asset_key": "ndvi",
            },
        )
    )
    layer = tile_layer_repository.upsert_for_raster(
        TileLayerRecord(
            layer_id=None,
            raster_output_id=raster.id or "",
            visibility="private",
            metadata={"index_name": "ndvi", "scene_id": scene.id},
        )
    )
    return layer.layer_id or ""


def _build_client(titiler_service: TiTilerTileService) -> tuple[TestClient, AnalyticsService, str]:
    settings = _settings()
    raster_repository = InMemoryRasterRepository()
    scene_repository = InMemorySceneRepository()
    tile_layer_repository = InMemoryTileLayerRepository(
        raster_repository=raster_repository,
        scene_repository=scene_repository,
    )
    layer_id = _seed_layer(raster_repository, scene_repository, tile_layer_repository)

    visualization_profiles, threshold_profiles = build_memory_profiles(
        VISUALIZATION_PROFILES,
        THRESHOLD_PROFILES,
    )
    analytics = AnalyticsService(
        field_query_repository=None,
        scene_repository=scene_repository,
        raster_repository=raster_repository,
        tile_layer_repository=tile_layer_repository,
        object_store=InMemoryObjectStore(),
        profile_repository=InMemoryProfileRepository(
            visualization_profiles=visualization_profiles,
            threshold_profiles=threshold_profiles,
        ),
        settings=settings,
    )

    app = create_app(settings)
    app.state.analytics_service = analytics
    app.state.tile_layer_repository = tile_layer_repository
    app.state.titiler_tile_service = titiler_service
    return TestClient(app), analytics, layer_id


def _signed_query(analytics: AnalyticsService, layer_id: str) -> str:
    reference = analytics._signing.sign(
        method="GET",
        operation="tile",
        resource_id=layer_id,
        path_template=f"/tiles/{layer_id}/{{z}}/{{x}}/{{y}}.png",
        geometry_or_query_hash=analytics._signing.query_hash(f"{layer_id}:tile"),
    )
    return reference.query_string()


def _forbidding_titiler() -> TiTilerTileService:
    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("TiTiler must not be called before signature verification")

    return TiTilerTileService(
        settings=_settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_tile_rejects_invalid_signature() -> None:
    client, _analytics, layer_id = _build_client(_forbidding_titiler())

    response = client.get(f"/tiles/{layer_id}/1/1/1.png?op=tile&exp=9999999999&kid=default&sig=bad")

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "invalid signature"


def test_tile_unknown_layer_returns_404() -> None:
    client, analytics, _layer_id = _build_client(_forbidding_titiler())
    unknown = "lyr_does_not_exist"

    response = client.get(f"/tiles/{unknown}/1/1/1.png?{_signed_query(analytics, unknown)}")

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "layer not found"


def test_tile_sanitizes_titiler_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal boom at http://titiler-internal.private:8000")

    titiler = TiTilerTileService(
        settings=_settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client, analytics, layer_id = _build_client(titiler)

    response = client.get(f"/tiles/{layer_id}/1/1/1.png?{_signed_query(analytics, layer_id)}")

    assert response.status_code == 502
    body = response.text
    assert "titiler-internal" not in body
    assert _TITILER_INTERNAL_URL not in body


def test_tile_titiler_404_maps_to_404() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    titiler = TiTilerTileService(
        settings=_settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client, analytics, layer_id = _build_client(titiler)

    response = client.get(f"/tiles/{layer_id}/1/1/1.png?{_signed_query(analytics, layer_id)}")

    assert response.status_code == 404


def test_tile_proxies_png_from_titiler() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, content=_MOCK_PNG, headers={"content-type": "image/png"})

    titiler = TiTilerTileService(
        settings=_settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client, analytics, layer_id = _build_client(titiler)

    response = client.get(f"/tiles/{layer_id}/3/4/5.png?{_signed_query(analytics, layer_id)}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == _MOCK_PNG
    assert captured["path"] == (
        "/collections/akasha-sentinel-2-l2a-derived-v1"
        "/items/s2-l2a-T43PHQ-20260115-item/tiles/WebMercatorQuad/3/4/5.png"
    )
    params = captured["params"]
    assert params["assets"] == "ndvi"
    assert params["rescale"] == "-0.2,0.9"
    assert params["colormap_name"] == "rdylgn"


def test_titiler_repeats_multi_asset_query_parameters() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["assets"] = request.url.params.get_list("assets")
        return httpx.Response(200, content=_MOCK_PNG, headers={"content-type": "image/png"})

    titiler = TiTilerTileService(
        settings=_settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    content, content_type = titiler.fetch_tile(
        collection_id="akasha-sentinel-2-l2a-derived-v1",
        item_id="s2-rgb-item",
        z=10,
        x=732,
        y=474,
        assets="red,green,blue",
        rescale="0,3000",
    )

    assert content == _MOCK_PNG
    assert content_type == "image/png"
    assert captured["assets"] == ["red", "green", "blue"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
