from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import h5py
import numpy as np
import pytest
import rasterio

from akasha.config import RuntimeBackend, Settings
from akasha.processing.nisar import (
    NISAR_NODATA,
    NisarPrepareError,
    SelectedNisarProduct,
    discover_gcov_assets,
    gamma0_power_to_db,
    prepare_nisar_product,
)


def test_gamma0_power_conversion_does_not_epsilon_clamp_invalid_values() -> None:
    converted = gamma0_power_to_db(np.array([1.0, 0.1, 0.01, 0.0, -1.0]))

    assert converted[:3] == pytest.approx([0.0, -10.0, -20.0])
    assert np.isnan(converted[3:]).all()


def test_prepare_nisar_gcov_writes_masked_multiband_db_cog(tmp_path: Path) -> None:
    hdf_path = _gcov_hdf(tmp_path / "nisar.h5")
    prepared = prepare_nisar_product(
        SelectedNisarProduct(
            product_id="NISAR_TEST_GCOV",
            package_path=hdf_path,
            acquisition_at=datetime(2026, 7, 18, 5, 30, tzinfo=UTC),
            aoi_id="bangalore_60km_geodesic_aoi",
        ),
        Settings(runtime_backend=RuntimeBackend.MEMORY, scratch_dir=tmp_path / "scratch"),
        dry_run=True,
    )

    assert prepared.polarizations == ("HH", "HV")
    assert prepared.manifest["sar:frequency_band"] == "S"
    assert prepared.manifest["calibration_formula"] == "10*log10(gamma0_power)"
    with prepared.backscatter_path.open("rb") as output:
        assert output.read(4) in {b"II+\x00", b"MM\x00+"}
    assert prepared.manifest["outputs"]["backscatter"]["checksum_sha256"] == (
        prepared.checksum_sha256
    )
    assert all(
        count > 0
        for count in prepared.manifest["outputs"]["backscatter"]["valid_pixel_counts"]
    )
    with rasterio.open(prepared.backscatter_path) as dataset:
        assert dataset.count == 2
        assert dataset.descriptions == ("HH", "HV")
        assert dataset.dtypes == ("float32", "float32")
        assert dataset.nodata == NISAR_NODATA
        assert dataset.crs.to_epsg() == 32643
        assert dataset.res == pytest.approx((10.0, 10.0))
        assert dataset.overviews(1)
        assert dataset.read(1)[0, 0] == NISAR_NODATA
        assert dataset.read(1)[0, 1] == NISAR_NODATA
        assert dataset.read(1)[1, 1] == pytest.approx(-10.0)
        assert dataset.read(2)[1, 1] == pytest.approx(-20.0)


def test_prepare_nisar_accepts_direct_hdf5_with_provider_zip_filename(
    tmp_path: Path,
) -> None:
    provider_path = _gcov_hdf(tmp_path / "original.zip")

    prepared = prepare_nisar_product(
        SelectedNisarProduct("NISAR_DIRECT_HDF5", provider_path, None, "aoi"),
        Settings(runtime_backend=RuntimeBackend.MEMORY, scratch_dir=tmp_path / "scratch"),
        dry_run=True,
    )

    assert prepared.polarizations == ("HH", "HV")
    assert prepared.manifest["identification"]["input_backscatter_normalization"] == (
        "gamma0"
    )


def test_prepare_nisar_accepts_declared_single_polarization(tmp_path: Path) -> None:
    hdf_path = _gcov_hdf(tmp_path / "single-pol.h5")
    with h5py.File(hdf_path, "r+") as handle:
        grid = handle["/science/SSAR/GCOV/grids/frequencyA"]
        del grid["listOfPolarizations"]
        del grid["listOfCovarianceTerms"]
        del grid["HVHV"]
        grid.create_dataset("listOfPolarizations", data=np.asarray([b"HH"]))
        grid.create_dataset("listOfCovarianceTerms", data=np.asarray([b"HHHH", b"HHHV"]))

    prepared = prepare_nisar_product(
        SelectedNisarProduct("NISAR_SINGLE_POL", hdf_path, None, "aoi"),
        Settings(runtime_backend=RuntimeBackend.MEMORY, scratch_dir=tmp_path / "scratch"),
        dry_run=True,
    )

    assert prepared.polarizations == ("HH",)
    with rasterio.open(prepared.backscatter_path) as dataset:
        assert dataset.count == 1
        assert dataset.descriptions == ("HH",)


@pytest.mark.parametrize("mutation", ["missing_mask", "mismatched_grid", "invalid_coordinates"])
def test_nisar_discovery_rejects_invalid_native_grid(
    tmp_path: Path,
    mutation: str,
) -> None:
    hdf_path = _gcov_hdf(tmp_path / f"{mutation}.h5")
    with h5py.File(hdf_path, "r+") as handle:
        grid = handle["/science/SSAR/GCOV/grids/frequencyA"]
        if mutation == "missing_mask":
            del grid["mask"]
        elif mutation == "mismatched_grid":
            del grid["HVHV"]
            grid.create_dataset("HVHV", data=np.ones((1023, 1024), dtype="float32"))
        else:
            coordinates = grid["xCoordinates"][:]
            coordinates[100] += 2.0
            del grid["xCoordinates"]
            grid.create_dataset("xCoordinates", data=coordinates)

    with pytest.raises(NisarPrepareError):
        discover_gcov_assets(tmp_path, explicit_hdf=hdf_path)


def test_nisar_discovery_rejects_ambiguous_science_files(tmp_path: Path) -> None:
    _gcov_hdf(tmp_path / "science-a.h5")
    _gcov_hdf(tmp_path / "science-b.h5")

    with pytest.raises(NisarPrepareError, match="exactly one"):
        discover_gcov_assets(tmp_path)


def test_nisar_discovery_rejects_filename_metadata_conflict(tmp_path: Path) -> None:
    hdf_path = _gcov_hdf(tmp_path / "NISAR_LSAR_GCOV_conflict.h5")

    with pytest.raises(NisarPrepareError, match="filename conflicts"):
        discover_gcov_assets(tmp_path, explicit_hdf=hdf_path)


@pytest.mark.parametrize(
    ("radar_band", "rtc", "normalization", "message"),
    [
        ("L", "True", "gamma0", "radar_band"),
        ("S", "False", "gamma0", "terrain correction"),
        ("S", "True", "sigma0", "Gamma0"),
    ],
)
def test_prepare_nisar_fails_closed_for_invalid_science_contract(
    tmp_path: Path,
    radar_band: str,
    rtc: str,
    normalization: str,
    message: str,
) -> None:
    hdf_path = _gcov_hdf(
        tmp_path / f"invalid-{radar_band}-{rtc}-{normalization}.h5",
        radar_band=radar_band,
        rtc=rtc,
        normalization=normalization,
    )

    with pytest.raises(NisarPrepareError, match=message):
        prepare_nisar_product(
            SelectedNisarProduct("NISAR_INVALID", hdf_path, None, "aoi"),
            Settings(runtime_backend=RuntimeBackend.MEMORY, scratch_dir=tmp_path / "scratch"),
            dry_run=True,
        )


def _gcov_hdf(
    path: Path,
    *,
    radar_band: str = "S",
    rtc: str = "True",
    normalization: str = "gamma0",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as handle:
        identification = handle.create_group("/science/SSAR/identification")
        values = {
            "missionId": "NISAR",
            "instrumentName": "SSAR",
            "productLevel": "L2",
            "productType": "GCOV",
            "radarBand": radar_band,
            "granuleId": "NISAR_TEST_GCOV_001",
            "productVersion": "1.0",
            "productSpecificationVersion": "1.2.1",
            "zeroDopplerStartTime": "2026-07-18T05:30:00Z",
            "zeroDopplerEndTime": "2026-07-18T05:30:15Z",
            "orbitPassDirection": "Ascending",
            "lookDirection": "Right",
        }
        for name, value in values.items():
            identification.create_dataset(name, data=np.bytes_(value))
        identification.create_dataset("trackNumber", data=71)
        identification.create_dataset("frameNumber", data=11)
        identification.create_dataset("absoluteOrbitNumber", data=140)

        grid = handle.create_group("/science/SSAR/GCOV/grids/frequencyA")
        grid.create_dataset("listOfPolarizations", data=np.asarray([b"HH", b"HV"]))
        grid.create_dataset("listOfCovarianceTerms", data=np.asarray([b"HHHH", b"HHHV", b"HVHV"]))
        grid.create_dataset("xCoordinates", data=500_005.0 + np.arange(1024) * 10.0)
        grid.create_dataset("yCoordinates", data=1_499_995.0 - np.arange(1024) * 10.0)
        grid.create_dataset("projection", data=32643)
        grid.create_dataset("numberOfSubSwaths", data=2)
        mask = np.ones((1024, 1024), dtype="uint8")
        mask[0, 0] = 0
        mask[0, 1] = 255
        grid.create_dataset("mask", data=mask)
        hh = np.full((1024, 1024), 0.1, dtype="float32")
        hv = np.full((1024, 1024), 0.01, dtype="float32")
        hh_dataset = grid.create_dataset("HHHH", data=hh, chunks=(16, 16))
        hh_dataset.attrs["description"] = "Covariance between HH and HH in Gamma0"
        hh_dataset.attrs["long_name"] = "radar backscatter gamma0"
        grid.create_dataset("HHHV", data=np.ones((1024, 1024), dtype="complex64"))
        hv_dataset = grid.create_dataset("HVHV", data=hv, chunks=(16, 16))
        hv_dataset.attrs["description"] = "Covariance between HV and HV in Gamma0"
        hv_dataset.attrs["long_name"] = "radar backscatter gamma0"

        parameters = handle.create_group(
            "/science/SSAR/GCOV/metadata/processingInformation/parameters"
        )
        parameters.create_dataset("radiometricTerrainCorrectionApplied", data=np.bytes_(rtc))
        parameters.create_dataset("polarimetricSymmetrizationApplied", data=np.bytes_("False"))
        parameters.create_dataset("noiseCorrectionApplied", data=np.bytes_("True"))
        rtc_group = parameters.create_group("rtc")
        rtc_group.create_dataset(
            "outputBackscatterNormalizationConvention", data=np.bytes_(normalization)
        )
        rtc_group.create_dataset(
            "inputBackscatterNormalizationConvention", data=np.bytes_(normalization)
        )
        rtc_group.create_dataset(
            "outputBackscatterExpressionConvention", data=np.bytes_("NRB")
        )
        ceos = handle.create_group(
            "/science/SSAR/GCOV/metadata/ceosAnalysisReadyData"
        )
        ceos.create_dataset(
            "outputBackscatterDecibelConversionFormula",
            data=np.bytes_("10*log10(<GCOV_TERM>)"),
        )
    return path
