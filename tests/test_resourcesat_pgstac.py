from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import PurePosixPath
from uuid import UUID

import pystac
import pytest

from akasha.catalog.pgstac_repository import (
    CLASSIFICATION_EXTENSION,
    EO_EXTENSION,
    PROJECTION_EXTENSION,
    RASTER_EXTENSION,
    RESOURCESAT_LISS3_DERIVED_COLLECTION_ID,
    PgstacRepository,
    _json_dumps,
    build_resourcesat_derived_item,
    collection_json,
)
from akasha.catalog.raster_repository import RasterOutputRecord
from akasha.catalog.scene_repository import ProviderSceneRecord
from akasha.processing.resourcesat import (
    LISS3_PROFILE,
    NDVI,
    NDWI_GREEN_NIR,
    RESOURCESAT_LISS3_BOA_SOURCE_ID,
    RESOURCESAT_MASK_METHOD,
)


def test_build_resourcesat_derived_item_sets_collection_assets_and_extensions() -> None:
    scene = _resourcesat_scene()
    outputs = [
        _resourcesat_output(NDVI, "s3://akasha-data/indices/rs/ndvi.cog.tif"),
        _resourcesat_output(
            NDWI_GREEN_NIR,
            "s3://akasha-data/indices/rs/ndwi_green_nir.cog.tif",
        ),
    ]

    item = build_resourcesat_derived_item(
        scene=scene,
        outputs=outputs,
        bbox=[77.0, 12.5, 78.0, 13.5],
        geometry=_geometry(),
    )
    item.validate()
    item_dict = item.to_dict()

    assert item.collection_id == RESOURCESAT_LISS3_DERIVED_COLLECTION_ID
    assert item.id.startswith("rs2a-liss3-boa-bangalore_60km_geodesic_aoi-20260319T000000-")
    assert EO_EXTENSION in item_dict["stac_extensions"]
    assert RASTER_EXTENSION in item_dict["stac_extensions"]
    assert PROJECTION_EXTENSION in item_dict["stac_extensions"]
    assert CLASSIFICATION_EXTENSION in item_dict["stac_extensions"]
    assert item.assets[NDWI_GREEN_NIR].href == (
        "s3://akasha-data/indices/rs/ndwi_green_nir.cog.tif"
    )
    assert "ndwi" not in item.assets
    assert item.assets[NDVI].extra_fields["bands"][0]["nodata"] == -32768
    assert item.assets[NDVI].extra_fields["bands"][0]["raster:scale"] == 0.0001
    assert item.assets[NDVI].extra_fields["proj:shape"] == [2, 2]
    assert item_dict["properties"]["eo:cloud_cover"] == 0.0
    classes = item_dict["properties"]["classification:classes"]
    assert [item["name"] for item in classes] == [
        "nodata",
        "valid",
        "cloud",
        "shadow",
        "water",
    ]
    assert item_dict["properties"]["akasha:mask_method"] == RESOURCESAT_MASK_METHOD


def test_resourcesat_collection_metadata_is_not_sentinel_metadata() -> None:
    collection = collection_json(RESOURCESAT_LISS3_DERIVED_COLLECTION_ID)

    pystac.Collection.from_dict(collection).validate()

    assert collection["id"] == RESOURCESAT_LISS3_DERIVED_COLLECTION_ID
    assert "ResourceSat-2A LISS-3 BOA" in collection["title"]
    assert "Sentinel-2" not in collection["description"]
    assert collection["summaries"]["akasha:source_id"] == [RESOURCESAT_LISS3_BOA_SOURCE_ID]
    assert collection["summaries"]["akasha:supported_indices"] == list(
        LISS3_PROFILE.supported_indices
    )
    assert collection["summaries"]["classification:classes"][0]["name"] == "nodata"
    assert collection["summaries"]["bands"][0]["eo:common_name"] == "green"
    assert collection["summaries"]["bands"][0]["raster:spatial_resolution"] == 23.5


def test_pgstac_repository_registers_resourcesat_collection_explicitly() -> None:
    engine = _FakeEngine()
    repository = PgstacRepository(engine)  # type: ignore[arg-type]
    item = build_resourcesat_derived_item(
        scene=_resourcesat_scene(),
        outputs=[_resourcesat_output(NDVI, "s3://akasha-data/indices/rs/ndvi.cog.tif")],
        bbox=[77.0, 12.5, 78.0, 13.5],
        geometry=_geometry(),
    )

    repository.upsert_item_json(item)

    assert engine.params
    collection_payload = engine.params[0]["collection"]
    assert RESOURCESAT_LISS3_DERIVED_COLLECTION_ID in collection_payload
    assert "ResourceSat-2A LISS-3 BOA" in collection_payload
    assert "Sentinel-2 L2A" not in collection_payload


def test_pgstac_json_serializes_database_native_metadata_types() -> None:
    identifier = UUID("11111111-1111-4111-8111-111111111111")

    payload = json.loads(
        _json_dumps(
            {
                "integral": Decimal("24.0"),
                "fractional": Decimal("28.32"),
                "timestamp": datetime(2026, 3, 19, tzinfo=UTC),
                "identifier": identifier,
                "path": PurePosixPath("indices/ndvi.tif"),
            }
        )
    )

    assert payload == {
        "fractional": 28.32,
        "identifier": str(identifier),
        "integral": 24,
        "path": "indices/ndvi.tif",
        "timestamp": "2026-03-19T00:00:00+00:00",
    }


def test_pgstac_json_rejects_non_finite_decimal() -> None:
    with pytest.raises(TypeError, match="Non-finite Decimal"):
        _json_dumps({"invalid": Decimal("NaN")})


def _resourcesat_scene() -> ProviderSceneRecord:
    return ProviderSceneRecord(
        id="scene-1",
        provider_adapter="bhoonidhi",
        source_id=RESOURCESAT_LISS3_BOA_SOURCE_ID,
        provider_product_id="resourcesat-2a-liss3-boa:composite:bangalore_60km_geodesic_aoi:2026-03-19",
        acquisition_at=datetime(2026, 3, 19, tzinfo=UTC),
        logical_scene_key=(
            "resourcesat-2a-liss3-boa:ResourceSat-2A_LISS3_BOA:"
            "composite:2026-03-19"
        ),
        scene_geometry=_geometry(),
        cloud_percent=0.0,
        aoi_id="bangalore_60km_geodesic_aoi",
        provider_metadata={
            "provider_collection": "ResourceSat-2A_LISS3_BOA",
            "output_kind": "resource_sat_composite",
            "mask_method": RESOURCESAT_MASK_METHOD,
        },
    )


def _resourcesat_output(index_name: str, href: str) -> RasterOutputRecord:
    return RasterOutputRecord(
        id=f"output-{index_name}",
        scene_id="scene-1",
        output_kind="derived_index",
        index_name=index_name,
        object_path=f"indices/bhoonidhi/resourcesat-2a-liss3-boa/scene-1/{index_name}.cog.tif",
        checksum_sha256="abc123",
        formula_version=f"{index_name}-resourcesat-v1",
        processing_profile_version=LISS3_PROFILE.processing_profile_version,
        dtype="int16",
        scale_factor=10000,
        offset=0.0,
        nodata_value=-32768,
        min_value=-5000,
        max_value=9000,
        native_resolution=23.5,
        processing_resolution=24.0,
        display_resolution=24.0,
        crs="EPSG:32643",
        cloud_mask_version=RESOURCESAT_MASK_METHOD,
        metadata={
            "pgstac_collection": RESOURCESAT_LISS3_DERIVED_COLLECTION_ID,
            "pgstac_asset_key": index_name,
            "pgstac_href": href,
            "proj_shape": [2, 2],
            "proj_transform": [24.0, 0.0, 799992.0, 0.0, -24.0, 1290000.0],
            "proj_bbox": [799992.0, 1289952.0, 800040.0, 1290000.0],
        },
    )


def _geometry() -> dict:
    return {
        "type": "Polygon",
        "coordinates": [
            [[77.0, 12.5], [78.0, 12.5], [78.0, 13.5], [77.0, 13.5], [77.0, 12.5]]
        ],
    }


class _FakeEngine:
    def __init__(self) -> None:
        self.params: list[dict] = []

    def begin(self):
        return _FakeConnection(self)


class _FakeConnection:
    def __init__(self, engine: _FakeEngine) -> None:
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:  # type: ignore[no-untyped-def]
        return None

    def execute(self, statement, params=None):  # type: ignore[no-untyped-def]
        del statement
        self._engine.params.append(params or {})
        return None
