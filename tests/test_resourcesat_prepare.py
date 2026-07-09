from __future__ import annotations

import json
import stat
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from akasha.config import Settings
from akasha.processing.resourcesat import (
    LISS3_PROFILE,
    LISS4_PROFILE,
    RESOURCESAT_AWIFS_BOA_SOURCE_ID,
    RESOURCESAT_LISS3_BOA_SOURCE_ID,
    RESOURCESAT_LISS4_MX70_L2_SOURCE_ID,
)
from akasha.processing.resourcesat_prepare import (
    ResourceSatPrepareError,
    SelectedResourceSatProduct,
    discover_band_files,
    prepare_resourcesat_product,
    safe_extract_product,
)
from akasha.providers.contracts import ProviderErrorCategory


def test_safe_extract_rejects_path_escape_and_cleans_partial_extract(tmp_path: Path) -> None:
    package = tmp_path / "bad.zip"
    extract_root = tmp_path / "extract"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("safe.txt", "ok")
        archive.writestr("../evil.txt", "bad")

    with pytest.raises(ResourceSatPrepareError) as error:
        safe_extract_product(package, extract_root)

    assert error.value.category == ProviderErrorCategory.INVALID_PRODUCT
    assert not extract_root.exists()


def test_safe_extract_rejects_symlink_entries(tmp_path: Path) -> None:
    package = tmp_path / "bad-symlink.zip"
    info = zipfile.ZipInfo("link")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(info, "target")

    with pytest.raises(ResourceSatPrepareError, match="unsafe Bhoonidhi ZIP member path"):
        safe_extract_product(package, tmp_path / "extract")


def test_safe_extract_rejects_expanded_size_before_extracting(tmp_path: Path) -> None:
    package = tmp_path / "too-large.zip"
    extract_root = tmp_path / "extract"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("large.txt", "too large")

    with pytest.raises(ResourceSatPrepareError, match="expanded size exceeds"):
        safe_extract_product(package, extract_root, max_expanded_bytes=1)

    assert not extract_root.exists()


def test_discover_band_files_requires_every_profile_band(tmp_path: Path) -> None:
    extract_root = tmp_path / "extract"
    extract_root.mkdir()
    for band_name in ("BAND2", "BAND3", "BAND4"):
        (extract_root / f"product_{band_name}.tif").write_bytes(b"not-a-real-tif")

    with pytest.raises(ResourceSatPrepareError) as error:
        discover_band_files(extract_root, LISS3_PROFILE)

    assert error.value.category == ProviderErrorCategory.INVALID_PRODUCT
    assert error.value.metadata["missing_bands"] == ["BAND5"]


def test_prepare_liss3_product_writes_four_band_analytic_mask_and_manifest(
    tmp_path: Path,
) -> None:
    package = _package(
        tmp_path,
        source_id=RESOURCESAT_LISS3_BOA_SOURCE_ID,
        product_id="LISS3_P1",
        band_values=_four_band_values(),
    )
    product = SelectedResourceSatProduct(
        source_id=RESOURCESAT_LISS3_BOA_SOURCE_ID,
        product_id="LISS3_P1",
        package_path=package,
        acquisition_at=datetime.fromisoformat("2026-01-02T03:04:05+00:00"),
        aoi_id="bangalore_60km_geodesic_aoi",
        geometry={"type": "Polygon", "coordinates": []},
    )

    prepared = prepare_resourcesat_product(
        product,
        Settings(scratch_dir=tmp_path / "scratch"),
        dry_run=True,
    )

    assert prepared.collection_id == LISS3_PROFILE.collection_id
    assert prepared.analytic_checksum_sha256
    assert prepared.mask_checksum_sha256
    assert prepared.band_metadata["BAND2"].path == "145"
    assert prepared.band_metadata["BAND2"].row == "051"
    assert prepared.band_metadata["BAND2"].valid_range == (1, 10000)
    assert prepared.band_metadata["BAND2"].background_values == (0,)
    assert prepared.manifest["akasha:metrics_provisional"] is True
    assert prepared.manifest["mask_method"] == "akasha-threshold-mask-v1"
    assert prepared.manifest["band_role_mapping"]["GREEN"] == "BAND2"
    assert prepared.manifest["outputs"]["analytic"]["band_count"] == 4
    assert prepared.manifest["outputs"]["mask"]["band_count"] == 1

    with rasterio.open(prepared.analytic_path) as analytic:
        assert analytic.count == 4
        assert analytic.dtypes == ("uint16", "uint16", "uint16", "uint16")
        assert analytic.descriptions == (
            "BAND2 GREEN",
            "BAND3 RED",
            "BAND4 NIR",
            "BAND5 SWIR1",
        )
        assert analytic.tags()["AKASHA_SOURCE_ID"] == RESOURCESAT_LISS3_BOA_SOURCE_ID

    with rasterio.open(prepared.mask_path) as mask:
        values = mask.read(1)
        assert mask.count == 1
        assert mask.dtypes == ("uint8",)
        assert set(np.unique(values).tolist()) == {0, 1, 2, 3, 4}


def test_prepare_liss4_product_writes_three_band_analytic(tmp_path: Path) -> None:
    package = _package(
        tmp_path,
        source_id=RESOURCESAT_LISS4_MX70_L2_SOURCE_ID,
        product_id="LISS4_P1",
        band_values={
            "BAND2": np.full((8, 8), 1000, dtype="uint16"),
            "BAND3": np.full((8, 8), 2000, dtype="uint16"),
            "BAND4": np.full((8, 8), 6000, dtype="uint16"),
        },
    )

    prepared = prepare_resourcesat_product(
        SelectedResourceSatProduct(
            source_id=RESOURCESAT_LISS4_MX70_L2_SOURCE_ID,
            product_id="LISS4_P1",
            package_path=package,
        ),
        Settings(scratch_dir=tmp_path / "scratch"),
        dry_run=True,
    )

    with rasterio.open(prepared.analytic_path) as analytic:
        assert analytic.count == 3
        assert analytic.descriptions == ("BAND2 GREEN", "BAND3 RED", "BAND4 NIR")
    assert prepared.manifest["collection"] == LISS4_PROFILE.collection_id
    assert prepared.manifest["band_order"] == ["BAND2", "BAND3", "BAND4"]


def test_prepare_awifs_product_writes_four_band_analytic(tmp_path: Path) -> None:
    package = _package(
        tmp_path,
        source_id=RESOURCESAT_AWIFS_BOA_SOURCE_ID,
        product_id="AWIFS_P1",
        band_values=_four_band_values(shape=(8, 8)),
    )

    prepared = prepare_resourcesat_product(
        SelectedResourceSatProduct(
            source_id=RESOURCESAT_AWIFS_BOA_SOURCE_ID,
            product_id="AWIFS_P1",
            package_path=package,
        ),
        Settings(scratch_dir=tmp_path / "scratch"),
        dry_run=True,
    )

    with rasterio.open(prepared.analytic_path) as analytic:
        assert analytic.count == 4
    assert prepared.manifest["source_id"] == RESOURCESAT_AWIFS_BOA_SOURCE_ID


def test_prepare_live_mode_fails_closed_for_default_tmp_scratch(tmp_path: Path) -> None:
    package = _package(
        tmp_path,
        source_id=RESOURCESAT_LISS3_BOA_SOURCE_ID,
        product_id="LISS3_P1",
        band_values=_four_band_values(shape=(8, 8)),
    )

    with pytest.raises(ValueError, match="unsafe ResourceSat runtime root"):
        prepare_resourcesat_product(
            SelectedResourceSatProduct(
                source_id=RESOURCESAT_LISS3_BOA_SOURCE_ID,
                product_id="LISS3_P1",
                package_path=package,
            ),
            Settings(),
            dry_run=False,
        )


def _package(
    tmp_path: Path,
    *,
    source_id: str,
    product_id: str,
    band_values: dict[str, np.ndarray],
) -> Path:
    source_dir = tmp_path / f"{product_id}-source"
    source_dir.mkdir()
    transform = from_origin(77.0, 13.0, 0.0002, 0.0002)
    for band_name, values in band_values.items():
        _write_band(source_dir / f"{product_id}_{band_name}.tif", values, transform=transform)
    metadata = {
        "source_id": source_id,
        "product_id": product_id,
        "path": "145",
        "row": "051",
        "acquisition_datetime": "2026-01-02T03:04:05Z",
        "valid_range": [1, 10000],
        "background_values": [0],
        "reflectance_scale": 0.0001,
        "reflectance_offset": 0.0,
    }
    (source_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    package = tmp_path / f"{product_id}.zip"
    with zipfile.ZipFile(package, "w") as archive:
        for path in source_dir.iterdir():
            archive.write(path, arcname=f"{product_id}/{path.name}")
    return package


def _write_band(path: Path, values: np.ndarray, *, transform) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=values.shape[1],
        height=values.shape[0],
        count=1,
        dtype=str(values.dtype),
        crs="EPSG:4326",
        transform=transform,
        nodata=0,
    ) as dataset:
        dataset.write(values, 1)


def _four_band_values(shape: tuple[int, int] = (8, 8)) -> dict[str, np.ndarray]:
    green = np.full(shape, 1000, dtype="uint16")
    red = np.full(shape, 2000, dtype="uint16")
    nir = np.full(shape, 6000, dtype="uint16")
    swir = np.full(shape, 3000, dtype="uint16")

    green[0, 0] = red[0, 0] = nir[0, 0] = swir[0, 0] = 0
    green[0, 1] = red[0, 1] = nir[0, 1] = 5000
    swir[0, 1] = 3000
    green[0, 2] = red[0, 2] = nir[0, 2] = swir[0, 2] = 500
    green[1, 0] = 5000
    red[1, 0] = nir[1, 0] = swir[1, 0] = 1000

    return {"BAND2": green, "BAND3": red, "BAND4": nir, "BAND5": swir}
