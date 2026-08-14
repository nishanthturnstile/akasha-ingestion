from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from urllib.parse import urlparse

import httpx
import numpy as np
from fastapi.testclient import TestClient
from rasterio.transform import from_origin

from akasha.api.app import create_app
from akasha.catalog.aoi_repository import AoiRecord
from akasha.catalog.asset_repository import InMemorySceneAssetRepository, SceneAssetRecord
from akasha.catalog.backfill_repository import InMemoryBackfillRepository
from akasha.catalog.field_query_repository import InMemoryFieldQueryRepository
from akasha.catalog.profile_repository import InMemoryProfileRepository, build_memory_profiles
from akasha.catalog.raster_repository import InMemoryRasterRepository
from akasha.catalog.scene_repository import InMemorySceneRepository
from akasha.catalog.seed_db import THRESHOLD_PROFILES, VISUALIZATION_PROFILES
from akasha.catalog.tile_layer_repository import InMemoryTileLayerRepository
from akasha.config import Environment, RuntimeBackend, Settings
from akasha.jobs.stage_store import InMemoryStageStore
from akasha.jobs.store import InMemoryJobStore
from akasha.processing.cog import write_cog_bytes
from akasha.processing.sentinel2 import SENTINEL2_REQUIRED_ASSETS
from akasha.providers.contracts import NormalizedAsset, NormalizedStacItem, ProviderSearchRequest
from akasha.schemas import SyncRequest
from akasha.security import hash_api_key
from akasha.services.analytics import AnalyticsService
from akasha.services.sentinel2_ingestion import Sentinel2IngestionService
from akasha.services.source_mirroring import SourceMirroringService
from akasha.services.titiler_tiles import TiTilerTileService
from akasha.storage.object_store import InMemoryObjectStore


def test_offline_sentinel2_vertical_slice_returns_available_and_signed_urls(tmp_path) -> None:
    settings = Settings(
        environment=Environment.TEST,
        runtime_backend=RuntimeBackend.MEMORY,
        task_always_eager=True,
        api_key_hashes=f"test:{hash_api_key('test-akasha-key')}",
        scratch_dir=tmp_path,
        public_base_url="http://testserver",
        signing_secret="test-signing-secret",
    )
    object_store = InMemoryObjectStore()
    scene_repository = InMemorySceneRepository()
    asset_repository = InMemorySceneAssetRepository()
    raster_repository = InMemoryRasterRepository()
    tile_layer_repository = InMemoryTileLayerRepository(
        raster_repository=raster_repository,
        scene_repository=scene_repository,
    )
    field_query_repository = InMemoryFieldQueryRepository()
    visualization_profiles, threshold_profiles = build_memory_profiles(
        VISUALIZATION_PROFILES,
        THRESHOLD_PROFILES,
    )
    profile_repository = InMemoryProfileRepository(
        visualization_profiles=visualization_profiles,
        threshold_profiles=threshold_profiles,
    )
    payloads = _source_payloads()
    provider = _Provider(_item())
    pgstac_repository = _PgstacRepository()
    mirror = SourceMirroringService(
        object_store=object_store,
        settings=settings,
        client=httpx.Client(transport=httpx.MockTransport(_asset_handler(payloads))),
    )
    service = Sentinel2IngestionService(
        job_store=InMemoryJobStore(),
        stage_store=InMemoryStageStore(),
        aoi_repository=_AoiRepository(),
        scene_repository=scene_repository,
        asset_repository=asset_repository,
        raster_repository=raster_repository,
        object_store=object_store,
        backfill_repository=InMemoryBackfillRepository(),
        pgstac_repository=pgstac_repository,
        tile_layer_repository=tile_layer_repository,
        provider=provider,
        mirroring_service=mirror,
        settings=settings,
    )

    job = service.start_backfill(
        SyncRequest(
            source_id="sentinel-2-l2a",
            provider_route="earthsearch:sentinel-2-l2a",
            aoi_id="bangalore_60km_geodesic_aoi",
            date_start=date(2026, 1, 1),
            date_end=date(2026, 1, 31),
            job_type="sentinel2_backfill",
            mode="full_pipeline",
        )
    )

    summary = job.result_metadata["backfill_summary"]
    assert summary["searched_count"] == 1
    assert summary["mirrored_asset_count"] == len(SENTINEL2_REQUIRED_ASSETS)
    assert summary["processed_count"] == 6
    assert summary["failed_count"] == 0
    assert len(pgstac_repository.items) == 1

    published_scene = scene_repository.list_for_source_aoi(
        source_id="sentinel-2-l2a",
        aoi_id="bangalore_60km_geodesic_aoi",
    )[0]
    published_scene.pgstac_item_id = None
    published_scene.processing_state = "pending"
    scene_repository.upsert(published_scene)
    pgstac_repository.items.clear()

    duplicate = service.start_backfill(
        SyncRequest(
            source_id="sentinel-2-l2a",
            provider_route="earthsearch:sentinel-2-l2a",
            aoi_id="bangalore_60km_geodesic_aoi",
            date_start=date(2026, 1, 2),
            date_end=date(2026, 2, 1),
            job_type="sentinel2_backfill",
            mode="full_pipeline",
        )
    )
    duplicate_summary = duplicate.result_metadata["backfill_summary"]
    assert duplicate.job_id != job.job_id
    assert duplicate_summary["searched_count"] == 1
    assert duplicate_summary["accepted_count"] == 1
    assert duplicate_summary["mirrored_asset_count"] == 0
    assert duplicate_summary["processed_count"] == 0
    assert duplicate_summary["skipped_count"] == 1
    assert len(pgstac_repository.items) == 1
    repaired_scene = scene_repository.list_for_source_aoi(
        source_id="sentinel-2-l2a",
        aoi_id="bangalore_60km_geodesic_aoi",
    )[0]
    assert repaired_scene.pgstac_item_id is not None
    assert repaired_scene.processing_state == "complete"

    provider._item = replace(
        _item(),
        stac_item_id="S2A_CLOUDY_001",
        logical_scene_key="sentinel-2-l2a:S2A_CLOUDY_001",
        cloud_percent=35.0,
    )
    cloudy = service.start_backfill(
        SyncRequest(
            source_id="sentinel-2-l2a",
            provider_route="earthsearch:sentinel-2-l2a",
            aoi_id="bangalore_60km_geodesic_aoi",
            date_start=date(2026, 1, 3),
            date_end=date(2026, 2, 2),
            job_type="sentinel2_backfill",
            mode="full_pipeline",
        )
    )
    cloudy_summary = cloudy.result_metadata["backfill_summary"]
    assert cloudy_summary["searched_count"] == 1
    assert cloudy_summary["accepted_count"] == 1
    assert cloudy_summary["processed_count"] == 6
    assert cloudy_summary["skipped_count"] == 0
    assert len(scene_repository.list_for_source_aoi(
        source_id="sentinel-2-l2a",
        aoi_id="bangalore_60km_geodesic_aoi",
    )) == 2

    analytics = AnalyticsService(
        field_query_repository=field_query_repository,
        scene_repository=scene_repository,
        raster_repository=raster_repository,
        tile_layer_repository=tile_layer_repository,
        object_store=object_store,
        profile_repository=profile_repository,
        settings=settings,
    )
    app = create_app(settings, object_store=object_store)
    app.state.analytics_service = analytics
    app.state.tile_layer_repository = tile_layer_repository
    app.state.titiler_tile_service = _mock_titiler_service(settings)
    client = TestClient(app)

    response = client.post(
        "/api/v1/analytics/field-index",
        headers={"X-API-Key": "test-akasha-key"},
        json={
            "geometry": _field_geometry(),
            "crs": "EPSG:4326",
            "index": "NDVI",
            "date": "2026-01-15",
            "fallbackPolicy": "nearest_valid_scene",
            "maxCloudPercentage": 20,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["status"] == "AVAILABLE"
    serialized = str(body)
    assert "s3://" not in serialized
    assert "raw/" not in serialized
    assert "earth-search" not in serialized

    stats_url = urlparse(body["data"]["statsUrl"])
    stats_response = client.get(f"{stats_url.path}?{stats_url.query}")
    assert stats_response.status_code == 200
    assert stats_response.json()["data"]["queryId"] == body["data"]["queryId"]

    tile_url = urlparse(
        body["data"]["tileUrl"].replace("{z}", "1").replace("{x}", "1").replace("{y}", "1")
    )
    tile_response = client.get(f"{tile_url.path}?{tile_url.query}")
    assert tile_response.status_code == 200
    assert tile_response.headers["content-type"] == "image/png"
    assert tile_response.content == _MOCK_PNG
    assert "titiler" not in str(tile_response.content)

    # Field-clipped NDVI overlay: signed, app-domain, returns a real PNG (never the
    # full-scene tiles). No internal storage/host paths leak in the URL or bytes.
    assert body["data"]["overlayUrl"]
    overlay_url = urlparse(body["data"]["overlayUrl"])
    assert overlay_url.path.endswith("/overlay.png")
    overlay_response = client.get(f"{overlay_url.path}?{overlay_url.query}")
    assert overlay_response.status_code == 200
    assert overlay_response.headers["content-type"] == "image/png"
    assert overlay_response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_sentinel2_mirror_assets_reuses_existing_mirrors(tmp_path) -> None:
    service = Sentinel2IngestionService(
        job_store=InMemoryJobStore(),
        stage_store=InMemoryStageStore(),
        backfill_repository=InMemoryBackfillRepository(),
        settings=Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.MEMORY,
            scratch_dir=tmp_path,
        ),
        object_store=InMemoryObjectStore(),
        mirroring_service=_FailingMirrorService(),
    )
    existing = SceneAssetRecord(
        id="asset-1",
        scene_id="scene-1",
        asset_kind="source",
        asset_key="red",
        mirror_status="mirrored",
        mirror_object_path="raw/earthsearch/sentinel-2-l2a/S2A_TEST_001/source-cogs/red.tif",
        mirror_checksum_sha256="abc123",
        size_bytes=123,
    )

    assert service._mirror_assets(_item(), [existing]) == [existing]


def test_scene_failure_does_not_register_partial_index_outputs(tmp_path) -> None:
    settings = Settings(
        environment=Environment.TEST,
        runtime_backend=RuntimeBackend.MEMORY,
        task_always_eager=True,
        scratch_dir=tmp_path,
    )
    object_store = _FailingDerivedObjectStore()
    scene_repository = InMemorySceneRepository()
    asset_repository = InMemorySceneAssetRepository()
    raster_repository = InMemoryRasterRepository()
    tile_layer_repository = InMemoryTileLayerRepository(
        raster_repository=raster_repository,
        scene_repository=scene_repository,
    )
    mirror = SourceMirroringService(
        object_store=object_store,
        settings=settings,
        client=httpx.Client(transport=httpx.MockTransport(_asset_handler(_source_payloads()))),
    )
    service = Sentinel2IngestionService(
        job_store=InMemoryJobStore(),
        stage_store=InMemoryStageStore(),
        aoi_repository=_AoiRepository(),
        scene_repository=scene_repository,
        asset_repository=asset_repository,
        raster_repository=raster_repository,
        object_store=object_store,
        backfill_repository=InMemoryBackfillRepository(),
        tile_layer_repository=tile_layer_repository,
        provider=_Provider(_item()),
        mirroring_service=mirror,
        settings=settings,
    )

    job = service.start_backfill(
        SyncRequest(
            source_id="sentinel-2-l2a",
            provider_route="earthsearch:sentinel-2-l2a",
            aoi_id="bangalore_60km_geodesic_aoi",
            date_start=date(2026, 1, 15),
            date_end=date(2026, 1, 15),
            job_type="sentinel2_backfill",
            mode="full_pipeline",
        )
    )

    scene_ids = [
        scene.id
        for scene in scene_repository.list_for_source_aoi(
            source_id="sentinel-2-l2a",
            aoi_id="bangalore_60km_geodesic_aoi",
        )
        if scene.id is not None
    ]
    assert job.result_metadata["backfill_summary"]["failed_count"] == 1
    assert raster_repository.list_for_scene_ids(scene_ids) == []


class _Provider:
    def __init__(self, item: NormalizedStacItem) -> None:
        self._item = item

    def search(self, request: ProviderSearchRequest) -> list[NormalizedStacItem]:
        if self._item.acquisition_at is None:
            return []
        if request.date_start <= self._item.acquisition_at.date() <= request.date_end:
            return [self._item]
        return []


class _AoiRepository:
    def get(self, aoi_id: str) -> AoiRecord | None:
        return AoiRecord(
            aoi_id=aoi_id,
            name="Bangalore 60km",
            geometry=_field_geometry(),
            bbox=[77.0, 12.99, 77.004, 13.0],
        )


class _PgstacRepository:
    def __init__(self) -> None:
        self.items: list[object] = []

    def upsert_item_json(self, item: object) -> None:
        self.items.append(item)


class _FailingMirrorService:
    def mirror_asset(self, **_: object) -> object:
        raise AssertionError("already mirrored assets should not be downloaded again")


class _FailingDerivedObjectStore(InMemoryObjectStore):
    def __init__(self) -> None:
        super().__init__()
        self._derived_calls = 0

    def put_derived_cog(self, **kwargs):
        self._derived_calls += 1
        if self._derived_calls == 2:
            raise RuntimeError("simulated derived COG upload failure")
        return super().put_derived_cog(**kwargs)


_MOCK_PNG = b"\x89PNG\r\n\x1a\n" + b"mock-tile-bytes" * 8


def _mock_titiler_service(settings) -> TiTilerTileService:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "WebMercatorQuad" in request.url.path
        return httpx.Response(200, content=_MOCK_PNG, headers={"content-type": "image/png"})

    return TiTilerTileService(
        settings=settings,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _item() -> NormalizedStacItem:
    return NormalizedStacItem(
        provider_adapter="earthsearch",
        provider_collection="sentinel-2-l2a",
        source_id="sentinel-2-l2a",
        stac_item_id="S2A_TEST_001",
        logical_scene_key="sentinel-2-l2a:S2A_TEST_001",
        acquisition_at=datetime(2026, 1, 15, tzinfo=UTC),
        platform="sentinel-2a",
        constellation="sentinel-2",
        instrument="msi",
        mgrs_tile="T43PHQ",
        footprint=_field_geometry(),
        bbox=[77.0, 12.996, 77.004, 13.0],
        cloud_percent=2.0,
        assets={asset_key: _asset(asset_key) for asset_key in SENTINEL2_REQUIRED_ASSETS},
        raw_item={"id": "S2A_TEST_001", "type": "Feature"},
    )


def _asset(asset_key: str) -> NormalizedAsset:
    return NormalizedAsset(
        asset_key=asset_key,
        href=f"https://earth-search.test/S2A_TEST_001/{asset_key}.tif",
        scale=None if asset_key == "scl" else 0.0001,
        offset=0.0,
        nodata=0,
        spatial_resolution=10,
    )


def _asset_handler(payloads: dict[str, bytes]):
    def handler(request: httpx.Request) -> httpx.Response:
        asset_key = request.url.path.rsplit("/", 1)[-1].removesuffix(".tif")
        return httpx.Response(200, content=payloads[asset_key])

    return handler


def _source_payloads() -> dict[str, bytes]:
    values = {
        "blue": 800,
        "green": 900,
        "red": 1000,
        "nir": 5000,
        "nir08": 5200,
        "rededge1": 2500,
        "swir16": 1800,
        "swir22": 1700,
        "scl": 4,
    }
    return {
        key: write_cog_bytes(
            np.full((4, 4), value, dtype="uint16" if key != "scl" else "uint8"),
            transform=from_origin(77.0, 13.0, 0.001, 0.001),
            crs="EPSG:4326",
            nodata=0,
        )
        for key, value in values.items()
    }


def _field_geometry() -> dict:
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [77.0005, 12.9965],
                [77.0035, 12.9965],
                [77.0035, 12.9995],
                [77.0005, 12.9995],
                [77.0005, 12.9965],
            ]
        ],
    }
