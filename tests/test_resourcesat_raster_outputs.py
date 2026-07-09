from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from akasha.catalog.raster_repository import InMemoryRasterRepository
from akasha.catalog.scene_repository import InMemorySceneRepository
from akasha.catalog.tile_layer_repository import InMemoryTileLayerRepository
from akasha.config import Settings
from akasha.processing.cog import write_cog_file
from akasha.processing.resourcesat import (
    LISS4_PROFILE,
    NDMI,
    NDWI_GREEN_NIR,
    RESOURCESAT_LISS3_BOA_SOURCE_ID,
    RESOURCESAT_LISS4_MX70_L2_SOURCE_ID,
    RESOURCESAT_MASK_METHOD,
)
from akasha.services.resourcesat_outputs import (
    generate_resourcesat_derived_indices,
    provider_scene_from_composite_manifest,
    provider_scene_from_prepare_manifest,
    scene_asset_records_from_composite_manifest,
    scene_asset_records_from_prepare_manifest,
)
from akasha.storage.object_store import InMemoryObjectStore, file_sha256


def test_resourcesat_prepare_and_composite_scene_contracts_are_deterministic(
    tmp_path: Path,
) -> None:
    prepare_manifest = _prepare_manifest(tmp_path)
    composite_manifest = _composite_manifest(tmp_path)

    prepared_scene = provider_scene_from_prepare_manifest(
        prepare_manifest,
        raw_object_path="raw/bhoonidhi/resourcesat-2a-liss3-boa/P1/original.zip",
    )
    composite_scene = provider_scene_from_composite_manifest(composite_manifest)

    assert prepared_scene.provider_adapter == "bhoonidhi"
    assert prepared_scene.provider_product_id == "P1"
    assert prepared_scene.logical_scene_key == (
        "resourcesat-2a-liss3-boa:ResourceSat-2A_LISS3_BOA:"
        "P1:2026-03-19T00:00:00+00:00"
    )
    assert prepared_scene.raw_object_path == (
        "raw/bhoonidhi/resourcesat-2a-liss3-boa/P1/original.zip"
    )
    assert composite_scene.provider_product_id == (
        "resourcesat-2a-liss3-boa:composite:bangalore_60km_geodesic_aoi:2026-03-19"
    )
    assert composite_scene.provider_metadata["composite"] is True
    assert composite_scene.provider_metadata["contributing_scenes"] == [
        {"id": "P1", "acquisition_datetime": "2026-03-19T00:00:00+00:00"}
    ]

    prepared_scene = replace(prepared_scene, id="scene-1")
    composite_scene = replace(composite_scene, id="scene-2")
    prepared_assets = scene_asset_records_from_prepare_manifest(
        prepared_scene,
        prepare_manifest,
        raw_object_path="raw/bhoonidhi/resourcesat-2a-liss3-boa/P1/original.zip",
        raw_checksum_sha256="raw-sha",
        raw_size_bytes=123,
    )
    composite_assets = scene_asset_records_from_composite_manifest(
        composite_scene,
        composite_manifest,
    )

    assert [asset.asset_key for asset in prepared_assets] == ["raw_zip", "analytic", "mask"]
    assert prepared_assets[0].asset_kind == "raw_package"
    assert prepared_assets[1].asset_kind == "prepared_analytic"
    assert composite_assets[0].asset_kind == "composite_analytic"
    assert composite_assets[1].asset_key == "mask"


def test_generate_resourcesat_derived_indices_excludes_unsupported_liss4_indices(
    tmp_path: Path,
) -> None:
    manifest_path = _write_liss4_manifest(tmp_path)
    settings = Settings(
        scratch_dir=tmp_path / "scratch",
        resourcesat_liss4_processing_resolution_m=6.0,
    )
    scene_repository = InMemorySceneRepository()
    scene = scene_repository.upsert(
        provider_scene_from_composite_manifest(
            json.loads(manifest_path.read_text(encoding="utf-8"))
        )
    )
    raster_repository = InMemoryRasterRepository()
    tile_layer_repository = InMemoryTileLayerRepository(
        raster_repository=raster_repository,
        scene_repository=scene_repository,
    )
    object_store = InMemoryObjectStore()

    result = generate_resourcesat_derived_indices(
        manifest_path=manifest_path,
        scene=scene,
        output_root=tmp_path / "indices",
        settings=settings,
        object_store=object_store,
        raster_repository=raster_repository,
        tile_layer_repository=tile_layer_repository,
        scene_repository=scene_repository,
        dry_run=True,
    )

    index_names = sorted(output.index_name for output in result.outputs)
    assert index_names == ["msavi", "ndvi", NDWI_GREEN_NIR]
    assert NDMI not in index_names
    for output in result.outputs:
        assert output.object_path.startswith(
            "indices/bhoonidhi/resourcesat-2a-liss4-mx70-l2/"
        )
        assert output.cloud_mask_version == RESOURCESAT_MASK_METHOD
        assert output.processing_profile_version == LISS4_PROFILE.processing_profile_version
        assert output.processing_resolution == 6.0
        assert output.metadata["pgstac_collection"] == LISS4_PROFILE.pgstac_collection
        assert object_store.get_required(f"{output.object_path}.sha256")

    ndwi_output = raster_repository.get_for_scene_index(
        scene_id=scene.id or "",
        index_name=NDWI_GREEN_NIR,
    )
    assert ndwi_output is not None
    assert ndwi_output.object_path.endswith("/ndwi_green_nir.cog.tif")
    with rasterio.open(result.local_paths[NDWI_GREEN_NIR]) as dataset:
        values = dataset.read(1)
    assert values[0, 0] < 0
    assert values[1, 1] == -32768


def _write_liss4_manifest(tmp_path: Path) -> Path:
    analytic_path = tmp_path / "analytic.tif"
    mask_path = tmp_path / "mask.tif"
    transform = from_origin(799992, 1290000, 6, 6)
    analytic = np.array(
        [
            [[1000, 1000], [1000, 1000]],
            [[2000, 2000], [2000, 2000]],
            [[6000, 6000], [6000, 6000]],
        ],
        dtype="uint16",
    )
    mask = np.array([[1, 4], [2, 0]], dtype="uint8")
    write_cog_file(
        analytic,
        analytic_path,
        transform=transform,
        crs="EPSG:32643",
        nodata=0,
        band_descriptions=("BAND2 GREEN", "BAND3 RED", "BAND4 NIR"),
    )
    write_cog_file(
        mask,
        mask_path,
        transform=transform,
        crs="EPSG:32643",
        nodata=0,
        band_descriptions=("mask",),
        overview_resampling="nearest",
    )
    manifest = {
        "schema_version": "resourcesat-composite-manifest-v1",
        "output_kind": "resource_sat_composite",
        "composite": True,
        "source_id": RESOURCESAT_LISS4_MX70_L2_SOURCE_ID,
        "collection": "ResourceSat-2A_LISS4-MX70_L2",
        "aoi_id": "bangalore_60km_geodesic_aoi",
        "composite_date": "2026-03-19",
        "composite_datetime": "2026-03-19T00:00:00+00:00",
        "bbox": [77.0, 12.5, 78.0, 13.5],
        "geometry": _geometry(),
        "grid": {
            "crs": "EPSG:32643",
            "resolution": 6.0,
            "width": 2,
            "height": 2,
            "transform": list(transform)[:6],
        },
        "mask_method": RESOURCESAT_MASK_METHOD,
        "metrics": {
            "coverage_percent": 75.0,
            "usable_pixel_percent": 50.0,
            "cloud_masked_percent": 25.0,
            "contributing_scenes": [
                {"id": "LISS4_001", "acquisition_datetime": "2026-03-19T00:00:00+00:00"}
            ],
        },
        "outputs": {
            "analytic": {
                "path": str(analytic_path),
                "checksum_sha256": file_sha256(analytic_path),
                "size_bytes": analytic_path.stat().st_size,
                "dtype": "uint16",
                "nodata": 0,
                "band_count": 3,
            },
            "mask": {
                "path": str(mask_path),
                "checksum_sha256": file_sha256(mask_path),
                "size_bytes": mask_path.stat().st_size,
                "dtype": "uint8",
                "nodata": 0,
                "band_count": 1,
            },
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _prepare_manifest(tmp_path: Path) -> dict:
    analytic = tmp_path / "prepared-analytic.tif"
    mask = tmp_path / "prepared-mask.tif"
    analytic.write_bytes(b"analytic")
    mask.write_bytes(b"mask")
    return {
        "source_id": RESOURCESAT_LISS3_BOA_SOURCE_ID,
        "collection": "ResourceSat-2A_LISS3_BOA",
        "product_id": "P1",
        "aoi_id": "bangalore_60km_geodesic_aoi",
        "acquisition_datetime": "2026-03-19T00:00:00+00:00",
        "path": "097",
        "row": "064",
        "geometry": _geometry(),
        "crs": "EPSG:32643",
        "mask_method": RESOURCESAT_MASK_METHOD,
        "akasha:metrics_provisional": True,
        "outputs": {
            "analytic": {
                "path": str(analytic),
                "checksum_sha256": file_sha256(analytic),
                "size_bytes": analytic.stat().st_size,
                "dtype": "uint16",
                "nodata": 0,
                "band_count": 4,
            },
            "mask": {
                "path": str(mask),
                "checksum_sha256": file_sha256(mask),
                "size_bytes": mask.stat().st_size,
                "dtype": "uint8",
                "nodata": 0,
                "band_count": 1,
            },
        },
    }


def _composite_manifest(tmp_path: Path) -> dict:
    analytic = tmp_path / "composite-analytic.tif"
    mask = tmp_path / "composite-mask.tif"
    analytic.write_bytes(b"analytic")
    mask.write_bytes(b"mask")
    return {
        "source_id": RESOURCESAT_LISS3_BOA_SOURCE_ID,
        "collection": "ResourceSat-2A_LISS3_BOA",
        "aoi_id": "bangalore_60km_geodesic_aoi",
        "composite_date": "2026-03-19",
        "composite_datetime": "2026-03-19T00:00:00+00:00",
        "geometry": _geometry(),
        "grid": {"crs": "EPSG:32643", "resolution": 24.0},
        "mask_method": RESOURCESAT_MASK_METHOD,
        "akasha:metrics_provisional": True,
        "metrics": {
            "coverage_percent": 100.0,
            "usable_pixel_percent": 100.0,
            "cloud_masked_percent": 0.0,
            "contributing_scenes": [
                {"id": "P1", "acquisition_datetime": "2026-03-19T00:00:00+00:00"}
            ],
        },
        "outputs": {
            "analytic": {
                "path": str(analytic),
                "checksum_sha256": file_sha256(analytic),
                "size_bytes": analytic.stat().st_size,
                "dtype": "uint16",
                "nodata": 0,
                "band_count": 4,
            },
            "mask": {
                "path": str(mask),
                "checksum_sha256": file_sha256(mask),
                "size_bytes": mask.stat().st_size,
                "dtype": "uint8",
                "nodata": 0,
                "band_count": 1,
            },
        },
    }


def _geometry() -> dict:
    return {
        "type": "Polygon",
        "coordinates": [
            [[77.0, 12.5], [78.0, 12.5], [78.0, 13.5], [77.0, 13.5], [77.0, 12.5]]
        ],
    }
