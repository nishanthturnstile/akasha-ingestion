from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

from akasha.catalog.aoi_repository import AoiRecord
from akasha.config import Settings
from akasha.processing.resourcesat import (
    RESOURCESAT_LISS3_BOA_SOURCE_ID,
    RESOURCESAT_LISS4_MX70_L2_SOURCE_ID,
)
from akasha.processing.resourcesat_composite import (
    AlignedResourceSatScene,
    aoi_mask_for_grid,
    build_best_available_composite,
    build_resource_sat_composite,
    composite_grid_crs,
    composite_policy,
    grid_from_aoi,
    profile_for_source,
    verify_resource_sat_composite,
)


def test_composite_grid_crs_ignores_generic_aoi_crs_and_derives_utm() -> None:
    aoi = _aoi(metadata={"crs": "EPSG:4326"})

    assert composite_grid_crs(aoi) == "EPSG:32643"


def test_composite_grid_crs_honors_projected_override_only() -> None:
    aoi = _aoi(metadata={"compositeGridCrs": "EPSG:3857"})

    assert composite_grid_crs(aoi) == "EPSG:3857"

    with pytest.raises(ValueError, match="must be projected"):
        composite_grid_crs(_aoi(metadata={"akasha:composite_grid_crs": "EPSG:4326"}))


def test_grid_from_aoi_uses_profile_resolution_and_aoi_mask_denominator() -> None:
    settings = Settings(resourcesat_liss3_processing_resolution_m=24.0)
    profile = profile_for_source(RESOURCESAT_LISS3_BOA_SOURCE_ID)
    grid = grid_from_aoi(_aoi(), profile, settings)
    aoi_mask = aoi_mask_for_grid(_aoi(), grid)

    assert grid.crs == "EPSG:32643"
    assert grid.resolution == 24.0
    assert grid.width >= 2
    assert grid.height >= 2
    assert 0 < int(aoi_mask.sum()) <= grid.width * grid.height


def test_best_available_composite_prefers_most_recent_valid_pixel_with_aoi_mask() -> None:
    older = _scene(
        "older",
        "2026-03-05T00:00:00+00:00",
        [[10, 20], [30, 40]],
        [[1, 2], [0, 3]],
    )
    newer = _scene(
        "newer",
        "2026-03-19T00:00:00+00:00",
        [[50, 60], [70, 80]],
        [[1, 1], [4, 0]],
    )
    aoi_mask = np.array([[True, True], [True, False]])

    result = build_best_available_composite([newer, older], aoi_mask=aoi_mask)

    assert result["analytic"][0].tolist() == [[50, 60], [70, 0]]
    assert result["mask"].tolist() == [[1, 1], [4, 0]]
    assert result["metrics"]["aoi_pixel_count"] == 3
    assert result["metrics"]["coverage_percent"] == 100.0
    assert result["metrics"]["usable_pixel_percent"] == 100.0
    assert [scene["id"] for scene in result["metrics"]["contributing_scenes"]] == [
        "older",
        "newer",
    ]


def test_build_resource_sat_composite_writes_manifest_and_verifies(tmp_path: Path) -> None:
    settings = Settings(
        scratch_dir=tmp_path / "scratch",
        resourcesat_liss3_processing_resolution_m=24.0,
    )
    transform = from_origin(799992, 1290000, 24, 24)
    older = _prepared_scene(
        tmp_path,
        source_id=RESOURCESAT_LISS3_BOA_SOURCE_ID,
        product_id="older",
        acquisition="2026-03-05T00:00:00Z",
        base=100,
        mask_values=[[1, 2], [0, 3]],
        transform=transform,
    )
    newer = _prepared_scene(
        tmp_path,
        source_id=RESOURCESAT_LISS3_BOA_SOURCE_ID,
        product_id="newer",
        acquisition="2026-03-19T00:00:00Z",
        base=500,
        mask_values=[[1, 1], [4, 0]],
        transform=transform,
    )

    result = build_resource_sat_composite(
        manifest_paths=[older, newer],
        aoi=_aoi(),
        output_root=tmp_path / "composites",
        settings=settings,
        dry_run=True,
    )

    assert result.analytic_cog.is_file()
    assert result.mask_cog.is_file()
    assert result.manifest["output_kind"] == "resource_sat_composite"
    assert result.manifest["aoi_id"] == "bangalore_60km_geodesic_aoi"
    assert result.manifest["grid"]["crs"] == "EPSG:32643"
    assert result.manifest["grid"]["resolution"] == 24.0
    assert result.manifest["metrics"]["coverage_percent"] == 100.0
    assert result.manifest["properties"]["akasha:metrics_provisional"] is True

    verify = verify_resource_sat_composite(result.manifest_path, settings=settings)
    assert verify.ok is True
    assert verify.problems == []


def test_verify_resource_sat_composite_records_low_liss3_coverage_warning(tmp_path: Path) -> None:
    settings = Settings(resourcesat_liss3_processing_resolution_m=24.0)
    scene = _prepared_scene(
        tmp_path,
        source_id=RESOURCESAT_LISS3_BOA_SOURCE_ID,
        product_id="low-coverage",
        acquisition="2026-03-19T00:00:00Z",
        base=100,
        mask_values=[[1, 0], [0, 0]],
        transform=from_origin(799992, 1290000, 24, 24),
    )

    result = build_resource_sat_composite(
        manifest_paths=[scene],
        aoi=_aoi(),
        output_root=tmp_path / "composites",
        settings=settings,
        dry_run=True,
    )

    assert result.manifest["metrics"]["coverage_percent"] == 25.0
    assert "coverage_below_threshold" in result.manifest["warnings"]
    assert verify_resource_sat_composite(result.manifest_path, settings=settings).ok is True


def test_build_resource_sat_composite_rejects_wrong_scene_aoi(tmp_path: Path) -> None:
    manifest = _prepared_scene(
        tmp_path,
        source_id=RESOURCESAT_LISS3_BOA_SOURCE_ID,
        product_id="wrong-aoi",
        acquisition="2026-03-19T00:00:00Z",
        base=100,
        mask_values=[[1, 1], [1, 1]],
        transform=from_origin(799992, 1290000, 24, 24),
        aoi_id="other-aoi",
    )

    with pytest.raises(ValueError, match="does not match requested AOI"):
        build_resource_sat_composite(
            manifest_paths=[manifest],
            aoi=_aoi(),
            output_root=tmp_path / "composites",
            settings=Settings(resourcesat_liss3_processing_resolution_m=24.0),
            dry_run=True,
        )


def test_verify_resource_sat_composite_recomputes_coverage_from_mask(tmp_path: Path) -> None:
    settings = Settings(
        resourcesat_liss3_processing_resolution_m=24.0,
        resourcesat_liss3_composite_min_coverage_percent=20.0,
    )
    scene = _prepared_scene(
        tmp_path,
        source_id=RESOURCESAT_LISS3_BOA_SOURCE_ID,
        product_id="coverage-tamper",
        acquisition="2026-03-19T00:00:00Z",
        base=100,
        mask_values=[[1, 0], [0, 0]],
        transform=from_origin(799992, 1290000, 24, 24),
    )
    result = build_resource_sat_composite(
        manifest_paths=[scene],
        aoi=_aoi(),
        output_root=tmp_path / "composites",
        settings=settings,
        dry_run=True,
    )
    manifest = result.manifest
    manifest["metrics"]["coverage_percent"] = 100.0
    manifest["metrics"]["usable_pixel_percent"] = 100.0
    result.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    verify = verify_resource_sat_composite(result.manifest_path, settings=settings)

    assert verify.ok is False
    assert any("does not match mask coverage" in problem for problem in verify.problems)


def test_verify_resource_sat_composite_rejects_false_composite_marker(
    tmp_path: Path,
) -> None:
    result = _valid_composite_result(tmp_path)
    manifest = result.manifest
    manifest["composite"] = False
    manifest["properties"]["akasha:composite"] = False
    result.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    verify = verify_resource_sat_composite(
        result.manifest_path,
        settings=Settings(resourcesat_liss3_processing_resolution_m=24.0),
    )

    assert verify.ok is False
    assert any("composite=true" in problem for problem in verify.problems)


def test_verify_resource_sat_composite_requires_contributing_scene_provenance(
    tmp_path: Path,
) -> None:
    result = _valid_composite_result(tmp_path)
    manifest = result.manifest
    manifest["metrics"].pop("contributing_scenes")
    manifest["properties"].pop("akasha:contributing_scenes")
    result.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    verify = verify_resource_sat_composite(
        result.manifest_path,
        settings=Settings(resourcesat_liss3_processing_resolution_m=24.0),
    )

    assert verify.ok is False
    assert any("contributing_scenes" in problem for problem in verify.problems)


def test_liss4_policy_allows_partial_coverage_warning() -> None:
    settings = Settings(resourcesat_liss4_composite_min_coverage_percent=10.0)
    policy = composite_policy(profile_for_source(RESOURCESAT_LISS4_MX70_L2_SOURCE_ID), settings)

    assert policy.min_coverage_percent == 10.0
    assert "partial_aoi_coverage_expected" in policy.warnings


def test_align_rejects_missing_acquisition_datetime(tmp_path: Path) -> None:
    manifest = _prepared_scene(
        tmp_path,
        source_id=RESOURCESAT_LISS3_BOA_SOURCE_ID,
        product_id="missing-date",
        acquisition=None,
        base=100,
        mask_values=[[1, 1], [1, 1]],
        transform=from_origin(799992, 1290000, 24, 24),
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["acquisition_datetime"] = None
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="acquisition datetime is required"):
        build_resource_sat_composite(
            manifest_paths=[manifest],
            aoi=_aoi(),
            output_root=tmp_path / "composites",
            settings=Settings(resourcesat_liss3_processing_resolution_m=24.0),
            dry_run=True,
        )


def _scene(
    scene_id: str,
    acquisition: str,
    values: list[list[int]],
    mask: list[list[int]],
) -> AlignedResourceSatScene:
    band = np.array(values, dtype="uint16")
    return AlignedResourceSatScene(
        scene_id=scene_id,
        acquisition_at=datetime.fromisoformat(acquisition),
        analytic=np.stack([band, band + 100, band + 200, band + 300]),
        mask=np.array(mask, dtype="uint8"),
    )


def _valid_composite_result(tmp_path: Path):
    settings = Settings(
        scratch_dir=tmp_path / "scratch",
        resourcesat_liss3_processing_resolution_m=24.0,
    )
    scene = _prepared_scene(
        tmp_path,
        source_id=RESOURCESAT_LISS3_BOA_SOURCE_ID,
        product_id="valid-composite",
        acquisition="2026-03-19T00:00:00Z",
        base=100,
        mask_values=[[1, 1], [1, 1]],
        transform=from_origin(799992, 1290000, 24, 24),
    )
    return build_resource_sat_composite(
        manifest_paths=[scene],
        aoi=_aoi(),
        output_root=tmp_path / "composites",
        settings=settings,
        dry_run=True,
    )


def _aoi(metadata: dict[str, object] | None = None) -> AoiRecord:
    west, south, east, north = _projected_bounds()
    transformer = Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)
    lon_w, lat_s = transformer.transform(west, south)
    lon_e, lat_n = transformer.transform(east, north)
    return AoiRecord(
        aoi_id="bangalore_60km_geodesic_aoi",
        name="Bangalore",
        geometry={
            "type": "Polygon",
            "coordinates": [
                [
                    [lon_w, lat_s],
                    [lon_e, lat_s],
                    [lon_e, lat_n],
                    [lon_w, lat_n],
                    [lon_w, lat_s],
                ]
            ],
        },
        bbox=[lon_w, lat_s, lon_e, lat_n],
        metadata=metadata or {},
    )


def _projected_bounds() -> tuple[int, int, int, int]:
    return (799992, 1289952, 800040, 1290000)


def _prepared_scene(
    tmp_path: Path,
    *,
    source_id: str,
    product_id: str,
    acquisition: str | None,
    base: int,
    mask_values: list[list[int]],
    transform,
    aoi_id: str = "bangalore_60km_geodesic_aoi",
) -> Path:
    scene_dir = tmp_path / product_id
    scene_dir.mkdir()
    band_count = 3 if source_id == RESOURCESAT_LISS4_MX70_L2_SOURCE_ID else 4
    analytic = np.stack(
        [
            np.full((2, 2), base + band_index * 10, dtype="uint16")
            for band_index in range(band_count)
        ]
    )
    with rasterio.open(
        scene_dir / "analytic.tif",
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=band_count,
        dtype="uint16",
        crs="EPSG:32643",
        transform=transform,
        nodata=0,
    ) as dataset:
        dataset.write(analytic)
    with rasterio.open(
        scene_dir / "mask.tif",
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype="uint8",
        crs="EPSG:32643",
        transform=transform,
        nodata=0,
    ) as dataset:
        dataset.write(np.array(mask_values, dtype="uint8"), 1)
    manifest = {
        "source_id": source_id,
        "collection": profile_for_source(source_id).collection_id,
        "product_id": product_id,
        "aoi_id": aoi_id,
        "acquisition_datetime": acquisition,
        "outputs": {
            "analytic": {"path": "analytic.tif"},
            "mask": {"path": "mask.tif"},
        },
    }
    manifest_path = scene_dir / "prepare_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path
