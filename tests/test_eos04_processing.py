from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from akasha.config import RuntimeBackend, Settings
from akasha.processing.eos04 import (
    EOS04_NODATA,
    EOS04_VALID_MASK_VALUE,
    Eos04PrepareError,
    SelectedEos04Product,
    gamma0_dn_to_db,
    normalize_polarizations,
    polarization_from_filename,
    prepare_eos04_product,
)


def test_eos04_polarizations_are_explicit_and_deterministic() -> None:
    assert normalize_polarizations("rv, rh, zz, rh") == ("RH", "RV")
    assert polarization_from_filename("scene_band_HH_sigma0.tif") == "HH"
    assert polarization_from_filename("backscatter.tif") is None


def test_eos04_l2b_gamma0_calibration_uses_noise_and_beta0_constant() -> None:
    values = np.array([100.0, 50.0, 10.0])

    calibrated = gamma0_dn_to_db(
        values,
        calibration_constant_db=40.0,
        noise_bias=100.0,
    )

    assert calibrated[0] == pytest.approx(10 * np.log10(9_900) - 40)
    assert calibrated[1] == pytest.approx(10 * np.log10(2_400) - 40)
    assert np.isnan(calibrated[2])


def test_prepare_eos04_l2b_product_writes_masked_multiband_db_cog(tmp_path: Path) -> None:
    archive = _l2b_archive(tmp_path, {"HH": 1_000, "HV": 400})
    prepared = prepare_eos04_product(
        SelectedEos04Product(
            product_id="EOS04_TEST_20260718",
            package_path=archive,
            acquisition_at=datetime(2026, 7, 18, tzinfo=UTC),
            aoi_id="bangalore_60km_geodesic_aoi",
        ),
        Settings(runtime_backend=RuntimeBackend.MEMORY, scratch_dir=tmp_path / "scratch"),
        dry_run=True,
    )

    assert prepared.polarizations == ("HH", "HV")
    assert prepared.manifest["input_representation"] == "uint16_gamma0_dn"
    assert prepared.manifest["valid_mask_value"] == EOS04_VALID_MASK_VALUE
    assert prepared.manifest["rtc_apply_flag"] == 1
    with rasterio.open(prepared.backscatter_path) as dataset:
        assert dataset.count == 2
        assert dataset.descriptions == ("HH", "HV")
        assert dataset.dtypes == ("float32", "float32")
        assert dataset.nodata == EOS04_NODATA
        assert dataset.read(1)[0, 0] == EOS04_NODATA
        expected_hh = 10 * np.log10(1_000**2 - 100) - 60
        expected_hv = 10 * np.log10(400**2 - 25) - 60
        assert dataset.read(1)[1, 1] == pytest.approx(expected_hh)
        assert dataset.read(2)[1, 1] == pytest.approx(expected_hv)


def test_prepare_eos04_product_preserves_rh_rv_polarizations(tmp_path: Path) -> None:
    archive = _l2b_archive(tmp_path, {"RH": 600, "RV": 300})
    prepared = prepare_eos04_product(
        SelectedEos04Product(
            product_id="EOS04_COMPACT_POL",
            package_path=archive,
            acquisition_at=datetime(2026, 7, 18, tzinfo=UTC),
            aoi_id="bangalore_60km_geodesic_aoi",
        ),
        Settings(runtime_backend=RuntimeBackend.MEMORY, scratch_dir=tmp_path / "scratch"),
        dry_run=True,
    )

    assert prepared.polarizations == ("RH", "RV")
    with rasterio.open(prepared.backscatter_path) as dataset:
        assert dataset.descriptions == ("RH", "RV")


def test_prepare_eos04_product_fails_closed_without_valid_l2b_metadata(tmp_path: Path) -> None:
    missing_meta = _l2b_archive(tmp_path / "missing", {"HH": 1_000}, include_meta=False)
    invalid_rtc = _l2b_archive(tmp_path / "rtc", {"HH": 1_000}, rtc_apply_flag=0)
    settings = Settings(runtime_backend=RuntimeBackend.MEMORY, scratch_dir=tmp_path / "scratch")

    with pytest.raises(Eos04PrepareError, match="BAND_META"):
        prepare_eos04_product(
            SelectedEos04Product("MISSING", missing_meta, None, "aoi"),
            settings,
            dry_run=True,
        )
    with pytest.raises(Eos04PrepareError, match="validated MRS L2B ARD profile"):
        prepare_eos04_product(
            SelectedEos04Product("INVALID_RTC", invalid_rtc, None, "aoi"),
            settings,
            dry_run=True,
        )


def _l2b_archive(
    tmp_path: Path,
    values_by_polarization: dict[str, int],
    *,
    include_meta: bool = True,
    rtc_apply_flag: int = 1,
) -> Path:
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    transform = from_origin(646_200, 1_443_600, 18, 18)
    for polarization, value in values_by_polarization.items():
        path = source_dir / f"scene_{polarization}" / f"imagery_{polarization}.tif"
        path.parent.mkdir()
        values = np.full((32, 32), value, dtype="uint16")
        _write_tiff(path, values, transform=transform)
    mask = np.full((32, 32), EOS04_VALID_MASK_VALUE, dtype="uint16")
    mask[0, 0] = 16
    _write_tiff(source_dir / "EOS04_mask.tif", mask, transform=transform)
    if include_meta:
        polarizations = list(values_by_polarization)
        lines = [
            "ProductType=L2B-ARD-PRODUCT",
            "SatID=EOS-04",
            "Sensor=SAR",
            "ImagingMode=MRS",
            f"RTC_Apply_Flag={rtc_apply_flag}",
            "Missing_Frames_Flag=0",
            f"NoOfPolarizations={len(polarizations)}",
        ]
        for index, polarization in enumerate(polarizations, start=1):
            lines.extend(
                [
                    f"TxRxPol{index}={polarization}",
                    f"Calibration_Constant_Beta0_{polarization}=60",
                    f"Image_Noise_Bias_{polarization}={'100' if index == 1 else '25'}",
                ]
            )
        (source_dir / "BAND_META.txt").write_text("\n".join(lines), encoding="utf-8")
    archive = tmp_path / "eos04.zip"
    with zipfile.ZipFile(archive, "w") as target:
        for path in source_dir.rglob("*"):
            if path.is_file():
                target.write(path, arcname=path.relative_to(source_dir))
    return archive


def _write_tiff(path: Path, values: np.ndarray, *, transform) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=values.shape[1],
        height=values.shape[0],
        count=1,
        dtype=str(values.dtype),
        crs="EPSG:32643",
        transform=transform,
        nodata=0,
        tiled=True,
        blockxsize=16,
        blockysize=16,
    ) as dataset:
        dataset.write(values, 1)
