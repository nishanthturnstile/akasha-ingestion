from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime
from urllib.parse import urlparse

import httpx
import numpy as np
from rasterio.transform import from_origin

from akasha.catalog.aoi_repository import AoiRecord
from akasha.catalog.asset_repository import InMemorySceneAssetRepository
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
from akasha.processing.landsat import LANDSAT_REQUIRED_ASSETS
from akasha.providers.contracts import NormalizedAsset, NormalizedStacItem, ProviderSearchRequest
from akasha.schemas import FieldIndexRequest, SyncRequest
from akasha.services.analytics import AnalyticsService
from akasha.services.landsat_ingestion import LandsatIngestionService
from akasha.services.source_mirroring import SourceMirroringService
from akasha.storage.object_store import InMemoryObjectStore


def test_landsat_backfill_is_idempotent_and_field_ndvi_is_available(tmp_path) -> None:
    settings = Settings(
        environment=Environment.TEST,
        runtime_backend=RuntimeBackend.MEMORY,
        task_always_eager=True,
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
    provider = _Provider(_item())
    pgstac_repository = _PgstacRepository()
    mirror = SourceMirroringService(
        object_store=object_store,
        settings=settings,
        client=httpx.Client(
            transport=httpx.MockTransport(_asset_handler(_source_payloads()))
        ),
    )
    service = LandsatIngestionService(
        job_store=InMemoryJobStore(),
        stage_store=InMemoryStageStore(),
        backfill_repository=InMemoryBackfillRepository(),
        settings=settings,
        aoi_repository=_AoiRepository(),
        scene_repository=scene_repository,
        asset_repository=asset_repository,
        raster_repository=raster_repository,
        object_store=object_store,
        pgstac_repository=pgstac_repository,
        tile_layer_repository=tile_layer_repository,
        provider=provider,
        mirroring_service=mirror,
    )

    request = SyncRequest(
        source_id="landsat-c2-l2",
        provider_route="planetary-computer:landsat-c2-l2",
        aoi_id="bangalore_60km_geodesic_aoi",
        date_start=date(2026, 1, 1),
        date_end=date(2026, 1, 31),
        job_type="landsat_backfill",
        mode="full_pipeline",
    )
    job = service.start_backfill(request)

    summary = job.result_metadata["backfill_summary"]
    assert summary["searched_count"] == 1
    assert summary["accepted_count"] == 1
    assert summary["mirrored_asset_count"] == len(LANDSAT_REQUIRED_ASSETS)
    assert summary["processed_count"] == 6
    assert summary["failed_count"] == 0
    assert len(pgstac_repository.items) == 1

    scene = scene_repository.list_for_source_aoi(
        source_id="landsat-c2-l2",
        aoi_id="bangalore_60km_geodesic_aoi",
    )[0]
    assets = asset_repository.list_for_scene(scene.id or "")
    assert {asset.asset_key for asset in assets if asset.asset_kind == "prepared"} == {
        "analytic",
        "mask",
    }
    serialized_assets = str([asdict(asset) for asset in assets])
    assert "sig=test-secret" not in serialized_assets
    assert all("?" not in (asset.asset_href or "") for asset in assets)
    output_names = {
        output.index_name
        for output in raster_repository.list_for_scene_ids([scene.id or ""])
    }
    assert output_names == {
        "ndvi",
        "msavi",
        "ndmi",
        "ndwi_green_nir",
    }

    replay = service.start_backfill(request)
    assert replay.job_id == job.job_id
    assert len(scene_repository.list_for_source_aoi(
        source_id="landsat-c2-l2",
        aoi_id="bangalore_60km_geodesic_aoi",
    )) == 1
    assert len(pgstac_repository.items) == 1

    visualization_profiles, threshold_profiles = build_memory_profiles(
        VISUALIZATION_PROFILES,
        THRESHOLD_PROFILES,
    )
    analytics = AnalyticsService(
        field_query_repository=InMemoryFieldQueryRepository(),
        scene_repository=scene_repository,
        raster_repository=raster_repository,
        asset_repository=asset_repository,
        tile_layer_repository=tile_layer_repository,
        object_store=object_store,
        profile_repository=InMemoryProfileRepository(
            visualization_profiles=visualization_profiles,
            threshold_profiles=threshold_profiles,
        ),
        settings=settings,
    )
    result = analytics.field_index(
        FieldIndexRequest(
            geometry=_field_geometry(),
            sourceId="landsat-c2-l2",
            index="NDVI",
            date=date(2026, 1, 15),
            maxCloudPercentage=20,
        )
    )
    assert result.status == "AVAILABLE"
    assert result.source == "landsat-c2-l2"
    assert result.providerRoute == "planetary-computer:landsat-c2-l2"
    assert result.resolution.nativeMeters == 30
    assert result.statistics.usablePixelPercentage == 100
    assert urlparse(result.overlayUrl or "").netloc == "testserver"


class _Provider:
    def __init__(self, item: NormalizedStacItem) -> None:
        self._item = item

    def search(self, request: ProviderSearchRequest) -> list[NormalizedStacItem]:
        del request
        return [self._item]

    def signed_href(self, asset: NormalizedAsset) -> str:
        return f"{asset.href}?sig=test-secret"


class _AoiRepository:
    def get(self, aoi_id: str) -> AoiRecord:
        return AoiRecord(
            aoi_id=aoi_id,
            name="Bangalore 60km",
            geometry=_field_geometry(),
            bbox=[77.0, 12.996, 77.004, 13.0],
        )


class _PgstacRepository:
    def __init__(self) -> None:
        self.items: list[object] = []

    def upsert_item_json(self, item: object) -> None:
        self.items.append(item)


def _item() -> NormalizedStacItem:
    item_id = "LC09_L2SP_144051_20260115_02_T1"
    return NormalizedStacItem(
        provider_adapter="planetary-computer",
        provider_collection="landsat-c2-l2",
        source_id="landsat-c2-l2",
        stac_item_id=item_id,
        logical_scene_key=f"landsat-c2-l2:{item_id}",
        acquisition_at=datetime(2026, 1, 15, tzinfo=UTC),
        platform="landsat-9",
        constellation="landsat",
        instrument="oli-tirs",
        mgrs_tile=None,
        footprint=_field_geometry(),
        bbox=[77.0, 12.996, 77.004, 13.0],
        cloud_percent=2.0,
        assets={key: _asset(key, item_id) for key in LANDSAT_REQUIRED_ASSETS},
        raw_item={
            "id": item_id,
            "type": "Feature",
            "properties": {
                "platform": "landsat-9",
                "landsat:collection_number": "02",
                "landsat:collection_category": "T1",
                "landsat:correction": "L2SP",
            },
        },
    )


def _asset(asset_key: str, item_id: str) -> NormalizedAsset:
    reflectance = asset_key not in {"qa_pixel", "qa_radsat"}
    return NormalizedAsset(
        asset_key=asset_key,
        href=f"https://landsat.test/{item_id}/{asset_key}.tif",
        media_type="image/tiff; application=geotiff; profile=cloud-optimized",
        roles=["data"],
        band_common_name=asset_key if reflectance else None,
        scale=0.0000275 if reflectance else None,
        offset=-0.2 if reflectance else 0.0,
        nodata=0,
        spatial_resolution=30,
        selected_access_mode="signed_https",
    )


def _asset_handler(payloads: dict[str, bytes]):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["sig"] == "test-secret"
        key = request.url.path.rsplit("/", 1)[-1].removesuffix(".tif")
        return httpx.Response(200, content=payloads[key])

    return handler


def _source_payloads() -> dict[str, bytes]:
    values = {
        "blue": 10000,
        "green": 11000,
        "red": 12000,
        "nir08": 20000,
        "swir16": 15000,
        "swir22": 14000,
        "qa_pixel": 64,
        "qa_radsat": 0,
    }
    return {
        key: write_cog_bytes(
            np.full((4, 4), value, dtype="uint16"),
            transform=from_origin(77.0, 13.0, 0.001, 0.001),
            crs="EPSG:4326",
            nodata=None if key == "qa_radsat" else 0,
        )
        for key, value in values.items()
    }


def _field_geometry() -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[
            [77.0005, 12.9965],
            [77.0035, 12.9965],
            [77.0035, 12.9995],
            [77.0005, 12.9995],
            [77.0005, 12.9965],
        ]],
    }
