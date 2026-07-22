from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from akasha.api.app import create_app
from akasha.catalog.asset_repository import SceneAssetRecord
from akasha.catalog.scene_repository import ProviderSceneRecord
from akasha.config import Environment, RuntimeBackend, Settings
from akasha.processing.eos04 import EOS04_SOURCE_ID
from akasha.processing.landsat import LANDSAT_PGSTAC_COLLECTION_ID, LANDSAT_SOURCE_ID
from akasha.processing.nisar import NISAR_SOURCE_ID
from akasha.security import hash_api_key
from akasha.services.natural_imagery import (
    SENTINEL2_SOURCE_ID,
    NaturalImageryService,
)

API_KEY = "test-akasha-key"
AOI_ID = "bangalore"


class _TileService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def fetch_tile(self, **kwargs):
        self.calls.append(kwargs)
        return b"png-bytes", "image/png"


def _app_with_scene(
    *,
    source_id: str = EOS04_SOURCE_ID,
    polarizations: list[str] | None = None,
    second_scene: bool = False,
) -> tuple[TestClient, _TileService]:
    app = create_app(
        Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.MEMORY,
            api_key_hashes=f"test:{hash_api_key(API_KEY)}",
        )
    )
    scene = app.state.scene_repository.upsert(
        ProviderSceneRecord(
            id=None,
            provider_adapter="bhoonidhi",
            source_id=source_id,
            provider_product_id=f"{source_id}-20260711",
            acquisition_at=datetime(2026, 7, 11, 5, 30, tzinfo=UTC),
            scene_geometry=(
                {
                    "type": "Polygon",
                    "coordinates": [
                        [[77.0, 12.0], [78.0, 12.0], [78.0, 13.0], [77.0, 13.0], [77.0, 12.0]]
                    ],
                }
                if source_id == SENTINEL2_SOURCE_ID
                else None
            ),
            status="accepted",
            cloud_percent=4.0 if source_id == SENTINEL2_SOURCE_ID else None,
            pgstac_item_id=f"{source_id}-20260711",
            aoi_id=AOI_ID,
        )
    )
    assert scene.id is not None
    asset_keys = (
        ("red", "green", "blue")
        if source_id == SENTINEL2_SOURCE_ID
        else ("analytic",)
        if source_id == LANDSAT_SOURCE_ID
        else ("backscatter",)
    )
    for asset_key in asset_keys:
        app.state.asset_repository.upsert(
            SceneAssetRecord(
                id=None,
                scene_id=scene.id,
                asset_kind="source" if source_id == SENTINEL2_SOURCE_ID else "prepared",
                asset_key=asset_key,
                mirror_status="mirrored" if source_id == SENTINEL2_SOURCE_ID else "not_required",
                mirror_object_path=(
                    f"raw/sentinel/{asset_key}.tif" if source_id == SENTINEL2_SOURCE_ID else None
                ),
                metadata={
                    "bbox": [77.0, 12.0, 78.0, 13.0],
                    "polarizations": polarizations or ["VV", "VH"],
                },
            )
        )
    if second_scene:
        duplicate = app.state.scene_repository.upsert(
            ProviderSceneRecord(
                id=None,
                provider_adapter="bhoonidhi",
                source_id=source_id,
                provider_product_id=f"{source_id}-20260711-B",
                acquisition_at=datetime(2026, 7, 11, 6, 30, tzinfo=UTC),
                status="accepted",
                pgstac_item_id=f"{source_id}-20260711-b",
                aoi_id=AOI_ID,
            )
        )
        assert duplicate.id is not None
        app.state.asset_repository.upsert(
            SceneAssetRecord(
                id=None,
                scene_id=duplicate.id,
                asset_kind="analytic",
                asset_key="backscatter",
                metadata={
                    "bbox": [77.0, 12.0, 78.0, 13.0],
                    "polarizations": polarizations or ["VV", "VH"],
                },
            )
        )
    tile_service = _TileService()
    app.state.natural_imagery_service = NaturalImageryService(
        scene_repository=app.state.scene_repository,
        asset_repository=app.state.asset_repository,
        tile_service=tile_service,
    )
    return TestClient(app), tile_service


def test_latest_imagery_search_returns_prepared_full_coverage_scene_and_same_scene_tiles() -> None:
    client, tile_service = _app_with_scene(source_id=SENTINEL2_SOURCE_ID)
    viewport = {
        "type": "Polygon",
        "coordinates": [[[77.1, 12.1], [77.2, 12.1], [77.2, 12.2], [77.1, 12.2], [77.1, 12.1]]],
    }

    response = client.post(
        "/api/v1/imagery/search",
        json={"viewport": viewport},
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 200
    candidate = response.json()["data"]["candidates"][0]
    assert candidate["sourceId"] == SENTINEL2_SOURCE_ID
    assert candidate["processingLevel"] == "L2A"
    assert candidate["cloudPercent"] == 4.0
    assert candidate["coveragePercent"] == 100.0
    assert candidate["coverageStatus"] == "full"
    assert candidate["usable"] is True

    tile = client.get(
        f"/api/v1/imagery/scenes/{candidate['sceneId']}/tiles/8/182/105.png",
        headers={"X-API-Key": API_KEY},
    )
    assert tile.status_code == 200
    assert tile_service.calls[-1]["assets"] == "red,green,blue"
    assert tile_service.calls[-1]["asset_bidx"] is None


def test_eos04_dates_expose_radar_metadata_without_optical_quality() -> None:
    client, _ = _app_with_scene()

    response = client.get(
        f"/api/v1/sources/{EOS04_SOURCE_ID}/dates",
        params={"aoiId": AOI_ID},
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["sourceId"] == EOS04_SOURCE_ID
    assert payload["dates"] == [
        {
            "acquisitionDate": "2026-07-11",
            "datetime": "2026-07-11T05:30:00Z",
            "tileAvailable": True,
            "sceneCount": 1,
            "bounds": [77.0, 12.0, 78.0, 13.0],
            "polarizations": ["VV", "VH"],
            "unavailableReason": None,
        }
    ]
    assert "cloud" not in str(payload).lower()


def test_eos04_tile_uses_backscatter_asset_and_fixed_db_rescale() -> None:
    client, tile_service = _app_with_scene()

    response = client.get(
        f"/api/v1/sources/{EOS04_SOURCE_ID}/dates/2026-07-11/tiles/8/182/105.png",
        params={"aoiId": AOI_ID},
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 200
    assert response.content == b"png-bytes"
    assert tile_service.calls == [
        {
            "collection_id": "akasha-eos-04-sar-mrs-l2b-backscatter-v1",
            "item_id": "eos-04-sar-mrs-l2b-20260711",
            "z": 8,
            "x": 182,
            "y": 105,
            "assets": "backscatter",
            "asset_bidx": "backscatter|1",
            "rescale": "-25,5",
        }
    ]


def test_landsat_dates_and_tile_use_true_colour_analytic_bands() -> None:
    client, tile_service = _app_with_scene(source_id=LANDSAT_SOURCE_ID)

    dates = client.get(
        f"/api/v1/sources/{LANDSAT_SOURCE_ID}/dates",
        params={"aoiId": AOI_ID},
        headers={"X-API-Key": API_KEY},
    )
    tile = client.get(
        f"/api/v1/sources/{LANDSAT_SOURCE_ID}/dates/2026-07-11/tiles/8/182/105.png",
        params={"aoiId": AOI_ID},
        headers={"X-API-Key": API_KEY},
    )

    assert dates.status_code == 200
    assert dates.json()["data"]["dates"][0]["tileAvailable"] is True
    assert tile.status_code == 200
    assert tile_service.calls == [
        {
            "collection_id": LANDSAT_PGSTAC_COLLECTION_ID,
            "item_id": f"{LANDSAT_SOURCE_ID}-20260711",
            "z": 8,
            "x": 182,
            "y": 105,
            "assets": "analytic",
            "asset_bidx": "analytic|3,2,1",
            "rescale": "0,0.3",
        }
    ]


def test_nisar_dates_and_tile_prefer_actual_hh_band() -> None:
    client, tile_service = _app_with_scene(
        source_id=NISAR_SOURCE_ID,
        polarizations=["HV", "HH"],
    )

    dates = client.get(
        f"/api/v1/sources/{NISAR_SOURCE_ID}/dates",
        params={"aoiId": AOI_ID},
        headers={"X-API-Key": API_KEY},
    )
    tile = client.get(
        f"/api/v1/sources/{NISAR_SOURCE_ID}/dates/2026-07-11/tiles/8/182/105.png",
        params={"aoiId": AOI_ID},
        headers={"X-API-Key": API_KEY},
    )

    assert dates.status_code == 200
    assert dates.json()["data"]["dates"][0]["polarizations"] == ["HV", "HH"]
    assert tile.status_code == 200
    assert tile_service.calls[0]["collection_id"] == ("akasha-nisar-ssar-beta-gcov-backscatter-v1")
    assert tile_service.calls[0]["asset_bidx"] == "backscatter|2"


def test_nisar_same_date_multiple_scenes_are_typed_unavailable() -> None:
    client, _ = _app_with_scene(source_id=NISAR_SOURCE_ID, second_scene=True)

    response = client.get(
        f"/api/v1/sources/{NISAR_SOURCE_ID}/dates",
        params={"aoiId": AOI_ID},
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 200
    date_entry = response.json()["data"]["dates"][0]
    assert date_entry["sceneCount"] == 2
    assert date_entry["tileAvailable"] is False
    assert "mosaic" in date_entry["unavailableReason"]


def test_natural_imagery_routes_require_api_key() -> None:
    client, _ = _app_with_scene()

    response = client.get(
        f"/api/v1/sources/{EOS04_SOURCE_ID}/dates",
        params={"aoiId": AOI_ID},
    )

    assert response.status_code == 401
